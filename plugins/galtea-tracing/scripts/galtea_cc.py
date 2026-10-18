#!/usr/bin/env python3
"""Log Claude Code conversations to Galtea as production sessions.

Run by the galtea-tracing plugin through scripts/run.sh:
  hook SessionStart -> first session after a config change: check key, product,
                       auto-close, thinking summaries, and run a self-test
  hook UserPromptSubmit -> remember the prompt, ensure the Galtea session exists
  hook Stop         -> POST /inferenceResults (prompt + final assistant reply),
                       then POST /traces/batch with the turn's steps, then the
                       session title to metadata when it changed
  hook SessionEnd   -> the session title, then PATCH /sessions/{id}/finish so
                       Monitors score it
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
_PRODUCT_ID, _API_URL, _REDACT_SECRETS). GALTEA_API_URL overrides the host for
development. GALTEA_CC_DISABLE=1 skips logging for this process. `claude -p`
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
STATE_MAX_AGE = 7 * 24 * 3600
WAIT = 40  # seconds a hook waits on another hook of its session: longer than one 30 s API call
CHECK_BUDGET = 20  # seconds; the SessionStart hook times out at 30
PENDING_GRACE = 5  # seconds SessionEnd waits for a Stop hook that has not started yet
PENDING_MAX = 10  # unsent prompts kept per session: an interrupted turn gets no Stop to take its prompt
SETTINGS_FILE = os.path.expanduser("~/.claude/settings.json")
REDACTED = "[REDACTED]"


# --- paths, log, config ----------------------------------------------------

def data_dir():
    return os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.expanduser("~/.claude/galtea-tracing")


def data_path(*parts):
    return os.path.join(data_dir(), *parts)


def log(msg):
    try:
        os.makedirs(data_dir(), exist_ok=True)
        with open(data_path("hook.log"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg))
    except Exception:
        pass


def log_exc(prefix):
    log("%s: %s" % (prefix, traceback.format_exc().strip().replace("\n", " | ")))


def load_config():
    env = os.environ
    redact = env.get("CLAUDE_PLUGIN_OPTION_REDACT_SECRETS", "").strip().lower()
    return {
        "api_key": env.get("CLAUDE_PLUGIN_OPTION_API_KEY", "").strip(),
        "product_id": env.get("CLAUDE_PLUGIN_OPTION_PRODUCT_ID", "").strip(),
        "api_url": (env.get("GALTEA_API_URL") or env.get("CLAUDE_PLUGIN_OPTION_API_URL") or DEFAULT_API_URL).strip(),
        "redact": redact not in ("false", "0", "no", "off"),
        "max_span_bytes": MAX_SPAN_BYTES,
    }


def plugin_version():
    try:
        with open(os.path.join(PLUGIN_ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
            return json.load(f).get("version")
    except Exception:
        return None


class ApiError(RuntimeError):
    def __init__(self, method, path, status, body):
        super().__init__("%s %s -> %s %s" % (method, path, status, body[:500]))
        self.status = status
        self.body = body


def api(cfg, method, path, body=None, timeout=None):
    if timeout is None:
        timeout = 30
        if cfg.get("deadline"):
            timeout = cfg["deadline"] - time.time()
            if timeout <= 0:
                raise RuntimeError("%s %s -> out of time" % (method, path))
    req = urllib.request.Request(
        cfg["api_url"].rstrip("/") + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": "Bearer " + cfg["api_key"], "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise ApiError(method, path, e.code, e.read())


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
        self.data = json.loads(raw) if raw.strip() else {"index": 0}
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

_NAME = r"[\w.-]{0,64}?(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY|ACCESS_KEY)[\w.-]{0,64}?"
# A value of digits only is a count or a limit (max_tokens=100000), never a secret.
_NOT_A_NUMBER = r"(?!\d+(?:[\s\"'`,;)\]}]|$))"
# (name, pattern, replacement). Each pattern is anchored by a literal prefix or a
# lookbehind so a long run that fails to match costs one scan, not one per offset.
RULES = [
    ("pem_private_key", re.compile(r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY-----"
                                   r"(?:[\s\S]{0,8000}?-----END [A-Z0-9 ]{0,40}PRIVATE KEY-----|[\s\S]*)"), REDACTED),
    ("galtea_key", re.compile(r"gsk_[A-Za-z0-9_-]{20,}"), REDACTED),
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
    ("env_assignment", re.compile(r"(?i)(?<![\w.-])(" + _NAME + r")(\s*=\s*)([\"']?)" + _NOT_A_NUMBER + r"[^\s\"'`]{6,}"),
     r"\1\2\3" + REDACTED),
    ("json_field", re.compile(r"(?i)(\"" + _NAME + r"\"\s*:\s*\")" + _NOT_A_NUMBER + r"(?:[^\"\\]|\\.){6,}(\")"),
     r"\1" + REDACTED + r"\2"),
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

def read_jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except FileNotFoundError:
        pass
    return out


def is_prompt_line(d):
    if d.get("type") != "user" or d.get("isSidechain"):
        return False
    c = (d.get("message") or {}).get("content")
    if isinstance(c, str):
        return True
    return isinstance(c, list) and not any(b.get("type") == "tool_result" for b in c)


def turn_lines(lines, prompt_id):
    """Main-thread lines from this turn's prompt to the end of the file."""
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
    return [d for d in lines[start:] if not d.get("isSidechain")]


def clip(value, limit):
    """Keep payloads under the per-span byte budget."""
    if value is None:
        return None
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(s.encode()) <= limit:
        return value
    return s.encode()[:limit].decode(errors="ignore") + "\n…[truncated %d bytes]" % (len(s.encode()) - limit)


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


def build_spans(lines, ir_id, cfg, parent_id, subagent_dir, totals, depth=0):
    """Turn transcript lines into Galtea spans. Model calls become GENERATION
    spans (grouped by API message id), tool calls TOOL spans, subagents AGENT
    spans whose own transcript is nested underneath."""
    limit = cfg["max_span_bytes"]
    spans, gens, tools = [], {}, {}
    prev_ts = lines[0].get("timestamp") if lines else None

    for d in lines:
        ts = d.get("timestamp")
        msg = d.get("message") or {}
        if d.get("type") == "assistant":
            mid = msg.get("id") or d.get("uuid")
            g = gens.get(mid)
            if g is None:
                g = {"id": str(uuid.uuid4()), "start": prev_ts, "thinking": [], "text": [],
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
                    tools[b["id"]] = {"id": str(uuid.uuid4()), "name": b.get("name"),
                                      "input": b.get("input"), "start": ts}
                    spans.append(("tool", b["id"]))
        elif d.get("type") == "user":
            for b in msg.get("content") if isinstance(msg.get("content"), list) else []:
                if b.get("type") != "tool_result" or b.get("tool_use_id") not in tools:
                    continue
                t = tools[b["tool_use_id"]]
                t["end"] = ts
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
            out.append({
                "id": g["id"], "inferenceResultId": ir_id, "parentTraceId": parent_id,
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
            is_agent = t["name"] in AGENT_TOOLS
            out.append({
                "id": t["id"], "inferenceResultId": ir_id, "parentTraceId": parent_id,
                "name": "%s:%s" % ("agent" if is_agent else "tool", t["name"]),
                "type": "AGENT" if is_agent else "TOOL",
                "inputData": {"input": clean(cfg, t["input"], limit)},
                "outputData": clean(cfg, t.get("output"), limit),
                "error": clean(cfg, t.get("output"), 2000) if t.get("is_error") else None,
                "startTime": t["start"], "endTime": t.get("end"),
                "latencyMs": ms_between(t["start"], t["end"]) if t.get("end") else None,
            })
            if is_agent and t.get("agent_id") and depth < 3:
                sub = [d for d in read_jsonl(os.path.join(subagent_dir, "agent-%s.jsonl" % t["agent_id"]))
                       if d.get("type") in ("user", "assistant")]
                out.extend(build_spans(sub, ir_id, cfg, t["id"], subagent_dir, totals, depth + 1))
    # Galtea rejects null parents only if referenced; drop empty keys for clean payloads
    return [{k: v for k, v in s.items() if v is not None} for s in out]


def log_spans(cfg, hook, ir_id, wait=1.5):
    path = hook.get("transcript_path")
    if not path:
        return None
    time.sleep(wait)  # let Claude Code flush the final transcript lines
    lines = turn_lines(read_jsonl(path), hook.get("prompt_id"))
    if not lines:
        return None
    subagent_dir = os.path.join(os.path.dirname(path), hook["session_id"], "subagents")
    totals = {}
    spans = build_spans(lines, ir_id, cfg, None, subagent_dir, totals)
    for i in range(0, len(spans), SPAN_BATCH):
        res = api(cfg, "POST", "/traces/batch?includeRejected=true", {"traces": spans[i:i + SPAN_BATCH]})
        rej = (res.get("partialSuccess") or {}).get("errors") if isinstance(res, dict) else None
        if rej:
            log("rejected spans for %s: %s" % (ir_id, json.dumps(rej)[:1000]))
    return totals


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
    return re.sub(r"^([A-Za-z][A-Za-z0-9+.-]*://)[^/@]*@", r"\1", url)


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
    try:
        with open(data_path("muted.json"), encoding="utf-8") as f:
            roots = json.load(f)
        return roots if isinstance(roots, list) else []
    except (OSError, ValueError):
        return []


def is_muted(cwd):
    if not cwd:
        return False
    here = norm(cwd)
    return any(here == r or here.startswith(r.rstrip(os.sep) + os.sep) for r in muted_roots())


def set_muted(cwd, muted):
    root = repo_root(cwd)
    roots = [r for r in muted_roots() if r != root]
    if muted:
        roots.append(root)
    os.makedirs(data_dir(), exist_ok=True)
    with open(data_path("muted.json"), "w", encoding="utf-8") as f:
        json.dump(sorted(roots), f, indent=2)
    return root


# --- hook handlers ---------------------------------------------------------

def session_body(cfg, custom_id, cwd, production=True):
    meta = {"source": "claude-code", "cwd": cwd, "user": os.environ.get("USER") or os.environ.get("USERNAME"),
            "plugin_version": plugin_version()}
    meta.update(git_meta(cwd))
    return {"customId": custom_id, "isProduction": production, "productId": cfg["product_id"],
            "metadata": {k: v for k, v in meta.items() if v is not None}}


def ensure_session(cfg, hook):
    """The Galtea session id, created once. While another hook of the session is
    creating it (the `creating` claim), wait for its result instead of creating a second."""
    key = hook["session_id"]
    deadline = time.time() + WAIT
    while True:
        with State(key) as st:
            if st.get("galtea_session_id"):
                return st["galtea_session_id"]
            now = time.time()
            if now - st.get("creating", 0) > WAIT or now > deadline:
                st["creating"] = now
                break
        time.sleep(0.1)
    try:
        sid, next_index, meta = create_session(cfg, hook)
    except Exception:
        with State(key) as st:
            st.pop("creating", None)
        raise
    with State(key) as st:
        st.pop("creating", None)
        st["galtea_session_id"] = sid
        st["index"] = max(st["index"], next_index)
        if meta is not None:
            st["metadata"] = meta
    return sid


def create_session(cfg, hook):
    """POST /sessions. When the customId is taken, this is a resumed session whose state
    file is gone (pruned, or the plugin reinstalled): reuse it after its last turn.
    Returns (session id, next turn index, metadata or None when unknown)."""
    key = hook["session_id"]
    body = session_body(cfg, key, hook.get("cwd"))
    try:
        return api(cfg, "POST", "/sessions", body)["id"], 0, body["metadata"]
    except ApiError as e:
        if e.status != 400:
            raise
        q = urllib.parse.quote
        found = items(api(cfg, "GET", "/sessions?customIds=%s&productIds=%s" % (q(key), q(cfg["product_id"]))))
        if not found:
            raise
    sid = max(found, key=lambda x: x.get("createdAt") or "")["id"]
    turns = items(api(cfg, "GET", "/inferenceResults?sessionIds=%s" % urllib.parse.quote(sid)))
    next_index = max([len(turns)] + [t["index"] + 1 for t in turns if isinstance(t.get("index"), int)])
    log("session %s already exists in Galtea; continuing it at turn %d" % (sid, next_index))
    return sid, next_index, None


def post_turn(cfg, sid, index, pending, hook):
    return api(cfg, "POST", "/inferenceResults", {
        "sessionId": sid,
        "index": index,
        "status": "GENERATED",
        "input": {"user_message": clean(cfg, pending["prompt"])},
        "actualOutput": clean(cfg, hook.get("last_assistant_message") or ""),
        "latency": int((time.time() - pending["ts"]) * 1000),
    })


def post_spans(cfg, hook, ir_id, wait=1.5):
    """Spans and token counts for one turn. A failure is logged; the turn stays."""
    try:
        totals = log_spans(cfg, hook, ir_id, wait)
        if totals:
            # no `tokens`: the API derives it, and has no cache-creation field to add
            api(cfg, "PATCH", "/inferenceResults/%s" % ir_id, {
                "inputTokens": totals.get("in", 0), "outputTokens": totals.get("out", 0),
                "cacheReadInputTokens": totals.get("cache", 0),
            })
    except Exception:
        log_exc("spans failed for %s" % ir_id)


def on_prompt(cfg, hook):
    if is_muted(hook.get("cwd")):
        return
    pid = hook.get("prompt_id")
    with State(hook["session_id"]) as st:
        # one entry per prompt: a queued prompt's hook can run before the previous turn's Stop
        prompts = [p for p in st.get("prompts", []) if pid is None or p.get("prompt_id") != pid]
        prompts.append({"prompt": hook.get("prompt", ""), "ts": time.time(), "prompt_id": pid})
        st["prompts"] = prompts[-PENDING_MAX:]
    ensure_session(cfg, hook)


def take_prompt(st, prompt_id):
    """Remove and return this Stop's prompt: the one with its prompt_id, else the oldest
    prompt stored without an id. With no prompt_id on the Stop, the oldest prompt."""
    prompts = st.get("prompts", [])
    ids = [p.get("prompt_id") for p in prompts]
    if prompt_id is None:
        i = 0 if prompts else None
    else:
        i = ids.index(prompt_id) if prompt_id in ids else (ids.index(None) if None in ids else None)
    return None if i is None else prompts.pop(i)


def on_stop(cfg, hook):
    key = hook["session_id"]
    muted = is_muted(hook.get("cwd"))
    token = uuid.uuid4().hex
    with State(key) as st:
        pending = take_prompt(st, hook.get("prompt_id"))
        if not pending or muted:
            return  # muted, or Stop re-fired (e.g. after a blocking Stop hook) with no new prompt
        st.setdefault("inflight", {})[token] = time.time()  # SessionEnd waits for it
    try:
        send_turn(cfg, hook, pending)
    finally:
        with State(key) as st:
            st.get("inflight", {}).pop(token, None)


def send_turn(cfg, hook, pending):
    key = hook["session_id"]
    index = None
    try:
        sid = ensure_session(cfg, hook)
        with State(key) as st:
            index = st["index"]
            st["index"] += 1
        ir = post_turn(cfg, sid, index, pending, hook)
    except Exception:
        log_exc("turn not sent for session %s" % key)
        if index is not None:
            with State(key) as st:
                if st["index"] == index + 1:  # no later turn took the next index
                    st["index"] = index
        return
    with State(key) as st:
        st.pop("finished", None)  # a resumed session was reopened by this turn
    hook["prompt_id"] = hook.get("prompt_id") or pending.get("prompt_id")
    post_spans(cfg, hook, ir["id"])
    sync_title(cfg, hook)


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
        if meta is None:  # a session found again after its state was lost, or made by an older plugin
            meta = api(cfg, "GET", "/sessions/%s" % sid).get("metadata")
            meta = meta if isinstance(meta, dict) else {}
        meta = dict(meta, title=sent[0], title_source=sent[1])
        api(cfg, "PATCH", "/sessions/%s" % sid, {"metadata": meta})
        with State(key) as st:
            st["metadata"], st["title_sent"] = meta, sent
    except Exception:
        log_exc("title not sent for session %s" % key)


def wait_for_turns(key):
    """Wait until no Stop hook of the session is still sending its turn. A stored prompt
    whose Stop has not started is waited for only PENDING_GRACE seconds, because an
    interrupted turn never gets a Stop."""
    start = time.time()
    while True:
        with State(key) as st:
            now = time.time()
            # a Stop hook killed mid-turn never clears its entry, so an old one is ignored
            st["inflight"] = {t: ts for t, ts in st.get("inflight", {}).items() if now - ts < WAIT}
            busy = st["inflight"] or (st.get("prompts") and now - start < PENDING_GRACE)
            if not busy or now - start > WAIT:
                return
        time.sleep(0.1)


def on_end(cfg, hook):
    key = hook["session_id"]
    wait_for_turns(key)
    if not is_muted(hook.get("cwd")):
        sync_title(cfg, hook)
    with State(key) as st:
        sid = st.get("galtea_session_id")
        if not sid or st["index"] == 0 or st.get("finished"):
            return
        st["finished"] = True
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


def thinking_summaries_on():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f).get("showThinkingSummaries") is True
    except Exception:
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
    Never finished, so no monitor evaluates it. Returns (ok, detail)."""
    tdir = tempfile.mkdtemp(prefix="galtea-cc-selftest-")
    custom_id = "galtea-cc-selftest-%d-%s" % (int(time.time()), uuid.uuid4().hex[:8])
    tpath = os.path.join(tdir, custom_id + ".jsonl")
    sid = None
    try:
        selftest_transcript(tpath)
        sid = api(cfg, "POST", "/sessions", session_body(cfg, custom_id, tdir, production=False))["id"]
        hook = {"session_id": custom_id, "transcript_path": tpath, "cwd": tdir, "prompt_id": "p1",
                "last_assistant_message": SELFTEST_REPLY}
        ir = post_turn(cfg, sid, 0, {"prompt": SELFTEST_PROMPT, "ts": time.time()}, hook)
        post_spans(cfg, hook, ir["id"], wait=0)
        irs = items(api(cfg, "GET", "/inferenceResults?sessionIds=%s" % sid))
        traces = items(api(cfg, "GET", "/traces?inferenceResultIds=%s" % ir["id"]))
        ok = len(irs) == 1 and len(traces) >= 3
        return ok, "turns=%d traces=%d" % (len(irs), len(traces))
    finally:
        if sid:
            try:
                api(cfg, "DELETE", "/sessions/%s" % sid, timeout=5)
            except Exception:
                log_exc("selftest could not delete development session %s" % sid)
        shutil.rmtree(tdir, ignore_errors=True)


def run_check(cfg):
    """Returns (ok, message) for the SessionStart systemMessage."""
    cfg = dict(cfg, deadline=time.time() + CHECK_BUDGET)
    try:
        product = api(cfg, "GET", "/products/%s" % cfg["product_id"])
    except Exception as e:
        log_exc("check: product lookup failed")
        if isinstance(e, ApiError) and e.status in (401, 403, 404):
            return False, ("Galtea tracing is not working: the API key or product id was refused (%s). "
                           "Fix them with /plugin, then start a new session." % str(e)[:200])
        return False, ("Galtea tracing could not reach Galtea at %s (%s). Sessions are not sent while it "
                       "is unreachable. The check runs again at the next session start."
                       % (cfg["api_url"], str(e)[:200]))
    notes = []
    scope = product.get("autoCloseScope")
    if scope in ("NONE", "DEVELOPMENT"):
        notes.append("This product does not auto-close production sessions (autoCloseScope=%s). "
                     "A session you leave without /exit stays open and is never evaluated. "
                     "Set auto-close to ALL or PRODUCTION in the product settings." % scope)
    if not thinking_summaries_on():
        notes.append('Thinking is not logged. To log it, add "showThinkingSummaries": true to ~/.claude/settings.json.')
    try:
        ok, detail = self_test(cfg)
    except Exception as e:
        log_exc("check: selftest failed")
        ok, detail = False, str(e)[:200]
    if not ok:
        return False, "Galtea tracing self-test failed (%s). See %s." % (detail, data_path("hook.log"))
    minutes = product.get("autoCloseInactivityMinutes")
    head = "Galtea tracing is on. Sessions go to product %s." % (product.get("name") or cfg["product_id"])
    if scope in ("ALL", "PRODUCTION") and minutes:
        head += " A session closes after %s minutes without activity." % minutes
    return True, " ".join([head] + notes)


def on_start(cfg, hook):
    if hook.get("source") == "startup":
        prune_state()
    fp = fingerprint(cfg)
    try:
        with open(data_path("checked"), encoding="utf-8") as f:
            if f.read().strip() == fp:
                return None
    except OSError:
        pass
    ok, message = run_check(cfg)
    if ok:
        os.makedirs(data_dir(), exist_ok=True)
        with open(data_path("checked"), "w", encoding="utf-8") as f:
            f.write(fp)
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
    if not cfg["api_key"] or not cfg["product_id"]:
        log("missing api key or product id; set them with /plugin")
        if event == "SessionStart":
            return {"systemMessage": "Galtea tracing is installed but has no API key or product id. "
                                     "Set them with /plugin, then start a new session."}
        return None
    return handler(cfg, hook)


def cmd_mute(muted, args):
    if args and args[0]:
        os.environ["CLAUDE_PLUGIN_DATA"] = args[0]
    root = set_muted(os.getcwd(), muted)
    if muted:
        print("Galtea tracing is muted for %s. Run /galtea-tracing:unmute to log it again." % root)
    else:
        print("Galtea tracing is on again for %s." % root)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "hook"
    if cmd == "hook":
        try:
            out = hook_main(sys.stdin.buffer.read())
            if out:
                sys.stdout.write(json.dumps(out) + "\n")
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
