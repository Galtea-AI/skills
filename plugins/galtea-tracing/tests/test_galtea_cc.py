"""Tests for scripts/galtea_cc.py. Stdlib only, no network: `api` is replaced by a recorder.

Run:  python3 -m unittest discover plugins/galtea-tracing/tests -v
"""
import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PLUGIN, "scripts"))
import galtea_cc as gc  # noqa: E402

REAL_API = gc.api

UUID = "3f2b8c1e-9a4d-4e7b-b1c2-5d6e7f8a9b0c"
SHA = "236c26f2d1a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5"
B64 = "aGVsbG8gd29ybGQgdGhpcyBpcyBhIGxvbmcgYmFzZTY0IGJsb2IgdGhhdCBpcyBub3QgYSBzZWNyZXQ="

# One sample per rule. The secret part must be gone after redaction.
SAMPLES = {
    "pem_private_key": ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAr1x\n-----END RSA PRIVATE KEY-----",
                        "MIIEowIBAAKCAQEAr1x"),
    "galtea_key": ("key gsk_AbCdEf123456_ZyXwVuTsRqPoNm987654", "gsk_AbCdEf123456"),
    "anthropic_key": ("sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWx", "sk-ant-api03"),
    "openai_project_key": ("sk-proj-AbCdEfGhIjKlMnOpQrStUvWx12", "sk-proj-AbCdEf"),
    "openai_key": ("sk-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "sk-AbCdEf"),
    "aws_access_key_id": ("aws AKIAIOSFODNN7EXAMPLE here", "AKIAIOSFODNN7EXAMPLE"),
    "github_token": ("ghp_" + "a1B2c3D4e5" * 4, "ghp_a1B2"),
    "github_pat": ("github_pat_" + "11ABCDEFG_" * 7, "github_pat_11ABC"),
    "slack_token": ("xoxb-1234567890-abcdefghij", "xoxb-1234567890"),
    "google_api_key": ("AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q", "AIzaSyA1b2"),
    "jwt": ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0"),
    "auth_header": ('curl -H "Authorization: Basic dXNlcjpwYXNzd29yZA==" x', "dXNlcjpwYXNzd29yZA"),
    "bearer": ("send bearer abcdefghijklmnop1234 now", "abcdefghijklmnop1234"),
    "url_password": ("postgres://admin:hunter2pass@db.example.com:5432/app", "hunter2pass"),
    "env_assignment": ("export DATABASE_PASSWORD=s3cr3tValue", "s3cr3tValue"),
    "json_field": ('{"client_secret": "abcdef123456"}', "abcdef123456"),
}


class FakeApi:
    """Records every call; answers from `responses` keyed by (method, path prefix)."""

    def __init__(self, responses=None, fail=None):
        self.calls = []
        self.responses = responses or {}
        self.fail = fail or ()

    def __call__(self, cfg, method, path, body=None, timeout=None):
        self.calls.append((method, path, body))
        for m, prefix, *exc in self.fail:
            if m == method and path.startswith(prefix):
                raise exc[0] if exc else RuntimeError("%s %s -> 500" % (method, path))
        for (m, prefix), res in self.responses.items():
            if m == method and path.startswith(prefix):
                return res(body) if callable(res) else res
        return {}

    def paths(self, method=None):
        return [p for m, p, _ in self.calls if method in (None, m)]

    def body(self, method, prefix):
        return next(b for m, p, b in self.calls if m == method and p.startswith(prefix))


def ok_responses():
    return {
        ("GET", "/products/"): {"id": "prod_1", "name": "My product", "autoCloseScope": "ALL",
                                "autoCloseInactivityMinutes": 30},
        ("POST", "/sessions"): {"id": "sess_1"},
        ("POST", "/inferenceResults"): {"id": "ir_1"},
        ("GET", "/inferenceResults"): [{"id": "ir_1"}],
        ("GET", "/traces"): [{"id": "a"}, {"id": "b"}, {"id": "c"}],
    }


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "data")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CLAUDE_PLUGIN_", "GALTEA_"))}
        env.update({"CLAUDE_PLUGIN_DATA": self.data, "CLAUDE_PLUGIN_OPTION_API_KEY": "gsk_test_key",
                    "CLAUDE_PLUGIN_OPTION_PRODUCT_ID": "prod_1", "GALTEA_API_URL": "http://127.0.0.1:9",
                    "CLAUDE_CODE_ENTRYPOINT": "cli"})
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        self.settings = os.path.join(self.tmp, "settings.json")
        for target, value in (("SETTINGS_FILE", self.settings), ("api", self.fail_network)):
            p = mock.patch.object(gc, target, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    @staticmethod
    def fail_network(*a, **k):
        raise AssertionError("unexpected API call")

    def use_api(self, fake):
        p = mock.patch.object(gc, "api", fake)
        p.start()
        self.addCleanup(p.stop)
        return fake

    def transcript(self, lines):
        path = os.path.join(self.tmp, "t.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(x) for x in lines) + "\n")
        return path

    def turn(self, output="42", usage=None):
        return self.transcript([
            {"type": "user", "promptId": "p1", "timestamp": "2026-01-01T00:00:00Z",
             "message": {"content": "hi"}},
            {"type": "assistant", "timestamp": "2026-01-01T00:00:01Z", "message": {
                "id": "m1", "model": "claude", "usage": usage or {"input_tokens": 10, "output_tokens": 5,
                                                                  "cache_read_input_tokens": 7,
                                                                  "cache_creation_input_tokens": 3},
                "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}},
            {"type": "user", "timestamp": "2026-01-01T00:00:02Z", "message": {
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": output}]}},
        ])

    def hook(self, event, **kw):
        h = {"hook_event_name": event, "session_id": "s1", "cwd": self.tmp, "prompt_id": "p1"}
        h.update(kw)
        return h


class TestConfig(Base):
    def test_no_product_makes_no_api_call(self):
        os.environ["CLAUDE_PLUGIN_OPTION_PRODUCT_ID"] = ""
        fake = self.use_api(FakeApi(ok_responses()))
        for event in ("UserPromptSubmit", "Stop", "SessionEnd"):
            self.assertIsNone(gc.hook_main(json.dumps(self.hook(event, prompt="hi"))))
        out = gc.hook_main(json.dumps(self.hook("SessionStart", source="startup")))
        self.assertIn("no API key or product id", out["systemMessage"])
        self.assertEqual(fake.calls, [])

    def test_disable_env_skips_everything(self):
        os.environ["GALTEA_CC_DISABLE"] = "1"
        fake = self.use_api(FakeApi(ok_responses()))
        self.assertIsNone(gc.hook_main(json.dumps(self.hook("SessionStart", source="startup"))))
        self.assertEqual(fake.calls, [])

    def test_headless_run_makes_no_api_call(self):
        # Claude Code sets CLAUDE_CODE_ENTRYPOINT=sdk-cli in every hook of a `claude -p` run
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "sdk-cli"
        fake = self.use_api(FakeApi(ok_responses()))
        path = self.transcript([{"type": "user", "promptId": "p1", "message": {"content": "hi"}}])
        with mock.patch.object(gc.time, "sleep"):
            for event in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"):
                self.assertIsNone(gc.hook_main(json.dumps(self.hook(
                    event, source="startup", prompt="hi", transcript_path=path, last_assistant_message="ok"))))
        self.assertEqual(fake.calls, [])
        self.assertFalse(os.path.exists(os.path.join(self.data, "state")))

    def test_options_and_host_override(self):
        os.environ["CLAUDE_PLUGIN_OPTION_API_URL"] = "https://example.invalid"
        os.environ["CLAUDE_PLUGIN_OPTION_REDACT_SECRETS"] = "false"
        cfg = gc.load_config()
        self.assertEqual(cfg["api_url"], "http://127.0.0.1:9")
        self.assertFalse(cfg["redact"])
        del os.environ["GALTEA_API_URL"]
        self.assertEqual(gc.load_config()["api_url"], "https://example.invalid")
        del os.environ["CLAUDE_PLUGIN_OPTION_API_URL"]
        self.assertEqual(gc.load_config()["api_url"], gc.DEFAULT_API_URL)

    def test_data_dir_falls_back_to_home(self):
        del os.environ["CLAUDE_PLUGIN_DATA"]
        self.assertEqual(gc.data_dir(), os.path.expanduser("~/.claude/galtea-tracing"))


class TestTurn(Base):
    def run_turn(self, path, cwd=None):
        fake = self.use_api(FakeApi(ok_responses()))
        with mock.patch.object(gc.time, "sleep"):
            gc.hook_main(json.dumps(self.hook("UserPromptSubmit", prompt="hi", cwd=cwd or self.tmp)))
            gc.hook_main(json.dumps(self.hook("Stop", transcript_path=path, last_assistant_message="done",
                                              cwd=cwd or self.tmp)))
        return fake

    def test_token_payload_omits_tokens(self):
        fake = self.run_turn(self.turn())
        body = fake.body("PATCH", "/inferenceResults/ir_1")
        self.assertEqual(body, {"inputTokens": 10, "outputTokens": 5, "cacheReadInputTokens": 7})
        gen = next(t for t in fake.body("POST", "/traces/batch")["traces"] if t["type"] == "GENERATION")
        self.assertEqual(gen["metadata"]["usage"]["cache_creation_input_tokens"], 3)

    def test_session_is_production_with_metadata(self):
        fake = self.run_turn(self.turn())
        body = fake.body("POST", "/sessions")
        self.assertTrue(body["isProduction"])
        self.assertEqual(body["productId"], "prod_1")
        self.assertEqual(body["metadata"]["plugin_version"], "0.1.0")
        self.assertEqual(fake.body("POST", "/inferenceResults")["index"], 0)

    def test_session_end_finishes_once(self):
        fake = self.run_turn(self.turn())
        gc.hook_main(json.dumps(self.hook("SessionEnd", reason="prompt_input_exit")))
        gc.hook_main(json.dumps(self.hook("SessionEnd", reason="other")))
        self.assertEqual(fake.paths("PATCH").count("/sessions/sess_1/finish"), 1)
        with mock.patch.object(gc.time, "sleep"):  # --resume: a new turn, then a new end
            gc.hook_main(json.dumps(self.hook("UserPromptSubmit", prompt="again")))
            gc.hook_main(json.dumps(self.hook("Stop", last_assistant_message="ok")))
        gc.hook_main(json.dumps(self.hook("SessionEnd", reason="other")))
        self.assertEqual(fake.paths("PATCH").count("/sessions/sess_1/finish"), 2)

    def test_muted_repo_skips_the_turn(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(repo, "sub"))
        gc.set_muted(repo, True)
        fake = self.run_turn(self.turn(), cwd=os.path.join(repo, "sub"))
        self.assertEqual(fake.calls, [])
        gc.set_muted(repo, False)
        fake = self.run_turn(self.turn(), cwd=os.path.join(repo, "sub"))
        self.assertIn("/inferenceResults", fake.paths("POST"))

    def test_mute_between_prompt_and_stop_drops_the_turn(self):
        fake = self.use_api(FakeApi(ok_responses()))
        gc.hook_main(json.dumps(self.hook("UserPromptSubmit", prompt="/galtea-tracing:mute")))
        gc.set_muted(self.tmp, True)
        gc.hook_main(json.dumps(self.hook("Stop", last_assistant_message="muted")))
        self.assertNotIn("/inferenceResults", fake.paths("POST"))
        gc.set_muted(self.tmp, False)
        gc.hook_main(json.dumps(self.hook("Stop", last_assistant_message="again")))
        self.assertNotIn("/inferenceResults", fake.paths("POST"))

    def test_mute_does_not_cover_a_sibling_prefix(self):
        a, ab = os.path.join(self.tmp, "a"), os.path.join(self.tmp, "ab")
        os.makedirs(a)
        os.makedirs(ab)
        gc.set_muted(a, True)
        self.assertTrue(gc.is_muted(a))
        self.assertFalse(gc.is_muted(ab))

    def test_redaction_runs_before_the_cut(self):
        secret = "gsk_" + "Z" * 40
        output = "x" * (gc.MAX_SPAN_BYTES - 10) + secret
        fake = self.run_turn(self.turn(output=output))
        tool = next(t for t in fake.body("POST", "/traces/batch")["traces"] if t["type"] == "TOOL")
        self.assertNotIn("gsk_", tool["outputData"])
        self.assertIn(gc.REDACTED, tool["outputData"])

    def test_prompt_and_reply_are_redacted(self):
        fake = self.use_api(FakeApi(ok_responses()))
        with mock.patch.object(gc.time, "sleep"):
            gc.hook_main(json.dumps(self.hook("UserPromptSubmit", prompt="use sk-ant-" + "q" * 30)))
            gc.hook_main(json.dumps(self.hook("Stop", last_assistant_message="AKIAIOSFODNN7EXAMPLE")))
        body = fake.body("POST", "/inferenceResults")
        self.assertEqual(body["input"]["user_message"], "use " + gc.REDACTED)
        self.assertEqual(body["actualOutput"], gc.REDACTED)

    def test_redaction_off_sends_raw_text(self):
        os.environ["CLAUDE_PLUGIN_OPTION_REDACT_SECRETS"] = "false"
        fake = self.use_api(FakeApi(ok_responses()))
        gc.hook_main(json.dumps(self.hook("UserPromptSubmit", prompt="AKIAIOSFODNN7EXAMPLE")))
        gc.hook_main(json.dumps(self.hook("Stop", last_assistant_message="ok")))
        self.assertEqual(fake.body("POST", "/inferenceResults")["input"]["user_message"], "AKIAIOSFODNN7EXAMPLE")

    def test_hooks_print_nothing(self):
        self.use_api(FakeApi(ok_responses()))
        for event in ("UserPromptSubmit", "Stop", "SessionEnd"):
            out = io.StringIO()
            stdin = types.SimpleNamespace(buffer=io.BytesIO(json.dumps(self.hook(event, prompt="x")).encode()))
            with mock.patch.object(sys, "stdin", stdin), contextlib.redirect_stdout(out), \
                    mock.patch.object(gc.time, "sleep"):
                self.assertEqual(gc.main(["galtea_cc.py", "hook"]), 0)
            self.assertEqual(out.getvalue(), "")

    def test_a_crash_is_logged_not_raised(self):
        self.use_api(FakeApi(fail=[("POST", "/sessions")]))
        stdin = types.SimpleNamespace(buffer=io.BytesIO(json.dumps(self.hook("UserPromptSubmit")).encode()))
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", stdin), contextlib.redirect_stdout(out):
            self.assertEqual(gc.main(["galtea_cc.py", "hook"]), 0)
        self.assertEqual(out.getvalue(), "")
        with open(os.path.join(self.data, "hook.log"), encoding="utf-8") as f:
            self.assertIn("POST /sessions -> 500", f.read())


class TestCheck(Base):
    def start(self):
        return gc.hook_main(json.dumps(self.hook("SessionStart", source="startup")))

    def test_self_test_uses_a_development_session_and_deletes_it(self):
        fake = self.use_api(FakeApi(ok_responses()))
        out = self.start()
        self.assertIn("Galtea tracing is on", out["systemMessage"])
        self.assertIs(fake.body("POST", "/sessions")["isProduction"], False)
        self.assertFalse(any("/finish" in p for p in fake.paths()))
        self.assertEqual(fake.paths("DELETE"), ["/sessions/sess_1"])

    def test_self_test_deletes_even_when_a_step_fails(self):
        fake = self.use_api(FakeApi(ok_responses(), fail=[("GET", "/inferenceResults")]))
        out = self.start()
        self.assertIn("self-test failed", out["systemMessage"])
        self.assertEqual(fake.paths("DELETE"), ["/sessions/sess_1"])
        self.assertFalse(os.path.exists(os.path.join(self.data, "checked")))

    def test_self_test_fails_on_too_few_traces(self):
        responses = ok_responses()
        responses[("GET", "/traces")] = [{"id": "a"}]
        fake = self.use_api(FakeApi(responses))
        self.assertIn("traces=1", self.start()["systemMessage"])
        self.assertEqual(fake.paths("DELETE"), ["/sessions/sess_1"])

    def test_check_runs_once_per_config(self):
        fake = self.use_api(FakeApi(ok_responses()))
        self.assertIsNotNone(self.start())
        n = len(fake.calls)
        self.assertIsNone(self.start())
        self.assertEqual(len(fake.calls), n)
        os.environ["CLAUDE_PLUGIN_OPTION_PRODUCT_ID"] = "prod_2"
        self.assertIsNotNone(self.start())

    def test_refused_key_reports_and_retries(self):
        for status in (401, 403, 404):
            with self.subTest(status=status):
                err = gc.ApiError("GET", "/products/prod_1", status, b"no")
                fake = self.use_api(FakeApi(fail=[("GET", "/products/", err)]))
                self.assertIn("refused", self.start()["systemMessage"])
                self.assertEqual(fake.paths("POST"), [])
                self.assertIsNotNone(self.start())

    def test_api_keeps_the_http_status(self):
        err = urllib.error.HTTPError("http://x/products/p", 401, "Unauthorized", {}, io.BytesIO(b"bad key"))
        with mock.patch.object(gc.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(gc.ApiError) as ctx:
                REAL_API(gc.load_config(), "GET", "/products/p")
        self.assertEqual(ctx.exception.status, 401)

    def test_unreachable_galtea_is_not_called_refused(self):
        for err in (urllib.error.URLError("Name or service not known"), socket.timeout("timed out"),
                    gc.ApiError("GET", "/products/prod_1", 502, b"bad gateway")):
            with self.subTest(err=repr(err)):
                self.use_api(FakeApi(fail=[("GET", "/products/", err)]))
                msg = self.start()["systemMessage"]
                self.assertNotIn("refused", msg)
                self.assertIn("could not reach Galtea", msg)
                self.assertIn("next session start", msg)
                self.assertFalse(os.path.exists(os.path.join(self.data, "checked")))

    def test_warns_on_auto_close_and_thinking_summaries(self):
        responses = ok_responses()
        responses[("GET", "/products/")] = {"name": "P", "autoCloseScope": "DEVELOPMENT"}
        self.use_api(FakeApi(responses))
        msg = self.start()["systemMessage"]
        self.assertIn("autoCloseScope=DEVELOPMENT", msg)
        self.assertIn('"showThinkingSummaries": true', msg)

    def test_no_thinking_note_when_setting_is_on(self):
        with open(self.settings, "w") as f:
            json.dump({"showThinkingSummaries": True}, f)
        self.use_api(FakeApi(ok_responses()))
        msg = self.start()["systemMessage"]
        self.assertNotIn("showThinkingSummaries", msg)
        self.assertIn("30 minutes", msg)

    def test_startup_prunes_old_state(self):
        state = os.path.join(self.data, "state")
        os.makedirs(state)
        old, new = os.path.join(state, "old.json"), os.path.join(state, "new.json")
        for p in (old, new):
            open(p, "w").close()
        os.utime(old, (time.time() - 8 * 86400,) * 2)
        self.use_api(FakeApi(ok_responses()))
        self.start()
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))


class TestRedaction(unittest.TestCase):
    def test_every_rule_has_a_sample(self):
        self.assertEqual(sorted(SAMPLES), sorted(name for name, _, _ in gc.RULES))

    def test_every_rule_masks_its_sample(self):
        for name, rx, repl in gc.RULES:
            text, secret = SAMPLES[name]
            with self.subTest(rule=name):
                self.assertIn(secret, text)
                once = rx.sub(repl, text)
                self.assertNotIn(secret, once)
                self.assertIn(gc.REDACTED, once)
                self.assertNotIn(secret, gc.redact(text))

    def test_names_are_kept(self):
        self.assertEqual(gc.redact("export DATABASE_PASSWORD=s3cr3tValue"), "export DATABASE_PASSWORD=" + gc.REDACTED)
        self.assertEqual(gc.redact('{"client_secret": "abcdef123456"}'), '{"client_secret": "%s"}' % gc.REDACTED)
        self.assertEqual(gc.redact("Authorization: Bearer abcdefghijklmnopqrstu"),
                         "Authorization: Bearer " + gc.REDACTED)

    def test_short_values_and_plain_ids_survive(self):
        for text in (UUID, SHA, B64, "git checkout %s" % SHA, "id=%s" % UUID, "PASSWORD=abc",
                     "max_tokens=4096", "https://github.com/Galtea-AI/skills.git", "the token is useful",
                     "max_tokens=100000", "input_tokens = 123456", "TOKEN_LIMIT=4096000",
                     "disk-0123456789abcdef0123456789abcdef", "task-0123456789abcdef0123456789abcdef",
                     "create(max_tokens=100000, model=x)", '{"max_tokens": "100000"}'):
            with self.subTest(text=text):
                self.assertEqual(gc.redact(text), text)
        self.assertEqual(gc.redact({"max_tokens": "100000"}), {"max_tokens": "100000"})

    def test_a_number_prefix_does_not_save_a_secret(self):
        self.assertEqual(gc.redact("GITHUB_TOKEN=123456abcdef"), "GITHUB_TOKEN=" + gc.REDACTED)
        self.assertEqual(gc.redact("key sk-" + "a1" * 20), "key " + gc.REDACTED)

    def test_unterminated_pem_is_redacted_to_the_end(self):
        out = gc.redact("before -----BEGIN PRIVATE KEY-----\nMIIabc\nmore")
        self.assertEqual(out, "before " + gc.REDACTED)

    def test_walks_dicts_and_lists(self):
        value = {"cmd": ["echo", "sk-ant-" + "x" * 25], "env": {"GITHUB_TOKEN": "abcdef1234", "n": 3,
                                                                  "name": "short"}}
        out = gc.redact(value)
        self.assertEqual(out, {"cmd": ["echo", gc.REDACTED],
                               "env": {"GITHUB_TOKEN": gc.REDACTED, "n": 3, "name": "short"}})

    def test_failure_never_returns_raw_text(self):
        with mock.patch.object(gc, "_redact", side_effect=RuntimeError("boom")), mock.patch.object(gc, "log"):
            self.assertEqual(gc.redact("gsk_" + "a" * 30), "[REDACTION FAILED]")

    def test_each_rule_is_fast_on_pathological_input(self):
        size = 20000
        for name, rx, repl in gc.RULES:
            sample, secret = SAMPLES[name]
            heads = (sample, sample[sample.index(secret):])  # fragments of the sample and of its secret
            frags = [h[:k] for h in heads for k in (3, 5, 8, 12, 20)]
            inputs = ["a" * size, " " * size, "-" * size, "=" * size, '"' * size]
            for frag in frags:
                inputs.append((frag * (size // len(frag) + 1))[:size])
                inputs.append((frag + " ") * (size // (len(frag) + 1)))
                inputs.append(frag + "a" * size)
                inputs.append(frag + " " * size)
            for text in inputs:
                start = time.perf_counter()
                rx.sub(repl, text)
                elapsed = time.perf_counter() - start
                with self.subTest(rule=name, head=text[:24]):
                    self.assertLess(elapsed, 0.05)


class TestConcurrency(Base):
    """Async hooks of one session run at the same time. The state lock is held only
    around state reads and writes, never across an API call."""

    def run_in_thread(self, event, **kw):
        t = threading.Thread(target=gc.hook_main, args=(json.dumps(self.hook(event, **kw)),), daemon=True)
        t.start()
        return t

    def state_is_free(self):
        """True when another opener gets the state lock within 2 s."""
        got = []

        def take():
            with gc.State("s1"):
                got.append(True)
        t = threading.Thread(target=take, daemon=True)
        t.start()
        t.join(2)
        return bool(got)

    def test_state_lock_is_free_during_api_calls(self):
        free = {}

        def probe(name, res):
            def answer(body):
                free[name] = self.state_is_free()
                return res
            return answer
        responses = ok_responses()
        responses[("POST", "/sessions")] = probe("create", {"id": "sess_1"})
        responses[("POST", "/inferenceResults")] = probe("turn", {"id": "ir_1"})
        responses[("PATCH", "/sessions/sess_1/finish")] = probe("finish", {})
        self.use_api(FakeApi(responses))
        gc.hook_main(json.dumps(self.hook("UserPromptSubmit", prompt="hi")))
        gc.hook_main(json.dumps(self.hook("Stop", last_assistant_message="ok")))
        gc.hook_main(json.dumps(self.hook("SessionEnd", reason="other")))
        self.assertEqual(free, {"create": True, "turn": True, "finish": True})

    def test_concurrent_hooks_create_one_session(self):
        started = threading.Event()

        def slow_create(body):
            started.set()
            time.sleep(0.3)
            return {"id": "sess_1"}
        responses = ok_responses()
        responses[("POST", "/sessions")] = slow_create
        fake = self.use_api(FakeApi(responses))
        prompt = self.run_in_thread("UserPromptSubmit", prompt="hi")
        self.assertTrue(started.wait(2))
        stop = self.run_in_thread("Stop", last_assistant_message="ok")
        prompt.join(5)
        stop.join(5)
        self.assertEqual(fake.paths("POST").count("/sessions"), 1)
        self.assertEqual(fake.body("POST", "/inferenceResults")["sessionId"], "sess_1")

    def test_concurrent_turns_get_distinct_indexes(self):
        release, first_in = threading.Event(), threading.Event()
        indexes = []

        def turn(body):
            indexes.append(body["index"])
            if len(indexes) == 1:
                first_in.set()
                release.wait(2)
            return {"id": "ir_%d" % body["index"]}
        responses = ok_responses()
        responses[("POST", "/inferenceResults")] = turn
        self.use_api(FakeApi(responses))
        gc.hook_main(json.dumps(self.hook("UserPromptSubmit", prompt="one")))
        slow = self.run_in_thread("Stop", last_assistant_message="1")
        self.assertTrue(first_in.wait(2))
        gc.hook_main(json.dumps(self.hook("UserPromptSubmit", prompt="two")))
        gc.hook_main(json.dumps(self.hook("Stop", last_assistant_message="2")))
        release.set()
        slow.join(5)
        self.assertEqual(sorted(indexes), [0, 1])
        with gc.State("s1") as st:
            self.assertEqual(st["index"], 2)

    def test_session_end_waits_for_a_turn_in_flight(self):
        first_in = threading.Event()
        done = []

        def turn(body):
            first_in.set()
            time.sleep(0.3)
            done.append("turn")
            return {"id": "ir_1"}
        responses = ok_responses()
        responses[("POST", "/inferenceResults")] = turn
        responses[("PATCH", "/sessions/sess_1/finish")] = lambda body: done.append("finish") or {}
        self.use_api(FakeApi(responses))
        gc.hook_main(json.dumps(self.hook("UserPromptSubmit", prompt="hi")))
        stop = self.run_in_thread("Stop", last_assistant_message="ok")
        self.assertTrue(first_in.wait(2))
        gc.hook_main(json.dumps(self.hook("SessionEnd", reason="other")))
        stop.join(5)
        self.assertEqual(done, ["turn", "finish"])

    def test_a_failed_turn_is_logged_and_frees_its_index(self):
        self.use_api(FakeApi(ok_responses(), fail=[("POST", "/inferenceResults")]))
        gc.hook_main(json.dumps(self.hook("UserPromptSubmit", prompt="hi")))
        gc.hook_main(json.dumps(self.hook("Stop", last_assistant_message="ok")))
        with open(os.path.join(self.data, "hook.log"), encoding="utf-8") as f:
            self.assertIn("turn not sent for session s1", f.read())
        with gc.State("s1") as st:
            self.assertEqual(st["index"], 0)


class TestPlatform(Base):
    def fake_msvcrt(self, busy):
        """msvcrt whose non-blocking lock fails `busy` times before it succeeds."""
        calls = []

        def locking(fd, mode, n):
            calls.append(mode)
            if mode == 2 and calls.count(2) <= busy:
                raise OSError("locked")
        return types.SimpleNamespace(LK_LOCK=1, LK_NBLCK=2, LK_UNLCK=0, locking=locking), calls

    def test_windows_lock_path_when_fcntl_is_missing(self):
        msvcrt, calls = self.fake_msvcrt(busy=0)
        with mock.patch.dict(sys.modules, {"fcntl": None, "msvcrt": msvcrt}):
            with gc.State("s1") as st:
                st["x"] = 1
        self.assertEqual(calls, [msvcrt.LK_NBLCK, msvcrt.LK_UNLCK])
        with gc.State("s1") as st:
            self.assertEqual(st["x"], 1)

    def test_windows_lock_retries_while_another_hook_holds_it(self):
        msvcrt, calls = self.fake_msvcrt(busy=3)
        with mock.patch.dict(sys.modules, {"fcntl": None, "msvcrt": msvcrt}), mock.patch.object(gc.time, "sleep"):
            with gc.State("s1") as st:
                st["x"] = 1
        self.assertEqual(calls, [msvcrt.LK_NBLCK] * 4 + [msvcrt.LK_UNLCK])

    def test_windows_lock_gives_up_at_the_deadline(self):
        msvcrt, calls = self.fake_msvcrt(busy=10 ** 6)
        clock = iter(range(0, 10 ** 6, 5))
        with mock.patch.dict(sys.modules, {"fcntl": None, "msvcrt": msvcrt}), \
                mock.patch.object(gc.time, "sleep"), mock.patch.object(gc.time, "time", lambda: next(clock)):
            with self.assertRaises(OSError):
                gc.State("s1").__enter__()
        self.assertNotIn(msvcrt.LK_LOCK, calls)
        self.assertLess(len(calls), 20)

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_git_metadata_strips_credentials(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t")
        for args in (["init", "-q", "-b", "main"], ["remote", "add", "origin", "https://user:tok@host/x.git"],
                     ["commit", "-q", "--allow-empty", "-m", "x"]):
            subprocess.run(["git", "-C", repo] + args, check=True, env=env, capture_output=True)
        meta = gc.git_meta(repo)
        self.assertEqual(meta["git_repo"], "https://host/x.git")
        self.assertEqual(meta["git_branch"], "main")
        self.assertEqual(len(meta["git_commit"]), 40)
        self.assertEqual(gc.git_meta(self.tmp), {})

    def test_mute_command_uses_the_given_data_dir(self):
        other = os.path.join(self.tmp, "other-data")
        out = io.StringIO()
        with mock.patch.object(os, "getcwd", return_value=self.tmp), contextlib.redirect_stdout(out):
            gc.main(["galtea_cc.py", "mute", other])
        self.assertIn("muted", out.getvalue())
        with open(os.path.join(other, "muted.json")) as f:
            self.assertEqual(json.load(f), [gc.norm(self.tmp)])

    @unittest.skipUnless(shutil.which("sh"), "needs sh")
    def test_run_sh_runs_the_script(self):
        env = {k: v for k, v in os.environ.items()}
        r = subprocess.run(["sh", os.path.join(PLUGIN, "scripts", "run.sh"), "unmute", self.data],
                           cwd=self.tmp, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0)
        self.assertIn("on again", r.stdout)

    @unittest.skipUnless(shutil.which("sh"), "needs sh")
    def test_run_sh_exits_0_without_python(self):
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        for tool in ("dirname", "uname"):
            path = shutil.which(tool)
            if path:
                os.symlink(path, os.path.join(bindir, tool))
        sh = shutil.which("sh")
        r = subprocess.run([sh, os.path.join(PLUGIN, "scripts", "run.sh"), "hook"], env={"PATH": bindir},
                           input="{}", capture_output=True, text=True, timeout=30)
        self.assertEqual((r.returncode, r.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
