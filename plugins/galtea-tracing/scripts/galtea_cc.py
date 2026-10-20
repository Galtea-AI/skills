#!/usr/bin/env python3
"""Log Claude Code conversations to Galtea as production sessions.

Run by the galtea-tracing plugin through scripts/run.sh:
  hook SessionStart -> first session after a config change: check key, product,
                       auto-close, thinking summaries, and run a self-test
  hook UserPromptSubmit -> remember the prompt
  hook Stop         -> the Galtea session at the first turn sent, then
                       POST /inferenceResults (prompt + final assistant reply),
                       then POST /traces/batch with the turn's steps, then the
                       session title to metadata when it changed. A Stop re-fired
                       after another Stop hook blocked updates that turn instead
  hook SessionEnd   -> the session title, then PATCH /sessions/{id}/finish so
                       Monitors score it; with a turn still being sent, no finish,
                       and the platform's inactivity auto-close closes the session
  mute [data_dir]   -> stop logging for the current repository
  unmute [data_dir] -> start logging again

One Claude Code session_id == one Galtea session (customId = session_id).
Stdlib only, Python 3.9+. A hook never blocks or fails Claude Code: it always
exits 0, errors go to <data dir>/hook.log, and the only output is the single
SessionStart systemMessage line.

Steps are parsed from the session transcript JSONL, whose format Anthropic
documents as internal. If parsing breaks, the turn is still logged; only the
spans are lost (and the error is logged).

Config comes from the plugin's install dialog (CLAUDE_PLUGIN_OPTION_API_KEY,
_PRODUCT_ID, _API_URL, _REDACT_SECRETS). GALTEA_CC_API_URL overrides the host
for development. GALTEA_CC_DISABLE=1 skips logging for this process. `claude -p`
runs are not logged.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_API_URL = "https://api.galtea.ai"
AGENT_TOOLS = ("Agent", "Task")
SPAN_BATCH = 100
MAX_SPAN_BYTES = 20000
# the prompt and the reply each; /inferenceResults takes a JSON body of at most 512 KB
MAX_TURN_BYTES = 100000
STATE_MAX_AGE = 7 * 24 * 3600
CREATE_BUDGET = 60  # seconds for the calls that find or create a session
WAIT = 70  # seconds a hook waits on another hook of its session: longer than CREATE_BUDGET
CONTINUE_WAIT = 120  # seconds a re-fired Stop waits for the Stop that sent its turn
UNREACHABLE_RETRY = 600  # seconds before the check runs again against a host that did not answer
# seconds for the check's steps, then for the self-test cleanup; the SessionStart hook times out at 30
CHECK_BUDGET = 20
CLEANUP_BUDGET = 6
MID_SESSION_SOURCES = ("clear", "compact", "fork")
INFLIGHT_MAX = 600  # seconds after which a Stop hook that never cleared its entry counts as killed
PENDING_MAX = 10  # unsent prompts kept per session: an interrupted turn gets no Stop to take its prompt
LOG_MAX_BYTES = 1000000  # hook.log rolls over to hook.log.1 past this size
REDACTED = "[REDACTED]"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


# --- paths, log, config ----------------------------------------------------

def config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def data_dir():
    return os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.join(config_dir(), "galtea-tracing")


def data_path(*parts):
    return os.path.join(data_dir(), *parts)


def scrub(text):
    """The text with every part of the configured API key masked. The last guard before
    the log and stdout, so a key that slipped into an error message never reaches them."""
    key = os.environ.get("CLAUDE_PLUGIN_OPTION_API_KEY") or ""
    for piece in sorted(set([key.strip()] + key.split()), key=len, reverse=True):
        if len(piece) >= 8:
            text = text.replace(piece, REDACTED)
    return text


def log(msg):
    try:
        os.makedirs(data_dir(), exist_ok=True)
        path = data_path("hook.log")
        if os.path.isfile(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8", errors="replace") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), scrub(msg)))
    except Exception:
        pass


def log_exc(prefix):
    log("%s: %s" % (prefix, traceback.format_exc().strip().replace("\n", " | ")))


def load_config():
    """The config, with `error` set to a message for the user when it cannot be used."""
    env = os.environ
    redact = env.get("CLAUDE_PLUGIN_OPTION_REDACT_SECRETS", "").strip().lower()
    cfg = {
        "api_key": env.get("CLAUDE_PLUGIN_OPTION_API_KEY", "").strip(),
        "product_id": env.get("CLAUDE_PLUGIN_OPTION_PRODUCT_ID", "").strip(),
        "api_url": (env.get("GALTEA_CC_API_URL") or env.get("CLAUDE_PLUGIN_OPTION_API_URL") or DEFAULT_API_URL).strip(),
        "redact": redact not in ("false", "0", "no", "off"),
        "max_span_bytes": MAX_SPAN_BYTES,
        "error": None,
    }
    url = urllib.parse.urlsplit(cfg["api_url"])
    if not cfg["api_key"] or not cfg["product_id"]:
        cfg["error"] = "Galtea tracing is installed but has no API key or product id. Set them with /plugin"
    elif not cfg["api_key"].startswith(("gsk_", "gsk-")) or re.search(r"\s", cfg["api_key"]):
        # a space or line break would end up in an HTTP header error, which repeats the key
        cfg["error"] = ("Galtea tracing is off: the API key is not a Galtea API key. It starts with gsk_ "
                        "(gsk- for an older key) and holds no spaces or line breaks. Set it with /plugin")
    elif re.search(r"\s", cfg["product_id"]):
        cfg["error"] = "Galtea tracing is off: the product id holds a space or a line break. Set it with /plugin"
    elif not (url.scheme == "https" or (url.scheme == "http" and url.hostname in LOCAL_HOSTS)) or not url.hostname:
        # the API key travels in every request
        cfg["error"] = ("Galtea tracing is off: the Galtea API URL must start with https:// "
                        "(http:// only for localhost). Set it with /plugin")
    return cfg


def plugin_version():
    """The manifest version, else the installed version: the cache folder is named after it."""
    try:
        with open(os.path.join(PLUGIN_ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
            version = json.load(f).get("version")
    except Exception:
        version = None
    if not version and os.path.basename(os.path.dirname(PLUGIN_ROOT)) == "galtea-tracing":
        version = os.path.basename(PLUGIN_ROOT)
    return version


class ApiError(RuntimeError):
    def __init__(self, method, path, status, body):
        super().__init__("%s %s -> %s %s" % (method, path, status, body[:500]))
        self.status = status


class OutOfTime(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the Authorization header to the host it names."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def api(cfg, method, path, body=None):
    timeout = 30
    if cfg.get("deadline"):
        timeout = cfg["deadline"] - time.time()
        if timeout <= 0:
            raise OutOfTime("%s %s -> out of time" % (method, path))
    req = urllib.request.Request(
        cfg["api_url"].rstrip("/") + path,
        # not ASCII-escaped: an escape would grow the body past the clipped size. A lone
        # surrogate (a JavaScript cut through an emoji) becomes "?" rather than failing the call
        data=json.dumps(body, ensure_ascii=False).encode("utf-8", "replace") if body is not None else None,
        method=method,
        headers={"Authorization": "Bearer " + cfg["api_key"], "Content-Type": "application/json"},
    )
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            forget_check()  # the next session start runs the check and shows the user the problem
        raise ApiError(method, path, e.code, e.read())
    except OSError:  # URLError, a timeout, a reset: their messages never hold the request headers
        raise
    except Exception as e:
        # an error raised while building the request can repeat the Authorization header
        raise RuntimeError("%s %s -> request failed (%s)" % (method, path, type(e).__name__)) from None


def forget_check():
    try:
        os.remove(data_path("checked"))
    except OSError:
        pass


def items(res):
    return res if isinstance(res, list) else (res or {}).get("data") or (res or {}).get("items") or []


# --- state -----------------------------------------------------------------

def lock(fh):
    """Exclusive lock on fh; returns the unlock function. fcntl on POSIX,
    msvcrt on Windows (imported here so a missing module is logged, not fatal)."""
    try:
        import fcntl
    except ImportError:
        import msvcrt
        # LK_LOCK gives up after about 10 s, so retry the non-blocking form up to our own deadline
        deadline = time.time() + WAIT
        while True:
            fh.seek(0)
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.time() >= deadline:
                    raise
                time.sleep(0.05)

        def unlock():
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        return unlock
    fcntl.flock(fh, fcntl.LOCK_EX)
    return lambda: fcntl.flock(fh, fcntl.LOCK_UN)


class State:
    """Per-session state file, locked so concurrent async hooks don't race.
    Hold it only to read and write state, never across an API call."""

    def __init__(self, session_id):
        os.makedirs(data_path("state"), exist_ok=True)
        self.path = data_path("state", re.sub(r"[^\w.-]", "_", session_id) + ".json")

    def __enter__(self):
        self.fh = os.fdopen(os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600), "r+", encoding="utf-8")
        try:
            self.unlock = lock(self.fh)
        except Exception:
            self.fh.close()
            raise
        self.fh.seek(0)
        raw = self.fh.read()
        try:
            data = json.loads(raw) if raw.strip() else {}
        except ValueError:
            data = None
        if not isinstance(data, dict):
            # a hook killed mid-write leaves partial JSON; the session lookup finds the session again
            log("unreadable state for session file %s; starting it again" % os.path.basename(self.path))
            data = {}
        data.setdefault("index", 0)
        self.data = data
        return self.data

    def __exit__(self, *exc):
        try:
            self.fh.seek(0)
            self.fh.truncate()
            json.dump(self.data, self.fh)
            self.fh.flush()
            self.unlock()
        finally:
            self.fh.close()


def prune_state():
    cutoff = time.time() - STATE_MAX_AGE
    try:
        names = os.listdir(data_path("state"))
    except OSError:
        return
    for name in names:
        p = data_path("state", name)
        try:
            if os.path.getmtime(p) < cutoff:
                os.remove(p)
        except OSError:
            pass


# --- redaction -------------------------------------------------------------

_KEYWORD = r"(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY|ACCESS_KEY)"
# The keyword ends a word or a camelCase part: max_tokens and clientSecret are names, tokenizer is not.
_NAME = r"[\w.-]{0,64}?" + _KEYWORD + r"(?:s?(?![a-z])|(?-i:(?<=[a-z])(?=[A-Z])))[\w.-]{0,64}?"
# A value of digits only is a count or a limit (max_tokens=100000), never a secret.
_NOT_A_NUMBER = r"(?!\d+(?:[\s\"'`,;:.)\]}]|$))"
# An unquoted value never ends in punctuation that closes a call, a list or a sentence.
_VALUE = r"([^\s\"'`]{5,}[^\s\"'`),;:.\]}])"
# A quoted value runs to its closing quote, or to the end of the line when the quote is cut off.
_QUOTED = r"([\"'])" + _NOT_A_NUMBER + r"((?:(?!\3)[^\\\n]|\\.){6,512})(?:\3|(?=\n)|$)"
_CODE_REF = re.compile(r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(\(.*)?")


def _assignment(m):
    """NAME=value with the value masked. An unquoted value is kept when it is code: a call,
    or a variable of the same name (password=password, api_key=settings.API_KEY)."""
    name, value = m.group(1), m.group(5)
    ref = _CODE_REF.fullmatch(value) if value else None
    if ref and (ref.group(2) or ref.group(1).split(".")[-1].lower() == name.split(".")[-1].lower()):
        return m.group(0)
    g = 5 if value else 4
    start, end = m.start(g) - m.start(), m.end(g) - m.start()
    return m.group(0)[:start] + REDACTED + m.group(0)[end:]


# (name, pattern, replacement). Each pattern is anchored by a literal prefix or a
# lookbehind so a long run that fails to match costs one scan, not one per offset.
RULES = [
    ("pem_private_key", re.compile(r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY-----"
                                   r"(?:[\s\S]{0,8000}?-----END [A-Z0-9 ]{0,40}PRIVATE KEY-----|[\s\S]*)"), REDACTED),
    ("galtea_key", re.compile(r"gsk[_-][A-Za-z0-9_-]{20,}"), REDACTED),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), REDACTED),
    ("openai_project_key", re.compile(r"sk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,}"), REDACTED),
    ("openai_key", re.compile(r"(?<![\w-])sk-[A-Za-z0-9]{32,}"), REDACTED),
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), REDACTED),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"), REDACTED),
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{60,}"), REDACTED),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), REDACTED),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_-]{35}"), REDACTED),
    ("jwt", re.compile(r"(?<![\w-])eyJ[\w-]{8,}\.eyJ[\w-]{8,}\.[\w-]{8,}"), REDACTED),
    ("auth_header", re.compile(r"(?i)\b(authorization|x-api-key)([\"']?\s*:\s*[\"']?)"
                               r"((?:bearer|basic|token|digest)\s+)?[^\s\"'\\,;]{6,}"), r"\1\2\3" + REDACTED),
    ("bearer", re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{16,}"), r"\1" + REDACTED),
    ("url_password", re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]{0,31}://[^\s:/@\"']{1,256}:)[^\s/@\"']{1,256}(@)"),
     r"\1" + REDACTED + r"\2"),
    ("env_assignment", re.compile(r"(?i)(?<![\w.-])(" + _NAME + r")(\s*=(?!=)\s*)(?:" + _QUOTED + "|"
                                  + _NOT_A_NUMBER + _VALUE + ")"), _assignment),
    # "name": "value" in JSON, 'name': 'value' in a Python dict
    ("json_field", re.compile(r"(?i)(([\"'])" + _NAME + r"\2\s*:\s*([\"']))" + _NOT_A_NUMBER
                              + r"(?:(?!\3)[^\\]|\\.){6,}(\3)"), r"\1" + REDACTED + r"\4"),
    # a line that is only `name: value`, as in YAML; it also masks a type in code, such as `password: string`
    ("yaml_field", re.compile(r"(?im)^([ \t]*(?:-[ \t]+)?" + _NAME + r"[ \t]*:[ \t]+([\"']?))" + _NOT_A_NUMBER
                              + r"[^\s{\[][^\n]{5,511}?(\2[ \t\r]*)$"), r"\1" + REDACTED + r"\3"),
]
_SECRET_KEY = re.compile(r"(?i)" + _NAME)


def _redact(value):
    if isinstance(value, str):
        for _, rx, repl in RULES:
            value = rx.sub(repl, value)
        return value
    if isinstance(value, dict):
        # a secret-named key holds a secret value, like the "name": "value" rule
        return {k: REDACTED if isinstance(v, str) and len(v) >= 6 and not v.isdigit()
                and isinstance(k, str) and _SECRET_KEY.fullmatch(k) else _redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


def redact(value):
    """Best-effort secret masking. A failure never lets the raw text through."""
    try:
        return _redact(value)
    except Exception:
        log_exc("redaction failed")
        return "[REDACTION FAILED]"


def clean(cfg, value, limit=None):
    """Redact (when on), then clip: the cut never splits a secret out of reach."""
    if cfg.get("redact", True):
        value = redact(value)
    return clip(value, limit) if limit else value


# --- transcript -> spans ---------------------------------------------------

def read_jsonl(path, start=0):
    """The JSON lines of the file from byte offset `start`, each with its own offset in `_offset`.
    A torn line, such as one Claude Code is still writing, is skipped."""
    out = []
    try:
        with open(path, "rb") as f:
            f.seek(start)
            offset = start
            for raw in f:
                try:
                    d = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    d = None
                if isinstance(d, dict):
                    d["_offset"] = offset
                    out.append(d)
                offset += len(raw)
    except FileNotFoundError:
        pass
    return out


def is_prompt_line(d):
    # isMeta lines are written by Claude Code, such as the feedback of a blocking Stop hook
    if d.get("type") != "user" or d.get("isSidechain") or d.get("isMeta"):
        return False
    c = (d.get("message") or {}).get("content")
    if isinstance(c, str):
        return True
    return isinstance(c, list) and not any(b.get("type") == "tool_result" for b in c)


def has_prompt(lines, prompt_id):
    return any(d.get("promptId") == prompt_id and is_prompt_line(d) for d in lines)


def prompt_text(d):
    c = (d.get("message") or {}).get("content")
    return c if isinstance(c, str) else "\n\n".join(b.get("text") or "" for b in c if b.get("type") == "text")


def turn_lines(lines, prompt_id):
    """Main-thread lines from this turn's prompt up to the next turn's prompt."""
    start = None
    for i, d in enumerate(lines):
        if prompt_id and d.get("promptId") == prompt_id and is_prompt_line(d):
            start = i
            break
    if start is None:  # fallback: last real user prompt
        for i in range(len(lines) - 1, -1, -1):
            if is_prompt_line(lines[i]):
                start = i
                break
    if start is None:
        return []
    own = lines[start].get("promptId")
    out = [lines[start]]
    for d in lines[start + 1:]:
        if d.get("isSidechain"):
            continue
        # a prompt line with no promptId is kept: only a known other prompt ends the turn
        if d.get("promptId") and d.get("promptId") != own and is_prompt_line(d):
            break
        out.append(d)
    return out


def clip(value, limit):
    """Keep payloads under the per-span byte budget."""
    if value is None:
        return None
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    b = s.encode("utf-8", "replace")
    if len(b) <= limit:
        return value
    return b[:limit].decode(errors="ignore") + "\n…[truncated %d bytes]" % (len(b) - limit)


def result_text(content):
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if b.get("type") == "text":
            parts.append(b.get("text", ""))
        else:
            parts.append("[%s]" % b.get("type"))
    return "\n".join(parts)


def ms_between(a, b):
    try:
        from datetime import datetime
        fa = datetime.fromisoformat(a.replace("Z", "+00:00"))
        fb = datetime.fromisoformat(b.replace("Z", "+00:00"))
        return max(0, int((fb - fa).total_seconds() * 1000))
    except Exception:
        return None


def span_id(ir_id, key):
    """The same id each time a span is built, so a span sent again would clash, never duplicate."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "galtea-cc:%s:%s" % (ir_id, key)))


def build_spans(lines, ir_id, cfg, parent_id, subagent_dir, totals, sent=(), depth=0, nested=None):
    """Turn transcript lines into Galtea spans. Model calls become GENERATION
    spans (grouped by API message id), tool calls TOOL spans, subagents AGENT
    spans whose own transcript is nested underneath. Each span carries its `_key`;
    the keys in `sent` are left out. A tool with no result yet is left out too, so a
    later Stop of the same turn sends it with its output. `totals` counts every model
    call of the lines, sent or not. A subagent transcript is nested once: a resumed
    subagent keeps one file, so nesting it again would repeat its span ids."""
    limit = cfg["max_span_bytes"]
    nested = set() if nested is None else nested
    spans, gens, tools = [], {}, {}
    prev_ts = lines[0].get("timestamp") if lines else None

    for d in lines:
        ts = d.get("timestamp")
        msg = d.get("message") or {}
        if d.get("type") == "assistant":
            mid = msg.get("id") or d.get("uuid")
            g = gens.get(mid)
            if g is None:
                g = {"id": span_id(ir_id, "gen:%s" % mid), "start": prev_ts, "thinking": [], "text": [],
                     "tool_calls": [], "thinking_omitted": 0, "model": msg.get("model")}
                gens[mid] = g
                spans.append(("gen", mid))
            g["end"] = ts
            g["usage"] = msg.get("usage") or g.get("usage")
            for b in msg.get("content") or []:
                t = b.get("type")
                if t == "thinking":
                    if (b.get("thinking") or "").strip():
                        g["thinking"].append(b["thinking"])
                    else:
                        g["thinking_omitted"] += 1
                elif t == "redacted_thinking":
                    g["thinking_omitted"] += 1
                elif t == "text":
                    g["text"].append(b.get("text", ""))
                elif t == "tool_use":
                    g["tool_calls"].append(b.get("name"))
                    tools[b["id"]] = {"id": span_id(ir_id, "tool:%s" % b["id"]), "name": b.get("name"),
                                      "input": b.get("input"), "start": ts}
                    spans.append(("tool", b["id"]))
        elif d.get("type") == "user":
            for b in msg.get("content") if isinstance(msg.get("content"), list) else []:
                if b.get("type") != "tool_result" or b.get("tool_use_id") not in tools:
                    continue
                t = tools[b["tool_use_id"]]
                t["done"], t["end"] = True, ts
                t["output"] = result_text(b.get("content"))
                t["is_error"] = bool(b.get("is_error"))
                r = d.get("toolUseResult")
                if isinstance(r, dict) and r.get("agentId"):
                    t["agent_id"] = r["agentId"]
        if ts:
            prev_ts = ts

    out = []
    for kind, key in spans:
        if kind == "gen":
            g = gens[key]
            u = g.get("usage") or {}
            for k_src, k_dst in (("input_tokens", "in"), ("output_tokens", "out"),
                                 ("cache_read_input_tokens", "cache")):
                totals[k_dst] = totals.get(k_dst, 0) + (u.get(k_src) or 0)
            if "gen:%s" % key in sent:
                continue
            out.append({
                "_key": "gen:%s" % key, "id": g["id"], "inferenceResultId": ir_id, "parentTraceId": parent_id,
                "name": "llm:%s" % (g["model"] or "claude"), "type": "GENERATION",
                "outputData": {
                    "thinking": clean(cfg, "\n\n".join(g["thinking"]), limit) or None,
                    "text": clean(cfg, "\n\n".join(g["text"]), limit) or None,
                    "tool_calls": g["tool_calls"] or None,
                },
                # raw usage keeps cache_creation_input_tokens, which inferenceResults has no field for
                "metadata": {"model": g["model"], "usage": u,
                             "thinking_blocks_omitted": g["thinking_omitted"]},
                "startTime": g["start"], "endTime": g.get("end"),
                "latencyMs": ms_between(g["start"], g.get("end")) if g["start"] and g.get("end") else None,
            })
        else:
            t = tools[key]
            if not t.get("done"):
                continue
            is_agent = t["name"] in AGENT_TOOLS
            if "tool:%s" % key not in sent:
                out.append({
                    "_key": "tool:%s" % key, "id": t["id"], "inferenceResultId": ir_id, "parentTraceId": parent_id,
                    "name": "%s:%s" % ("agent" if is_agent else "tool", t["name"]),
                    "type": "AGENT" if is_agent else "TOOL",
                    "inputData": {"input": clean(cfg, t["input"], limit)},
                    "outputData": clean(cfg, t.get("output"), limit),
                    "error": clean(cfg, t.get("output"), 2000) if t.get("is_error") else None,
                    "startTime": t["start"], "endTime": t["end"],
                    "latencyMs": ms_between(t["start"], t["end"]) if t["end"] else None,
                })
            if is_agent and t.get("agent_id") and t["agent_id"] not in nested and depth < 3:
                nested.add(t["agent_id"])
                sub_path = os.path.join(subagent_dir, "agent-%s.jsonl" % t["agent_id"])
                if not os.path.isfile(sub_path):
                    log("subagent transcript %s missing; its spans are lost" % os.path.basename(sub_path))
                sub = [d for d in read_jsonl(sub_path) if d.get("type") in ("user", "assistant")]
                out.extend(build_spans(sub, ir_id, cfg, t["id"], subagent_dir, totals, sent, depth + 1, nested))
    # Galtea rejects null parents only if referenced; drop empty keys for clean payloads
    return [{k: v for k, v in s.items() if v is not None} for s in out]


def reply_end(lines, reply):
    """The lines up to the one that holds the turn's final reply. A blocking Stop hook makes
    Claude continue the turn, so the lines after the reply can be work that a later Stop sends."""
    want = (reply or "").strip()
    for i in range(len(lines) - 1, -1, -1) if want else ():
        d = lines[i]
        if d.get("type") == "assistant":
            blocks = (d.get("message") or {}).get("content") or []
            texts = [b.get("text") or "" for b in blocks if b.get("type") == "text"]
            if texts and "\n\n".join(texts).strip() == want:
                return lines[:i + 1]
    return lines


def log_spans(cfg, hook, ir_id, sent, wait=1.5, start=0):
    """Post the turn's spans that are not in `sent`, adding each stored batch's keys to it.
    The transcript is read from byte offset `start`, which is at or before this turn's prompt.
    Returns (token totals of the turn, offset of the turn's prompt line), or None when the
    turn has no lines. Each case that loses the turn's spans writes one log line, never
    transcript content."""
    key, pid, path = hook["session_id"], hook.get("prompt_id"), hook.get("transcript_path")
    if not path:
        log("no transcript path for session %s; turn %s has no spans" % (key, ir_id))
        return None
    time.sleep(wait)  # let Claude Code flush the final transcript lines
    if not os.path.isfile(path):
        log("transcript file missing for session %s; turn %s has no spans" % (key, ir_id))
        return None
    if start > os.path.getsize(path):
        start = 0
    all_lines = read_jsonl(path, start)
    if pid and start and not has_prompt(all_lines, pid):
        all_lines = read_jsonl(path)
    if pid and not has_prompt(all_lines, pid):
        log("prompt %s is not in the transcript of session %s; using its last prompt" % (pid, key))
    lines = turn_lines(all_lines, pid)
    if not lines:
        log("no transcript lines match turn %s of session %s; it has no spans" % (ir_id, key))
        return None
    subagent_dir = os.path.join(os.path.dirname(path), key, "subagents")
    totals = {}
    spans = build_spans(reply_end(lines, hook.get("last_assistant_message")), ir_id, cfg, None,
                        subagent_dir, totals, set(sent))
    for i in range(0, len(spans), SPAN_BATCH):
        batch = spans[i:i + SPAN_BATCH]
        res = api(cfg, "POST", "/traces/batch?includeRejected=true",
                  {"traces": [{k: v for k, v in s.items() if k != "_key"} for s in batch]})
        sent.extend(s["_key"] for s in batch)  # a rejected span is not sent again: it would be rejected again
        rej = (res.get("partialSuccess") or {}).get("errors") if isinstance(res, dict) else None
        if rej:
            log("rejected spans for %s: %s" % (ir_id, json.dumps(rej)[:1000]))
    return totals, lines[0]["_offset"]


# --- git and mute ----------------------------------------------------------

def git(cwd, *args):
    try:
        r = subprocess.run(["git", "-C", cwd] + list(args), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=2)
    except Exception:
        return None
    out = r.stdout.strip()
    return out if r.returncode == 0 and out else None


def strip_userinfo(url):
    """The remote URL without its user name and password, also in the scp form user@host:path."""
    url = re.sub(r"^([A-Za-z][A-Za-z0-9+.-]*://)[^/@]*@", r"\1", url)
    return re.sub(r"^[^@/:]+@(?=[^@/:]+:)", "", url)


def git_meta(cwd):
    if not cwd or not os.path.isdir(cwd):
        return {}
    meta = {}
    remote = git(cwd, "remote", "get-url", "origin")
    if remote:
        meta["git_repo"] = strip_userinfo(remote)
    branch = git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if branch:
        meta["git_branch"] = branch
    commit = git(cwd, "rev-parse", "HEAD")
    if commit:
        meta["git_commit"] = commit
    return meta


def norm(path):
    return os.path.normcase(os.path.normpath(os.path.realpath(path)))


def repo_root(cwd):
    return norm(git(cwd, "rev-parse", "--show-toplevel") or cwd)


def muted_roots():
    """The muted folders; None when muted.json exists but cannot be read."""
    try:
        with open(data_path("muted.json"), encoding="utf-8") as f:
            roots = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        roots = None
    if not isinstance(roots, list):
        log("muted.json cannot be read; every repository counts as muted until it is fixed")
        return None
    return roots


def contains(root, path):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def is_muted(cwd):
    """Fails closed: an unreadable mute list mutes everything, since mute keeps data private."""
    if not cwd:
        return False
    roots = muted_roots()
    return roots is None or any(contains(r, norm(cwd)) for r in roots)


def set_muted(cwd, muted):
    """The muted or unmuted root; None when the mute list cannot be read, which is left as is."""
    root = repo_root(cwd)
    current = muted_roots()
    if current is None:
        return None
    roots = [r for r in current if r != root]
    if muted:
        roots.append(root)
    os.makedirs(data_dir(), exist_ok=True)
    # replaced in one step, so a hook reading it never sees half a list
    fd, tmp = tempfile.mkstemp(dir=data_dir(), prefix="muted.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(sorted(roots), f, indent=2)
        os.replace(tmp, data_path("muted.json"))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return root


# --- hook handlers ---------------------------------------------------------

def session_body(cfg, custom_id, cwd, production=True):
    meta = {"source": "claude-code", "cwd": cwd, "user": os.environ.get("USER") or os.environ.get("USERNAME"),
            "plugin_version": plugin_version()}
    if cwd and os.path.isdir(cwd):
        meta["user_email"] = git(cwd, "config", "user.email")
    meta.update(git_meta(cwd))
    return {"customId": custom_id, "isProduction": production, "productId": cfg["product_id"],
            "metadata": {k: v for k, v in meta.items() if v is not None}}


def ensure_session(cfg, hook):
    """The Galtea session id, created once per product. While another hook of the session
    is creating it (the `creating` claim), wait for its result instead of creating a second.
    When the configured product is not the one the session was made in, the conversation
    starts again in the new product."""
    key = hook["session_id"]
    deadline = time.time() + WAIT
    while True:
        with State(key) as st:
            if st.get("galtea_session_id"):
                if st.get("product_id") == cfg["product_id"]:
                    return st["galtea_session_id"]
                reset_session_state(st)
                log("session %s moves to product %s" % (key, cfg["product_id"]))
            now = time.time()
            if now - st.get("creating", 0) > WAIT or now > deadline:
                st["creating"] = now
                break
        time.sleep(0.1)
    try:
        sid, next_index, meta = create_session(dict(cfg, deadline=time.time() + CREATE_BUDGET), hook)
    except Exception:
        with State(key) as st:
            st.pop("creating", None)
        raise
    with State(key) as st:
        st.pop("creating", None)
        st["galtea_session_id"], st["product_id"] = sid, cfg["product_id"]
        st["index"] = max(st["index"], next_index)
        if meta is not None:
            st["metadata"] = meta
    return sid


def create_session(cfg, hook):
    """The session with this customId in the product, else a new one from POST /sessions.
    A found session is a resumed one whose state file is gone; it continues after its last
    turn. The lookup comes first because customId is unique per version only: once the
    product has a newer version, POST /sessions succeeds and would split the conversation.
    Returns (session id, next turn index, metadata or None when unknown)."""
    key = hook["session_id"]
    try:
        found = find_sessions(cfg, key)
    except Exception:
        if not is_first_turn(hook):
            # creating it now could split the conversation; the next turn looks it up again
            log_exc("session lookup failed for %s; this turn is not sent" % key)
            raise
        log_exc("session lookup failed for %s; it has no earlier turn, so it is created" % key)
        found = []
    if not found:
        body = session_body(cfg, key, hook.get("cwd"))
        return api(cfg, "POST", "/sessions", body)["id"], 0, body["metadata"]
    sid = max(found, key=lambda x: x.get("createdAt") or "")["id"]
    q = urllib.parse.quote(sid, safe="")
    try:
        newest = items(api(cfg, "GET", "/inferenceResults?sessionIds=%s&sort=index&sort=desc&limit=1" % q))
    except ApiError:  # a platform that cannot sort by index
        newest = None
    if newest and isinstance(newest[0].get("index"), int):
        next_index = newest[0]["index"] + 1
    elif newest == []:
        next_index = 0
    else:
        turns = items(api(cfg, "GET", "/inferenceResults?sessionIds=%s" % q))
        next_index = max([len(turns)] + [t["index"] + 1 for t in turns if isinstance(t.get("index"), int)])
    log("session %s already exists in Galtea; continuing it at turn %d" % (sid, next_index))
    return sid, next_index, None


def is_first_turn(hook):
    """True when the transcript holds no prompt before this turn's prompt."""
    path = hook.get("transcript_path")
    prompts = [d for d in read_jsonl(path) if is_prompt_line(d)] if path else []
    pid = hook.get("prompt_id")
    own = [i for i, d in enumerate(prompts) if pid and d.get("promptId") == pid]
    return bool(prompts) and (own[0] if own else len(prompts) - 1) == 0


def find_sessions(cfg, custom_id):
    q = urllib.parse.quote
    res = api(cfg, "GET", "/sessions?customIds=%s&productIds=%s" % (q(custom_id), q(cfg["product_id"])))
    return [s for s in items(res) if isinstance(s, dict) and s.get("customId") == custom_id]


def post_turn(cfg, sid, index, pending, hook, stop_ts=None):
    return api(cfg, "POST", "/inferenceResults", {
        "sessionId": sid,
        "index": index,
        "status": "GENERATED",
        "input": {"user_message": clean(cfg, pending["prompt"], MAX_TURN_BYTES)},
        "actualOutput": clean(cfg, hook.get("last_assistant_message") or "", MAX_TURN_BYTES),
        # from the prompt to the Stop, without the time this hook spends on the API
        "latency": int(((stop_ts or time.time()) - pending["ts"]) * 1000),
    })


def post_spans(cfg, hook, ir_id, wait=1.5, sent=(), start=0):
    """Spans and token counts for one turn. Returns (keys of the turn's stored spans, offset
    of the turn's prompt line or None). A failure is logged and keeps the keys of the
    batches stored before it, so no span is sent twice; the turn stays."""
    sent = list(sent)
    try:
        res = log_spans(cfg, hook, ir_id, sent, wait, start)
    except Exception:
        log_exc("spans failed for %s" % ir_id)
        return sent, None
    if not res:
        return sent, None
    totals, offset = res
    if totals:
        try:
            # no `tokens`: the API derives it, and has no cache-creation field to add
            api(cfg, "PATCH", "/inferenceResults/%s" % ir_id, {
                "inputTokens": totals["in"], "outputTokens": totals["out"], "cacheReadInputTokens": totals["cache"],
            })
        except Exception:
            log_exc("token counts not sent for %s" % ir_id)
    return sent, offset


def on_prompt(cfg, hook):
    if is_muted(hook.get("cwd")):
        return
    pid = hook.get("prompt_id")
    with State(hook["session_id"]) as st:
        # one entry per prompt: a queued prompt's hook can run before the previous turn's Stop
        prompts = [p for p in st.get("prompts", []) if pid is None or p.get("prompt_id") != pid]
        prompts.append({"prompt": hook.get("prompt", ""), "ts": time.time(), "prompt_id": pid})
        dropped = len(prompts) - PENDING_MAX
        st["prompts"] = prompts[-PENDING_MAX:]
    if dropped > 0:
        log("session %s dropped its oldest unsent prompt: more than %d prompts got no Stop"
            % (hook["session_id"], PENDING_MAX))


def take_prompt(st, prompt_id, asked=None, exact=False):
    """Remove and return this Stop's prompt: the one with its prompt_id, else (unless `exact`)
    the oldest prompt stored without an id. A Stop with no prompt_id comes from a Claude Code
    that sends none: it takes the newest prompt whose text is `asked`, the turn's prompt in the
    transcript, else the newest prompt. The prompts stored before the one taken got no Stop,
    as a turn interrupted with Esc gets none, so they are dropped."""
    prompts = st.get("prompts", [])
    ids = [p.get("prompt_id") for p in prompts]
    if prompt_id is not None:
        i = ids.index(prompt_id) if prompt_id in ids else (ids.index(None) if None in ids and not exact else None)
        return None if i is None else prompts.pop(i)
    if not prompts:
        return None
    match = [j for j, p in enumerate(prompts) if asked is not None and p.get("prompt") == asked]
    i = match[-1] if match else len(prompts) - 1
    if i:
        log("a Stop dropped %d earlier prompts that got no Stop" % i)
    st["prompts"] = prompts[i + 1:]
    return prompts[i]


def last_prompt_text(path):
    prompts = [d for d in read_jsonl(path) if is_prompt_line(d)] if path else []
    return prompt_text(prompts[-1]) if prompts else None


def on_stop(cfg, hook):
    key = hook["session_id"]
    stop_ts = time.time()
    muted = is_muted(hook.get("cwd"))
    token = uuid.uuid4().hex
    pid = hook.get("prompt_id")
    # a Stop re-fired by a blocking Stop hook continues its turn. It takes a prompt only by its own
    # id, which is still stored when it runs before the Stop that sends the turn
    refire = bool(hook.get("stop_hook_active"))
    asked = None if pid or refire else last_prompt_text(hook.get("transcript_path"))
    with State(key) as st:
        pending = None if refire and not pid else take_prompt(st, pid, asked, exact=refire)
        if (pending or refire) and not muted:
            st.setdefault("inflight", {})[token] = time.time()  # SessionEnd leaves the session open
    if muted:
        return
    if not pending and not refire:
        log("Stop of session %s has no stored prompt; its turn is not sent" % key)
        return
    try:
        if pending:
            send_turn(cfg, hook, pending, stop_ts)
        else:
            continue_turn(cfg, hook, token)
    finally:
        with State(key) as st:
            st.get("inflight", {}).pop(token, None)


def claim_turn(cfg, hook):
    """(session id, turn index) for one turn. The id and the index are read under one
    lock, so a session deleted by drop_empty_session is never given a turn."""
    key = hook["session_id"]
    while True:
        ensure_session(cfg, hook)
        with State(key) as st:
            if st.get("galtea_session_id"):
                index = st["index"]
                st["index"] += 1
                return st["galtea_session_id"], index


def drop_empty_session(cfg, key, sid):
    """Delete the session when no turn holds an index in it: it has no turns, so
    auto-close would never close it. The next turn creates the session again."""
    with State(key) as st:
        if st.get("galtea_session_id") != sid or st["index"] != 0:
            return
        reset_session_state(st)
    try:
        api(cfg, "DELETE", "/sessions/%s" % sid)
    except Exception:
        log_exc("could not delete empty session %s" % sid)


def send_turn(cfg, hook, pending, stop_ts=None):
    key = hook["session_id"]
    for attempt in (1, 2):
        index = sid = None
        try:
            sid, index = claim_turn(cfg, hook)
            ir = post_turn(cfg, sid, index, pending, hook, stop_ts)
            break
        except Exception as e:
            # only a 4xx proves the server stored nothing; after a timeout or a 5xx it may have
            # stored the turn, and a gap in the indexes is harmless where a duplicate is not
            refused = index is not None and isinstance(e, ApiError) and 400 <= e.status < 500
            if refused and e.status == 404 and attempt == 1:
                forget_session(key, sid)
                continue
            log_exc("turn not sent for session %s" % key)
            if refused:
                with State(key) as st:
                    if st["index"] == index + 1:  # no later turn took the next index
                        st["index"] = index
                drop_empty_session(cfg, key, sid)
            return
    with State(key) as st:
        st.pop("finished", None)  # a resumed session was reopened by this turn
        start = st.get("scan_from", 0)
    hook["prompt_id"] = hook.get("prompt_id") or pending.get("prompt_id")
    sent, offset = post_spans(cfg, hook, ir["id"], start=start)
    with State(key) as st:
        st["last_turn"] = {"prompt_id": hook["prompt_id"], "ir_id": ir["id"], "sent": sent}
        if offset is not None:
            st["scan_from"] = max(st.get("scan_from", 0), offset)
    sync_title(cfg, hook)


def reset_session_state(st):
    """Forget the Galtea session; the next turn finds or creates it, from turn 0."""
    for k in ("galtea_session_id", "metadata", "title_sent", "finished", "last_turn"):
        st.pop(k, None)
    st["index"] = 0


def forget_session(key, sid):
    """The session was deleted in Galtea: the next claim creates it again, from turn 0."""
    log("session %s is gone from Galtea; creating it again" % sid)
    with State(key) as st:
        if st.get("galtea_session_id") == sid:
            reset_session_state(st)


def continue_turn(cfg, hook, token):
    """A blocking Stop hook made Claude continue the last turn. Its final reply replaces
    the turn's output, and the turn's spans not sent yet are added. Waits for the Stop
    that sends the turn, which can still be running."""
    key = hook["session_id"]
    deadline = time.time() + CONTINUE_WAIT
    while True:
        with State(key) as st:
            now = time.time()
            busy = any(t != token and now - ts < INFLIGHT_MAX for t, ts in st.get("inflight", {}).items())
            last, start = st.get("last_turn"), st.get("scan_from", 0)
        if not busy or now > deadline:
            break
        time.sleep(0.2)
    pid = hook.get("prompt_id")
    if not last or (pid and last.get("prompt_id") and pid != last["prompt_id"]):
        log("Stop of session %s continued a turn that was not sent; its steps are not sent" % key)
        return
    hook = dict(hook, prompt_id=pid or last.get("prompt_id"))
    try:
        api(cfg, "PATCH", "/inferenceResults/%s" % last["ir_id"],
            {"actualOutput": clean(cfg, hook.get("last_assistant_message") or "", MAX_TURN_BYTES)})
    except Exception:
        log_exc("reply not updated for %s" % last["ir_id"])
    sent, _ = post_spans(cfg, hook, last["ir_id"], sent=last.get("sent") or (), start=start)
    with State(key) as st:
        if (st.get("last_turn") or {}).get("ir_id") == last["ir_id"]:
            st["last_turn"]["sent"] = sent


def read_title(path):
    """(title, "custom" | "ai") from the transcript: the newest /rename title, else the
    newest title Claude Code generated. None when the transcript has neither."""
    newest = {}
    if not path:
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"custom-title"' not in line and '"ai-title"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                for kind, field, source in (("custom-title", "customTitle", "custom"), ("ai-title", "aiTitle", "ai")):
                    if d.get("type") == kind and isinstance(d.get(field), str) and d[field].strip():
                        newest[source] = d[field].strip()
    except FileNotFoundError:
        return None
    for source in ("custom", "ai"):
        if source in newest:
            return newest[source], source
    return None


def sync_title(cfg, hook):
    """Send the session title when it changed. PATCH /sessions/{id} replaces `metadata`
    as a whole, so the body carries every key the session already has.
    A failure is logged and never blocks the turn."""
    key = hook["session_id"]
    try:
        found = read_title(hook.get("transcript_path"))
        if not found:
            return
        sent = [clean(cfg, found[0]), found[1]]
        with State(key) as st:
            sid, meta, last = st.get("galtea_session_id"), st.get("metadata"), st.get("title_sent")
        if not sid or last == sent:
            return
        if meta is None:  # the session was found by its customId, so the state holds no copy of its metadata
            meta = api(cfg, "GET", "/sessions/%s" % sid).get("metadata")
            meta = meta if isinstance(meta, dict) else {}
        meta = dict(meta, title=sent[0], title_source=sent[1])
        api(cfg, "PATCH", "/sessions/%s" % sid, {"metadata": meta})
        with State(key) as st:
            st["metadata"], st["title_sent"] = meta, sent
    except Exception:
        log_exc("title not sent for session %s" % key)


def on_end(cfg, hook):
    """Finish the session, unless a Stop hook is still sending a turn: finishing then would
    let monitors score the session before that turn's traces arrive. The platform's
    inactivity auto-close closes it once they have."""
    key = hook["session_id"]
    if not is_muted(hook.get("cwd")):
        sync_title(cfg, hook)
    with State(key) as st:
        now = time.time()
        busy = any(now - ts < INFLIGHT_MAX for ts in st.get("inflight", {}).values())
        sid = st.get("galtea_session_id")
        skip = not sid or st["index"] == 0 or st.get("finished") or busy
        if not skip:
            st["finished"] = True
    if busy:
        log("session %s is still sending a turn at its end; auto-close will close it" % key)
    if skip:
        return
    try:
        api(cfg, "PATCH", "/sessions/%s/finish" % sid,
            {"stoppingReason": "claude_code:%s" % hook.get("reason", "other")})
    except Exception:
        with State(key) as st:
            st.pop("finished", None)
        raise


def fingerprint(cfg):
    raw = "\n".join((cfg["api_key"], cfg["product_id"], cfg["api_url"].rstrip("/")))
    return hashlib.sha256(raw.encode()).hexdigest()


def settings_files(cwd):
    """The user settings file first, then the project ones that can also turn a setting on."""
    files = [os.path.join(config_dir(), "settings.json")]
    if cwd:
        files += [os.path.join(cwd, ".claude", n) for n in ("settings.json", "settings.local.json")]
    return files


def thinking_summaries_on(cwd):
    for path in settings_files(cwd):
        try:
            with open(path, encoding="utf-8") as f:
                if json.load(f).get("showThinkingSummaries") is True:
                    return True
        except Exception:
            pass
    return False


SELFTEST_PROMPT = "selftest: how many files?"
SELFTEST_REPLY = "There are 42 files."


def selftest_transcript(path):
    ts = lambda s: "2026-01-01T00:00:%02d.000Z" % s
    lines = [
        {"type": "user", "promptId": "p1", "timestamp": ts(0),
         "message": {"role": "user", "content": SELFTEST_PROMPT}},
        {"type": "assistant", "timestamp": ts(2), "message": {"id": "m1", "model": "selftest", "content": [
            {"type": "thinking", "thinking": "I should run ls."},
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls | wc -l"}}],
            "usage": {"input_tokens": 10, "output_tokens": 5}}},
        {"type": "user", "promptId": "p1", "timestamp": ts(3),
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "42"}]}},
        {"type": "assistant", "timestamp": ts(4), "message": {"id": "m2", "model": "selftest", "content": [
            {"type": "text", "text": SELFTEST_REPLY}], "usage": {"input_tokens": 5, "output_tokens": 5}}},
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(x) for x in lines) + "\n")


def self_test(cfg):
    """A development session with one synthetic turn, checked and then deleted.
    Never finished, so no monitor evaluates it. Returns (ok, detail, unreachable), with
    unreachable set when Galtea did not answer in time; a session left behind fails the test."""
    tdir = tempfile.mkdtemp(prefix="galtea-cc-selftest-")
    custom_id = "galtea-cc-selftest-%d-%s" % (int(time.time()), uuid.uuid4().hex[:8])
    tpath = os.path.join(tdir, custom_id + ".jsonl")
    sid, create_sent, unreachable = None, False, False
    q = urllib.parse.quote
    try:
        selftest_transcript(tpath)
        create_sent = True
        sid = api(cfg, "POST", "/sessions", session_body(cfg, custom_id, tdir, production=False))["id"]
        hook = {"session_id": custom_id, "transcript_path": tpath, "cwd": tdir, "prompt_id": "p1",
                "last_assistant_message": SELFTEST_REPLY}
        ir = post_turn(cfg, sid, 0, {"prompt": SELFTEST_PROMPT, "ts": time.time()}, hook)
        post_spans(cfg, hook, ir["id"], wait=0)
        irs = items(api(cfg, "GET", "/inferenceResults?sessionIds=%s" % q(sid, safe="")))
        traces = items(api(cfg, "GET", "/traces?inferenceResultIds=%s" % q(ir["id"], safe="")))
        ok, detail = len(irs) == 1 and len(traces) >= 3, "turns=%d traces=%d" % (len(irs), len(traces))
    except Exception as e:
        log_exc("check: selftest failed")
        ok, detail, unreachable = False, str(e)[:200], isinstance(e, (OSError, OutOfTime))
    finally:
        shutil.rmtree(tdir, ignore_errors=True)
    if not delete_selftest_session(cfg, custom_id, sid, create_sent):
        return False, "%s; it could not clean up its development session %s, so delete it in Galtea" % (
            detail, sid or custom_id), unreachable
    return ok, detail, unreachable


def delete_selftest_session(cfg, custom_id, sid, create_sent):
    """True when no self-test session is left. The calls share the time left before
    cfg["cleanup_deadline"], so the check ends before the hook is killed."""
    cfg = dict(cfg, deadline=cfg.get("cleanup_deadline") or time.time() + CLEANUP_BUDGET)
    sids = [sid] if sid else []
    if create_sent and not sid:
        # the server can create the session and answer after our timeout
        try:
            sids = [x["id"] for x in find_sessions(cfg, custom_id)]
        except Exception:
            log_exc("selftest could not look up development session %s" % custom_id)
            return False
    cleaned = True
    for s in sids:
        try:
            api(cfg, "DELETE", "/sessions/%s" % urllib.parse.quote(s, safe=""))
        except Exception:
            log_exc("selftest could not delete development session %s" % s)
            cleaned = False
    return cleaned


def run_check(cfg, cwd=None):
    """Returns (ok, message, unreachable) for the SessionStart systemMessage."""
    start = time.time()
    cfg = dict(cfg, deadline=start + CHECK_BUDGET, cleanup_deadline=start + CHECK_BUDGET + CLEANUP_BUDGET)
    try:
        product = api(cfg, "GET", "/products/%s" % urllib.parse.quote(cfg["product_id"], safe=""))
    except Exception as e:
        log_exc("check: product lookup failed")
        if isinstance(e, ApiError) and e.status in (401, 403, 404):
            return False, ("Galtea tracing is not working: the API key or product id was refused (%s). "
                           "Fix them with /plugin, then start a new session." % str(e)[:200]), False
        if isinstance(e, ApiError):
            return False, ("Galtea tracing got an error from Galtea at %s (%s). The check runs again when "
                           "you next start or resume a session." % (cfg["api_url"], str(e)[:200])), False
        return False, ("Galtea tracing could not reach Galtea at %s (%s). Sessions are not sent while it "
                       "is unreachable. The check runs again at a session start after %d minutes."
                       % (cfg["api_url"], str(e)[:200], UNREACHABLE_RETRY // 60)), True
    notes = []
    scope = product.get("autoCloseScope")
    if scope in ("NONE", "DEVELOPMENT"):
        notes.append("This product does not auto-close production sessions (autoCloseScope=%s). "
                     "A session you leave without /exit stays open and is never evaluated. "
                     "Set auto-close to ALL or PRODUCTION in the product settings." % scope)
    policy = product.get("closedSessionInferenceCreationPolicy")
    if policy in ("IGNORE", "REJECT"):
        verb = {"IGNORE": "drops", "REJECT": "refuses"}[policy]
        notes.append("This product %s new turns on a closed session (closedSessionInferenceCreationPolicy=%s). "
                     "A conversation you continue after its session closed is not logged. Set the policy to "
                     "REOPEN_AND_REEVALUATE in the product settings." % (verb, policy))
    if not thinking_summaries_on(cwd):
        notes.append('Thinking is not logged. To log it, add "showThinkingSummaries": true to %s.'
                     % settings_files(cwd)[0])
    ok, detail, unreachable = self_test(cfg)
    if unreachable:
        return False, ("Galtea tracing self-test got no answer from Galtea at %s (%s). Sessions may not be "
                       "sent. The check runs again at a session start after %d minutes."
                       % (cfg["api_url"], detail, UNREACHABLE_RETRY // 60)), True
    if not ok:
        return False, "Galtea tracing self-test failed (%s). See %s." % (detail, data_path("hook.log")), False
    minutes = product.get("autoCloseInactivityMinutes")
    head = "Galtea tracing is on. Sessions go to product %s." % (product.get("name") or cfg["product_id"])
    if scope in ("ALL", "PRODUCTION") and minutes:
        head += " A session closes after %s minutes without activity." % minutes
    return True, " ".join([head] + notes), False


def read_marker(name, max_age=None):
    """The fingerprint in a marker file; None when it is missing or older than max_age seconds."""
    try:
        if max_age is not None and os.path.getmtime(data_path(name)) < time.time() - max_age:
            return None
        with open(data_path(name), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def on_start(cfg, hook):
    if hook.get("source") == "startup":
        prune_state()
    # SessionStart is synchronous: a check against a hanging host must not stall every /clear
    if hook.get("source") in MID_SESSION_SOURCES:
        return None
    fp = fingerprint(cfg)
    # a host that did not answer stalled the last start for the whole check: wait before the next try
    if fp in (read_marker("checked"), read_marker("unreachable", UNREACHABLE_RETRY)):
        return None
    ok, message, unreachable = run_check(cfg, hook.get("cwd"))
    if ok or unreachable:
        try:
            os.makedirs(data_dir(), exist_ok=True)
            with open(data_path("checked" if ok else "unreachable"), "w", encoding="utf-8") as f:
                f.write(fp)
        except OSError:  # the user still gets the result
            log_exc("check marker not written")
    return {"systemMessage": message}


HANDLERS = {"SessionStart": on_start, "UserPromptSubmit": on_prompt, "Stop": on_stop, "SessionEnd": on_end}


def hook_main(raw):
    """Run one hook. Returns the dict to print, or None."""
    if os.environ.get("GALTEA_CC_DISABLE") == "1":
        return None
    # `claude -p` kills async hooks at teardown, so its Stop never posts the turn and
    # the session would stay empty and open forever. Claude Code sets this in every hook.
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT") == "sdk-cli":
        return None
    hook = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    event = hook.get("hook_event_name")
    handler = HANDLERS.get(event)
    if not handler or not hook.get("session_id"):
        return None
    cfg = load_config()
    if cfg["error"]:
        log(cfg["error"])
        if event == "SessionStart":
            return {"systemMessage": cfg["error"] + ", then start a new session."}
        return None
    return handler(cfg, hook)


def cmd_mute(muted, args):
    if args and args[0]:
        os.environ["CLAUDE_PLUGIN_DATA"] = args[0]
    root = set_muted(os.getcwd(), muted)
    if root is None:
        print("Galtea tracing mutes every repository while %s cannot be read. Fix or delete it, then run "
              "this command again." % data_path("muted.json"))
    elif muted:
        print("Galtea tracing is muted for %s. Run /galtea-tracing:unmute to log it again." % root)
    else:
        # a mute of a parent folder stays: it also covers the other repositories under it
        parents = [r for r in muted_roots() or [] if contains(r, norm(os.getcwd()))]
        if parents:
            print("Galtea tracing is still muted for %s, by the mute of %s. Run /galtea-tracing:unmute in %s "
                  "to log it again." % (root, parents[0], parents[0]))
        else:
            print("Galtea tracing is on again for %s." % root)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "hook"
    if cmd == "hook":
        try:
            out = hook_main(sys.stdin.buffer.read())
            if out:
                sys.stdout.write(scrub(json.dumps(out)) + "\n")
        except Exception:
            log_exc("hook failed")
        return 0
    if cmd in ("mute", "unmute"):
        cmd_mute(cmd == "mute", argv[2:])
        return 0
    sys.stderr.write("usage: galtea_cc.py [hook|mute|unmute] [data_dir]\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
