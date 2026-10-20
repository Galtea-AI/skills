"""Tests for scripts/galtea_cc.py. Stdlib only, no network: `api` is replaced by a recorder.

Run:  python3 -m unittest discover plugins/galtea-tracing/tests -v
"""
import contextlib
import http.server
import io
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
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
    "yaml_field": ("db:\n  password: s3cr3tValue99\n", "s3cr3tValue99"),
}


class FakeApi:
    """Records every call; answers from `responses` keyed by (method, path prefix)."""

    def __init__(self, responses=None, fail=None):
        self.calls = []
        self.deadlines = []
        self.responses = responses or {}
        self.fail = fail or ()

    def __call__(self, cfg, method, path, body=None):
        # like the real api(): a call past its deadline is never sent
        if cfg.get("deadline") and cfg["deadline"] - gc.time.time() <= 0:
            raise gc.OutOfTime("%s %s -> out of time" % (method, path))
        self.calls.append((method, path, body))
        self.deadlines.append(cfg.get("deadline"))
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
                    "CLAUDE_PLUGIN_OPTION_PRODUCT_ID": "prod_1", "GALTEA_CC_API_URL": "http://127.0.0.1:9",
                    "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_CONFIG_DIR": self.tmp})
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        self.settings = os.path.join(self.tmp, "settings.json")
        p = mock.patch.object(gc, "api", self.fail_network)
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
        # Claude Code writes "\n" on every platform; text mode would write "\r\n" on Windows
        with open(path, "w", encoding="utf-8", newline="\n") as f:
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

    def run_hook(self, event, **kw):
        """Run a hook the way Claude Code does: through main(), which logs any error."""
        stdin = types.SimpleNamespace(buffer=io.BytesIO(json.dumps(self.hook(event, **kw)).encode()))
        with mock.patch.object(sys, "stdin", stdin), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gc.main(["galtea_cc.py", "hook"]), 0)


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
        del os.environ["GALTEA_CC_API_URL"]
        self.assertEqual(gc.load_config()["api_url"], "https://example.invalid")
        del os.environ["CLAUDE_PLUGIN_OPTION_API_URL"]
        self.assertEqual(gc.load_config()["api_url"], gc.DEFAULT_API_URL)

    def test_an_older_gsk_dash_key_is_accepted(self):
        os.environ["CLAUDE_PLUGIN_OPTION_API_KEY"] = "gsk-0123456789abcdef"
        self.assertIsNone(gc.load_config()["error"])
        os.environ["CLAUDE_PLUGIN_OPTION_API_KEY"] = "sk-0123456789abcdef"
        self.assertIn("not a Galtea API key", gc.load_config()["error"])

    def run_main(self, event, **kw):
        out = io.StringIO()
        stdin = types.SimpleNamespace(buffer=io.BytesIO(json.dumps(self.hook(event, **kw)).encode()))
        with mock.patch.object(sys, "stdin", stdin), contextlib.redirect_stdout(out):
            gc.main(["galtea_cc.py", "hook"])
        return out.getvalue()

    def test_a_malformed_key_never_reaches_the_log_or_the_message(self):
        head, tail = "gsk_" + "A1b2C3d4E5" * 3, "TailOfTheSecret987"
        for key in (head + "\n" + tail, head + " " + tail, "sk-" + tail + head[4:]):
            with self.subTest(key=repr(key)):
                os.environ["CLAUDE_PLUGIN_OPTION_API_KEY"] = key
                self.use_api(REAL_API)
                with mock.patch.object(gc._OPENER, "open", side_effect=AssertionError("network")):
                    out = self.run_main("SessionStart", source="startup")
                    for event in ("UserPromptSubmit", "Stop", "SessionEnd"):
                        self.run_main(event, prompt="hi")
                with open(os.path.join(self.data, "hook.log"), encoding="utf-8") as f:
                    log = f.read()
                self.assertIn("not a Galtea API key", json.loads(out)["systemMessage"])
                self.assertIn("not a Galtea API key", log)
                self.assertNotIn("network", log)
                for piece in (head, tail, head[4:]):
                    self.assertNotIn(piece, log)
                    self.assertNotIn(piece, out)

    def test_a_request_that_cannot_be_built_never_shows_the_key(self):
        head, tail = "gsk_" + "A1b2C3d4E5" * 3, "TailOfTheSecret987"
        cfg = dict(gc.load_config(), api_key=head + "\n" + tail)
        with self.assertRaises(Exception) as ctx:
            REAL_API(cfg, "GET", "/products/p")
        shown = "".join(traceback.format_exception(type(ctx.exception), ctx.exception, ctx.exception.__traceback__))
        self.assertIn("GET /products/p", shown)
        for piece in (head, tail):
            self.assertNotIn(piece, shown)

    def test_the_log_never_holds_the_configured_key(self):
        os.environ["CLAUDE_PLUGIN_OPTION_API_KEY"] = "gsk_" + "Q" * 30
        gc.log("echo of gsk_%s by a server" % ("Q" * 30))
        with open(os.path.join(self.data, "hook.log"), encoding="utf-8") as f:
            self.assertNotIn("Q" * 30, f.read())

    def test_stdout_never_holds_the_configured_key(self):
        os.environ["CLAUDE_PLUGIN_OPTION_API_KEY"] = "gsk_" + "Q" * 30
        with mock.patch.object(gc, "hook_main", return_value={"systemMessage": "server said gsk_" + "Q" * 30}):
            self.assertNotIn("Q" * 30, self.run_main("SessionStart"))

    def test_data_dir_falls_back_to_the_config_dir(self):
        del os.environ["CLAUDE_PLUGIN_DATA"]
        self.assertEqual(gc.data_dir(), os.path.join(self.tmp, "galtea-tracing"))
        del os.environ["CLAUDE_CONFIG_DIR"]
        self.assertEqual(gc.data_dir(), os.path.join(os.path.expanduser("~"), ".claude", "galtea-tracing"))

    def test_an_api_url_without_https_is_refused_except_on_this_machine(self):
        for url, refused in (("http://api.galtea.ai", True), ("http://10.0.0.5:8000", True), ("ftp://h/x", True),
                             ("https://", True), ("https://api.galtea.ai", False), ("http://localhost:8000", False),
                             ("http://127.0.0.1:9", False), ("http://[::1]:8000", False)):
            with self.subTest(url=url):
                os.environ["GALTEA_CC_API_URL"] = url
                error = gc.load_config()["error"]
                if refused:
                    self.assertIn("must start with https://", error)
                else:
                    self.assertIsNone(error)

    def test_a_product_id_with_a_space_or_line_break_is_refused(self):
        for product in ("prod 1", "prod\n1", "prod\t1"):
            with self.subTest(product=repr(product)):
                os.environ["CLAUDE_PLUGIN_OPTION_PRODUCT_ID"] = product
                self.assertIn("product id holds a space or a line break", gc.load_config()["error"])

    def test_only_the_plugin_host_variable_overrides_the_api_url_option(self):
        os.environ["CLAUDE_PLUGIN_OPTION_API_URL"] = "https://option.example"
        os.environ["GALTEA_CC_API_URL"] = "https://override.example"
        os.environ["GALTEA_API_URL"] = "https://sdk.example"
        self.assertEqual(gc.load_config()["api_url"], "https://override.example")
        del os.environ["GALTEA_CC_API_URL"]
        self.assertEqual(gc.load_config()["api_url"], "https://option.example")
        del os.environ["CLAUDE_PLUGIN_OPTION_API_URL"]
        self.assertEqual(gc.load_config()["api_url"], gc.DEFAULT_API_URL)

    def test_a_hook_never_fails_when_the_data_dir_cannot_be_written(self):
        blocker = os.path.join(self.tmp, "a-file")
        open(blocker, "w").close()
        os.environ["CLAUDE_PLUGIN_DATA"] = os.path.join(blocker, "data")
        fake = self.use_api(FakeApi(ok_responses()))
        path = self.turn()
        for event in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"):
            with self.subTest(event=event):
                out = io.StringIO()
                hook = self.hook(event, source="startup", prompt="hi", transcript_path=path,
                                 last_assistant_message="ok")
                stdin = types.SimpleNamespace(buffer=io.BytesIO(json.dumps(hook).encode()))
                with mock.patch.object(sys, "stdin", stdin), contextlib.redirect_stdout(out):
                    self.assertEqual(gc.main(["galtea_cc.py", "hook"]), 0)
                printed = out.getvalue()
                if event == "SessionStart":
                    self.assertIn("Galtea tracing is on", json.loads(printed)["systemMessage"])
                else:
                    self.assertEqual(printed, "")
        self.assertIn("/products/prod_1", fake.paths("GET"))
        self.assertEqual(fake.paths("POST"), ["/sessions", "/inferenceResults", "/traces/batch?includeRejected=true"])

    def test_the_log_rolls_over_past_its_size_limit(self):
        log = os.path.join(self.data, "hook.log")
        os.makedirs(self.data)
        with mock.patch.object(gc, "LOG_MAX_BYTES", 100):
            with open(log, "w") as f:
                f.write("o" * 100)
            gc.log("kept")
            self.assertFalse(os.path.exists(log + ".1"))
            gc.log("rolled")
        with open(log + ".1") as f:
            old = f.read()
        with open(log) as f:
            new = f.read()
        self.assertTrue(old.startswith("o" * 100) and old.rstrip().endswith(" kept"), old)
        self.assertEqual(len(new.splitlines()), 1)
        self.assertTrue(new.rstrip().endswith(" rolled"), new)

    def test_plugin_version_is_the_install_folder_without_a_manifest_version(self):
        for parent, version in (("galtea-tracing", "3f2b8c1e9a4d"), ("other-plugin", None)):
            with self.subTest(parent=parent):
                root = os.path.join(self.tmp, "cache", parent, "3f2b8c1e9a4d")
                os.makedirs(os.path.join(root, ".claude-plugin"))
                with open(os.path.join(root, ".claude-plugin", "plugin.json"), "w") as f:
                    json.dump({"name": "galtea-tracing"}, f)
                with mock.patch.object(gc, "PLUGIN_ROOT", root):
                    self.assertEqual(gc.plugin_version(), version)

    def test_a_manifest_version_wins_over_the_install_folder(self):
        root = os.path.join(self.tmp, "galtea-tracing", "3f2b8c1e9a4d")
        os.makedirs(os.path.join(root, ".claude-plugin"))
        with open(os.path.join(root, ".claude-plugin", "plugin.json"), "w") as f:
            json.dump({"version": "1.2.3"}, f)
        with mock.patch.object(gc, "PLUGIN_ROOT", root):
            self.assertEqual(gc.plugin_version(), "1.2.3")

    def thinking_setting(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"showThinkingSummaries": True}, f)

    def test_thinking_summaries_count_in_the_config_dir_and_the_project(self):
        # HOME is pinned so a setting in the real ~/.claude cannot make a case pass
        os.environ["HOME"] = os.path.join(self.tmp, "home")
        project = os.path.join(self.tmp, "project")
        self.assertFalse(gc.thinking_summaries_on(project))
        with open(self.settings, "w") as f:
            json.dump({"showThinkingSummaries": False}, f)
        self.assertFalse(gc.thinking_summaries_on(project))
        for path in (self.settings, os.path.join(project, ".claude", "settings.json"),
                     os.path.join(project, ".claude", "settings.local.json")):
            with self.subTest(path=path):
                self.thinking_setting(path)
                self.assertTrue(gc.thinking_summaries_on(project))
                os.remove(path)

    def test_the_thinking_note_names_the_settings_file_of_the_config_dir(self):
        os.environ["HOME"] = os.path.join(self.tmp, "home")
        project = os.path.join(self.tmp, "project")
        self.use_api(FakeApi(ok_responses()))
        ok, msg, _ = gc.run_check(gc.load_config(), project)
        self.assertTrue(ok)
        self.assertIn('add "showThinkingSummaries": true to %s.' % os.path.join(self.tmp, "settings.json"), msg)
        self.thinking_setting(os.path.join(project, ".claude", "settings.local.json"))
        self.assertNotIn("showThinkingSummaries", gc.run_check(gc.load_config(), project)[1])


class TestApi(Base):
    class Answer(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def call(self, answer=b"", method="POST", path="/sessions", body=None, **cfg):
        cfg = dict(gc.load_config(), **cfg)
        with mock.patch.object(gc._OPENER, "open", return_value=self.Answer(answer)) as opened:
            res = REAL_API(cfg, method, path, body)
        return res, opened

    def test_the_timeout_is_the_time_left_before_the_deadline(self):
        with mock.patch.object(gc.time, "time", lambda: 1000.0):
            _, opened = self.call(deadline=1012.5)
        self.assertEqual(opened.call_args.kwargs["timeout"], 12.5)
        _, opened = self.call()
        self.assertEqual(opened.call_args.kwargs["timeout"], 30)

    def test_a_spent_deadline_raises_without_a_request(self):
        for deadline in (1000.0, 999.0):
            with self.subTest(deadline=deadline):
                with mock.patch.object(gc.time, "time", lambda: 1000.0), \
                        mock.patch.object(gc._OPENER, "open") as opened:
                    with self.assertRaises(gc.OutOfTime):
                        REAL_API(dict(gc.load_config(), deadline=deadline), "GET", "/products/p")
                opened.assert_not_called()

    def test_the_request_carries_the_url_key_method_and_json_body(self):
        _, opened = self.call(api_url="https://h/x/", method="PATCH", path="/sessions", body={"a": "é", "n": 1})
        req = opened.call_args.args[0]
        self.assertEqual(req.full_url, "https://h/x/sessions")
        self.assertEqual(req.get_header("Authorization"), "Bearer gsk_test_key")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(req.get_method(), "PATCH")
        self.assertEqual(json.loads(req.data.decode("utf-8")), {"a": "é", "n": 1})
        _, opened = self.call(method="DELETE", path="/sessions/s")
        self.assertIsNone(opened.call_args.args[0].data)

    def test_a_json_answer_is_parsed_and_an_empty_answer_is_an_empty_dict(self):
        self.assertEqual(self.call(b'{"id": "sess_1"}')[0], {"id": "sess_1"})
        self.assertEqual(self.call(b"")[0], {})

    def test_a_lone_surrogate_is_sent_as_a_question_mark(self):
        _, opened = self.call(body={"input": {"user_message": "x \ud83d"}})
        sent = opened.call_args.args[0].data.decode("utf-8")
        self.assertEqual(json.loads(sent), {"input": {"user_message": "x ?"}})

    def serve(self, handler):
        server = http.server.HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return "http://127.0.0.1:%d" % server.server_address[1]

    def test_a_redirect_is_never_followed(self):
        seen = {"a": [], "b": []}

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

        class B(Handler):
            def do_GET(self):
                seen["b"].append(self.headers.get("Authorization"))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        b_url = self.serve(B)

        class A(Handler):
            def do_GET(self):
                seen["a"].append(self.headers.get("Authorization"))
                self.send_response(302)
                self.send_header("Location", b_url + "/login")
                self.send_header("Content-Length", "0")
                self.end_headers()

        os.environ["no_proxy"] = "*"  # a proxy from the environment must not take the request
        with self.assertRaises(gc.ApiError) as ctx:
            REAL_API(dict(gc.load_config(), api_url=self.serve(A)), "GET", "/products/p")
        self.assertEqual(ctx.exception.status, 302)
        self.assertEqual(seen, {"a": ["Bearer gsk_test_key"], "b": []})


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

    def test_a_turn_stops_at_the_next_prompt(self):
        def step(pid, n, second):
            return [
                {"type": "user", "promptId": pid, "timestamp": "2026-01-01T00:00:%02dZ" % second,
                 "message": {"content": "prompt %s" % pid}},
                {"type": "assistant", "timestamp": "2026-01-01T00:00:%02dZ" % (second + 1), "message": {
                    "id": "m" + pid, "model": "claude", "usage": {"input_tokens": n, "output_tokens": n},
                    "content": [{"type": "tool_use", "id": "t" + pid, "name": "Bash", "input": {"command": pid}}]}},
                {"type": "user", "promptId": pid, "timestamp": "2026-01-01T00:00:%02dZ" % (second + 2),
                 "message": {"content": [{"type": "tool_result", "tool_use_id": "t" + pid, "content": "ok"}]}},
            ]
        path = self.transcript(step("p1", 10, 0) + step("p2", 99, 10))
        fake = self.run_turn(path)
        traces = fake.body("POST", "/traces/batch")["traces"]
        self.assertEqual([t["name"] for t in traces], ["llm:claude", "tool:Bash"])
        self.assertEqual(next(t for t in traces if t["type"] == "TOOL")["inputData"], {"input": {"command": "p1"}})
        self.assertEqual(fake.body("PATCH", "/inferenceResults/ir_1"),
                         {"inputTokens": 10, "outputTokens": 10, "cacheReadInputTokens": 0})

    def hook_log(self):
        with open(os.path.join(self.data, "hook.log"), encoding="utf-8") as f:
            return f.read()

    def test_lost_spans_are_logged_without_content(self):
        agent = [
            {"type": "user", "promptId": "p1", "message": {"content": "SECRET_PROMPT_TEXT"}},
            {"type": "assistant", "message": {"id": "m1", "content": [
                {"type": "tool_use", "id": "t1", "name": "Agent", "input": {"prompt": "SECRET_AGENT_INPUT"}}]}},
            {"type": "user", "promptId": "p1", "toolUseResult": {"agentId": "a1"},
             "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "SECRET_RESULT"}]}},
        ]
        cases = {
            "no transcript path": lambda: None,
            "transcript file missing": lambda: os.path.join(self.tmp, "gone.jsonl"),
            "no transcript lines match": lambda: self.transcript([{"type": "assistant", "message": {"content": []}}]),
            "subagent transcript agent-a1.jsonl missing": lambda: self.transcript(agent),
        }
        for expected, make in cases.items():
            with self.subTest(expected=expected):
                path = make()
                stop = {} if path is None else {"transcript_path": path}
                self.use_api(FakeApi(ok_responses()))
                with mock.patch.object(gc.time, "sleep"):
                    self.run_hook("UserPromptSubmit", prompt="SECRET_PROMPT_TEXT")
                    self.run_hook("Stop", last_assistant_message="ok", **stop)
                log = self.hook_log()
                self.assertIn(expected, log)
                self.assertNotIn("SECRET_", log)

    def test_an_unknown_prompt_id_is_logged(self):
        path = self.transcript([{"type": "user", "promptId": "other", "message": {"content": "hi"}}])
        self.run_turn(path)
        self.assertIn("prompt p1 is not in the transcript", self.hook_log())

    def test_session_is_production_with_metadata(self):
        fake = self.run_turn(self.turn())
        body = fake.body("POST", "/sessions")
        self.assertTrue(body["isProduction"])
        self.assertEqual(body["productId"], "prod_1")
        self.assertEqual(fake.body("POST", "/inferenceResults")["index"], 0)

    def test_an_interrupted_first_prompt_creates_no_session(self):
        # Esc, then /exit or /clear: the prompt never gets a Stop
        fake = self.use_api(FakeApi(ok_responses()))
        self.run_hook("UserPromptSubmit", prompt="hi")
        self.run_hook("SessionEnd", reason="prompt_input_exit")
        self.assertEqual(fake.calls, [])

    def test_a_refused_first_turn_leaves_no_empty_session(self):
        err = gc.ApiError("POST", "/inferenceResults", 400, b"bad")
        fake = self.use_api(FakeApi(ok_responses(), fail=[("POST", "/inferenceResults", err)]))
        self.run_hook("UserPromptSubmit", prompt="hi")
        self.run_hook("Stop", last_assistant_message="ok")
        self.assertEqual(fake.paths("DELETE"), ["/sessions/sess_1"])
        fake.fail = ()
        self.run_hook("UserPromptSubmit", prompt="again")
        self.run_hook("Stop", last_assistant_message="ok")
        self.assertEqual(fake.paths("POST").count("/sessions"), 2)
        self.assertEqual([b["index"] for m, p, b in fake.calls if p == "/inferenceResults"], [0, 0])

    def test_a_refused_first_turn_keeps_a_session_another_turn_uses(self):
        def refuse(body):
            with gc.State("s1") as st:  # a concurrent Stop took the next index meanwhile
                st["index"] += 1
            raise gc.ApiError("POST", "/inferenceResults", 400, b"bad")
        responses = ok_responses()
        responses[("POST", "/inferenceResults")] = refuse
        fake = self.use_api(FakeApi(responses))
        self.run_hook("UserPromptSubmit", prompt="hi")
        self.run_hook("Stop", last_assistant_message="ok")
        self.assertEqual(fake.paths("DELETE"), [])

    def test_a_refused_later_turn_keeps_the_session(self):
        fake = self.run_turn(self.turn())
        fake.fail = [("POST", "/inferenceResults", gc.ApiError("POST", "/inferenceResults", 400, b"bad"))]
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt="again")
            self.run_hook("Stop", last_assistant_message="ok")
        self.assertEqual(fake.paths("DELETE"), [])

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

    def test_a_long_prompt_and_reply_fit_the_api_body_limit(self):
        fake = self.use_api(FakeApi(ok_responses()))
        big = "\U0001F600" * 150000  # 600 KB of 4-byte characters, 12 bytes each when ASCII-escaped
        self.run_hook("UserPromptSubmit", prompt=big)
        self.run_hook("Stop", last_assistant_message=big)
        body = fake.body("POST", "/inferenceResults")
        for field in (body["input"]["user_message"], body["actualOutput"]):
            self.assertIn("[truncated", field)
            self.assertLessEqual(len(field.encode()), 100000 + 40)
        sent = {}

        def capture(req, timeout):
            sent["data"] = req.data
            raise urllib.error.URLError("captured")
        with mock.patch.object(gc._OPENER, "open", side_effect=capture), self.assertRaises(OSError):
            REAL_API(gc.load_config(), "POST", "/inferenceResults", body)
        self.assertLess(len(sent["data"]), 512 * 1024)

    def test_a_session_deleted_in_galtea_is_created_again(self):
        ids = iter(["sess_1", "sess_2"])
        seen = []

        def turn(body):
            seen.append((body["sessionId"], body["index"]))
            if len(seen) == 2:  # the session was deleted after the first turn
                raise gc.ApiError("POST", "/inferenceResults", 404, b"session not found")
            return {"id": "ir_%d" % len(seen)}
        responses = ok_responses()
        responses[("POST", "/sessions")] = lambda body: {"id": next(ids)}
        responses[("POST", "/inferenceResults")] = turn
        fake = self.use_api(FakeApi(responses))
        for prompt in ("one", "two", "three"):
            self.run_hook("UserPromptSubmit", prompt=prompt)
            self.run_hook("Stop", last_assistant_message=prompt)
        self.assertEqual(seen, [("sess_1", 0), ("sess_1", 1), ("sess_2", 0), ("sess_2", 1)])
        self.assertEqual(fake.paths("POST").count("/sessions"), 2)
        self.assertEqual(fake.paths("DELETE"), [])

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
        self.run_hook("UserPromptSubmit", prompt="hi")
        stdin = types.SimpleNamespace(buffer=io.BytesIO(json.dumps(self.hook("Stop")).encode()))
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", stdin), contextlib.redirect_stdout(out):
            self.assertEqual(gc.main(["galtea_cc.py", "hook"]), 0)
        self.assertEqual(out.getvalue(), "")
        with open(os.path.join(self.data, "hook.log"), encoding="utf-8") as f:
            self.assertIn("POST /sessions -> 500", f.read())


class TestMute(Base):
    def setUp(self):
        super().setUp()
        self.addCleanup(os.chdir, os.getcwd())

    def command(self, name, cwd):
        os.chdir(cwd)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(gc.main(["galtea_cc.py", name, self.data]), 0)
        return out.getvalue()

    def git_repo(self, path):
        os.makedirs(path, exist_ok=True)
        subprocess.run(["git", "init", "-q", path], check=True, capture_output=True)
        return path

    def muted_list(self):
        with open(os.path.join(self.data, "muted.json"), encoding="utf-8") as f:
            return json.load(f)

    def sent_turn(self):
        fake = self.use_api(FakeApi(ok_responses()))
        path = self.turn()
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt="hi")
            self.run_hook("Stop", transcript_path=path, last_assistant_message="done")
        self.assertIn("/inferenceResults", fake.paths("POST"))
        return fake, path

    def test_a_muted_prompt_is_not_stored(self):
        gc.set_muted(self.tmp, True)
        self.run_hook("UserPromptSubmit", prompt="MUTED_TEXT")
        state = os.path.join(self.data, "state", "s1.json")
        if os.path.exists(state):
            with open(state, encoding="utf-8") as f:
                self.assertNotIn("MUTED_TEXT", f.read())

    def test_a_muted_stop_leaves_no_turn_in_flight(self):
        fake, path = self.sent_turn()
        self.run_hook("UserPromptSubmit", prompt="/galtea-tracing:mute", prompt_id="p2")
        gc.set_muted(self.tmp, True)
        self.run_hook("Stop", prompt_id="p2", transcript_path=path, last_assistant_message="muted")
        with gc.State("s1") as st:
            self.assertEqual(st.get("inflight", {}), {})
        self.run_hook("SessionEnd", reason="prompt_input_exit")
        self.assertIn("/sessions/sess_1/finish", fake.paths("PATCH"))

    def test_session_end_in_a_muted_repo_sends_no_title(self):
        fake, path = self.sent_turn()
        gc.set_muted(self.tmp, True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "custom-title", "customTitle": "secret plan"}) + "\n")
        self.run_hook("SessionEnd", transcript_path=path, reason="prompt_input_exit")
        self.assertNotIn("/sessions/sess_1", fake.paths("PATCH"))
        self.assertNotIn("secret plan", json.dumps(fake.calls))

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_mute_from_a_subdirectory_mutes_the_repository(self):
        repo = self.git_repo(os.path.join(self.tmp, "repo"))
        for sub in ("a", "b"):
            os.makedirs(os.path.join(repo, sub))
        self.command("mute", os.path.join(repo, "a"))
        self.assertEqual(self.muted_list(), [gc.norm(repo)])
        self.assertTrue(gc.is_muted(repo))
        self.assertTrue(gc.is_muted(os.path.join(repo, "b")))

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_the_state_file_is_owner_only(self):
        self.addCleanup(os.umask, os.umask(0o022))
        self.run_hook("UserPromptSubmit", prompt="hi")
        mode = os.stat(os.path.join(self.data, "state", "s1.json")).st_mode
        self.assertEqual(stat.S_IMODE(mode), 0o600)

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_unmute_under_a_muted_parent_says_it_is_still_muted(self):
        work = os.path.join(self.tmp, "work")
        proj = self.git_repo(os.path.join(work, "proj"))
        self.command("mute", work)
        out = self.command("unmute", proj)
        self.assertIn("still muted for %s, by the mute of %s" % (gc.norm(proj), gc.norm(work)), out)
        self.assertNotIn("on again", out)
        self.assertTrue(gc.is_muted(proj))

    def write_muted(self, text):
        os.makedirs(self.data, exist_ok=True)
        with open(os.path.join(self.data, "muted.json"), "w", encoding="utf-8") as f:
            f.write(text)

    def test_an_unreadable_mute_list_mutes_every_folder(self):
        for text in ('["/x", ', '{"a": 1}'):
            with self.subTest(text=text):
                shutil.rmtree(self.data, True)
                self.write_muted(text)
                self.assertTrue(gc.is_muted(self.tmp))
                self.assertTrue(gc.is_muted(os.path.join(self.tmp, "elsewhere")))
                with open(os.path.join(self.data, "hook.log"), encoding="utf-8") as f:
                    self.assertIn("muted.json cannot be read", f.read())

    def test_unmute_with_an_unreadable_mute_list_says_it_cannot_be_read(self):
        self.write_muted('["/x", ')
        out = self.command("unmute", self.tmp)
        self.assertIn("muted.json cannot be read", out)
        self.assertTrue(gc.is_muted(self.tmp))

    def test_mute_and_unmute_leave_only_the_mute_list(self):
        self.command("mute", self.tmp)
        self.command("unmute", self.tmp)
        self.assertEqual(os.listdir(self.data), ["muted.json"])
        self.assertEqual(self.muted_list(), [])

    def test_a_failed_write_keeps_the_previous_mute_list(self):
        a, b = os.path.join(self.tmp, "a"), os.path.join(self.tmp, "b")
        gc.set_muted(a, True)
        with mock.patch.object(gc.json, "dump", side_effect=OSError("disk full")), self.assertRaises(OSError):
            gc.set_muted(b, True)
        self.assertEqual(self.muted_list(), [gc.norm(a)])
        self.assertEqual(os.listdir(self.data), ["muted.json"])


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

    def test_self_test_deletes_a_session_whose_create_timed_out(self):
        # the server can create the session and answer after the client gave up
        def lookup(body):
            custom_id = fake.body("POST", "/sessions")["customId"]
            return [{"id": "sess_other", "customId": "someone-else"}, {"id": "sess_late", "customId": custom_id}]
        responses = ok_responses()
        responses[("GET", "/sessions")] = lookup
        fake = self.use_api(FakeApi(responses, fail=[("POST", "/sessions", socket.timeout("timed out"))]))
        self.assertIn("self-test got no answer from Galtea", self.start()["systemMessage"])
        self.assertEqual(fake.paths("DELETE"), ["/sessions/sess_late"])

    def test_a_failed_cleanup_fails_the_check(self):
        fake = self.use_api(FakeApi(ok_responses(), fail=[("DELETE", "/sessions/")]))
        msg = self.start()["systemMessage"]
        self.assertIn("could not clean up its development session sess_1", msg)
        self.assertEqual(fake.paths("DELETE"), ["/sessions/sess_1"])
        self.assertFalse(os.path.exists(os.path.join(self.data, "checked")))

    def test_cleanup_ends_inside_the_hook_timeout(self):
        # the SessionStart hook is killed at 30 s, so the cleanup gets only what is left of the budget
        for spent, deletes in ((gc.CHECK_BUDGET + 4, 1), (30, 0)):
            with self.subTest(spent=spent):
                shutil.rmtree(self.data, True)
                clock = [1000.0]

                def slow(body):
                    clock[0] = 1000.0 + spent  # the steps used up the time
                    return [{"id": "a"}, {"id": "b"}, {"id": "c"}]
                responses = ok_responses()
                responses[("GET", "/traces")] = slow
                fake = self.use_api(FakeApi(responses))
                with mock.patch.object(gc.time, "time", lambda: clock[0]):
                    msg = self.start()["systemMessage"]
                deadlines = [d for (m, p, b), d in zip(fake.calls, fake.deadlines) if m == "DELETE"]
                self.assertEqual(len(deadlines), deletes)
                for d in deadlines:
                    self.assertTrue(clock[0] < d <= 1000.0 + 28, d)
                if not deletes:
                    self.assertIn("could not clean up", msg)
                    self.assertFalse(os.path.exists(os.path.join(self.data, "checked")))

    def test_the_lookup_after_a_create_timeout_ends_inside_the_hook_timeout(self):
        clock = [1000.0]

        def late_create(body):
            clock[0] = 1000.0 + gc.CHECK_BUDGET + 4
            raise socket.timeout("timed out")
        responses = ok_responses()
        responses[("POST", "/sessions")] = late_create
        responses[("GET", "/sessions")] = []
        fake = self.use_api(FakeApi(responses))
        with mock.patch.object(gc.time, "time", lambda: clock[0]):
            self.start()
        lookup = [d for (m, p, b), d in zip(fake.calls, fake.deadlines) if (m, p[:10]) == ("GET", "/sessions?")]
        self.assertEqual(len(lookup), 1)
        self.assertTrue(clock[0] < lookup[0] <= 1000.0 + 28, lookup[0])

    def test_a_failed_lookup_after_a_create_timeout_fails_the_check(self):
        fake = self.use_api(FakeApi(ok_responses(), fail=[("POST", "/sessions", socket.timeout("timed out")),
                                                          ("GET", "/sessions")]))
        msg = self.start()["systemMessage"]
        self.assertIn("could not clean up its development session galtea-cc-selftest-", msg)
        self.assertTrue(any(p.startswith("/sessions?") for p in fake.paths("GET")))
        self.assertEqual(fake.paths("DELETE"), [])
        self.assertFalse(os.path.exists(os.path.join(self.data, "checked")))

    def test_no_lookup_is_sent_after_the_cleanup_deadline(self):
        clock = [1000.0]
        sent = []

        def answer(req, timeout):
            path = req.full_url[len("http://127.0.0.1:9"):]
            sent.append((req.get_method(), path))
            if (req.get_method(), path) == ("POST", "/sessions"):
                clock[0] = 1000.0 + gc.CHECK_BUDGET + gc.CLEANUP_BUDGET + 1
                raise socket.timeout("timed out")
            return io.BytesIO(json.dumps({"name": "P", "autoCloseScope": "ALL"}).encode())
        self.use_api(REAL_API)
        with mock.patch.object(gc._OPENER, "open", side_effect=answer), \
                mock.patch.object(gc.time, "time", lambda: clock[0]):
            msg = self.start()["systemMessage"]
        self.assertEqual(sent, [("GET", "/products/prod_1"), ("POST", "/sessions")])
        self.assertIn("could not clean up its development session galtea-cc-selftest-", msg)

    def test_self_test_fails_on_too_few_traces(self):
        responses = ok_responses()
        responses[("GET", "/traces")] = [{"id": "a"}]
        fake = self.use_api(FakeApi(responses))
        self.assertIn("traces=1", self.start()["systemMessage"])
        self.assertEqual(fake.paths("DELETE"), ["/sessions/sess_1"])

    def test_self_test_fails_when_the_turn_is_not_stored(self):
        responses = ok_responses()
        responses[("GET", "/inferenceResults")] = []
        fake = self.use_api(FakeApi(responses))
        msg = self.start()["systemMessage"]
        self.assertIn("self-test failed", msg)
        self.assertIn("turns=0", msg)
        self.assertFalse(os.path.exists(os.path.join(self.data, "checked")))
        self.assertEqual(fake.paths("DELETE"), ["/sessions/sess_1"])

    def test_a_self_test_timeout_waits_before_the_next_check(self):
        marker = os.path.join(self.data, "unreachable")
        for err in (socket.timeout("timed out"), gc.OutOfTime("POST /inferenceResults -> out of time")):
            with self.subTest(err=repr(err)):
                shutil.rmtree(self.data, True)
                clock = [1000.0]
                fake = self.use_api(FakeApi(ok_responses(), fail=[("POST", "/inferenceResults", err)]))
                with mock.patch.object(gc.time, "time", lambda: clock[0]):
                    self.assertIn("self-test got no answer from Galtea", self.start()["systemMessage"])
                    self.assertEqual(fake.paths("DELETE"), ["/sessions/sess_1"])
                    self.assertFalse(os.path.exists(os.path.join(self.data, "checked")))
                    os.utime(marker, (1000.0, 1000.0))
                    n = len(fake.calls)
                    clock[0] = 1000.0 + gc.UNREACHABLE_RETRY - 1
                    self.assertIsNone(self.start())
                    self.assertEqual(len(fake.calls), n)
                    clock[0] = 1000.0 + gc.UNREACHABLE_RETRY + 1
                    self.assertIn("self-test got no answer from Galtea", self.start()["systemMessage"])
                    self.assertEqual(fake.paths("GET").count("/products/prod_1"), 2)

    def test_check_runs_once_per_config(self):
        responses = ok_responses()
        responses[("GET", "/products/")] = {"autoCloseScope": "ALL"}  # no name: the message shows the id
        fake = self.use_api(FakeApi(responses))
        self.assertIn("Galtea tracing is on. Sessions go to product prod_1.", self.start()["systemMessage"])
        n = len(fake.calls)
        self.assertIsNone(self.start())
        self.assertEqual(len(fake.calls), n)
        os.environ["CLAUDE_PLUGIN_OPTION_PRODUCT_ID"] = "prod_2"
        self.assertIn("Galtea tracing is on. Sessions go to product prod_2.", self.start()["systemMessage"])
        os.environ["GALTEA_CC_API_URL"] = "http://127.0.0.1:9/"
        self.assertIsNone(self.start())
        os.environ["GALTEA_CC_API_URL"] = "http://127.0.0.1:8"
        self.assertIn("Galtea tracing is on. Sessions go to product prod_2.", self.start()["systemMessage"])
        self.assertEqual(fake.paths("GET").count("/products/prod_2"), 2)

    def test_the_product_id_is_quoted_in_the_path(self):
        os.environ["CLAUDE_PLUGIN_OPTION_PRODUCT_ID"] = "a/b#c"
        fake = self.use_api(FakeApi(ok_responses()))
        self.start()
        self.assertEqual(fake.paths("GET")[0], "/products/a%2Fb%23c")

    def test_a_product_id_with_a_space_is_refused(self):
        os.environ["CLAUDE_PLUGIN_OPTION_PRODUCT_ID"] = "prod 1"
        fake = self.use_api(FakeApi(ok_responses()))
        self.assertIn("the product id holds a space", self.start()["systemMessage"])
        self.assertEqual(fake.calls, [])

    def test_check_skips_clear_compact_and_fork(self):
        fake = self.use_api(FakeApi(ok_responses()))
        for source in ("clear", "compact", "fork"):
            with self.subTest(source=source):
                self.assertIsNone(gc.hook_main(json.dumps(self.hook("SessionStart", source=source))))
        self.assertEqual(fake.calls, [])
        out = gc.hook_main(json.dumps(self.hook("SessionStart", source="resume")))
        self.assertIn("Galtea tracing is on", out["systemMessage"])

    def test_refused_key_reports_and_retries(self):
        for status in (401, 403, 404):
            with self.subTest(status=status):
                err = gc.ApiError("GET", "/products/prod_1", status, b"no")
                fake = self.use_api(FakeApi(fail=[("GET", "/products/", err)]))
                self.assertIn("refused", self.start()["systemMessage"])
                self.assertEqual(fake.paths("POST"), [])
                self.assertIn("refused", self.start()["systemMessage"])

    def test_api_keeps_the_http_status(self):
        err = urllib.error.HTTPError("http://x/products/p", 401, "Unauthorized", {}, io.BytesIO(b"bad key"))
        with mock.patch.object(gc._OPENER, "open", side_effect=err):
            with self.assertRaises(gc.ApiError) as ctx:
                REAL_API(gc.load_config(), "GET", "/products/p")
        self.assertEqual(ctx.exception.status, 401)

    def test_a_refused_key_in_any_hook_runs_the_check_again(self):
        marker = os.path.join(self.data, "checked")
        for status, kept in ((401, False), (403, False), (404, True), (500, True)):
            with self.subTest(status=status):
                os.makedirs(self.data, exist_ok=True)
                with open(marker, "w") as f:
                    f.write(gc.fingerprint(gc.load_config()))
                self.use_api(REAL_API)
                err = urllib.error.HTTPError("http://x/sessions", status, "no", {}, io.BytesIO(b"no"))
                with mock.patch.object(gc._OPENER, "open", side_effect=err):
                    self.run_hook("UserPromptSubmit", prompt="hi")
                    self.run_hook("Stop", last_assistant_message="ok")
                self.assertEqual(os.path.exists(marker), kept)

    def test_unreachable_galtea_is_not_called_refused(self):
        for err in (urllib.error.URLError("Name or service not known"), socket.timeout("timed out")):
            with self.subTest(err=repr(err)):
                shutil.rmtree(self.data, True)
                fake = self.use_api(FakeApi(fail=[("GET", "/products/", err)]))
                msg = self.start()["systemMessage"]
                self.assertNotIn("refused", msg)
                self.assertIn("could not reach Galtea", msg)
                self.assertIn("after 10 minutes", msg)
                self.assertFalse(os.path.exists(os.path.join(self.data, "checked")))
                # the next start within 10 minutes is not stalled by the same host again
                self.assertIsNone(self.start())
                self.assertEqual(len(fake.calls), 1)
                later = time.time() + 601
                with mock.patch.object(gc.time, "time", lambda: later):
                    self.assertIn("could not reach Galtea", self.start()["systemMessage"])
                self.assertEqual(len(fake.calls), 2)

    def test_an_error_from_galtea_is_not_called_unreachable(self):
        err = gc.ApiError("GET", "/products/prod_1", 502, b"bad gateway")
        fake = self.use_api(FakeApi(fail=[("GET", "/products/", err)]))
        msg = self.start()["systemMessage"]
        self.assertIn("got an error from Galtea", msg)
        self.assertNotIn("could not reach", msg)
        self.assertIn("got an error from Galtea", self.start()["systemMessage"])
        self.assertEqual(len(fake.calls), 2)

    def test_warns_when_a_closed_session_drops_new_turns(self):
        for policy, verb in (("IGNORE", "drops"), ("REJECT", "refuses"), ("REOPEN_AND_REEVALUATE", None)):
            with self.subTest(policy=policy):
                shutil.rmtree(self.data, True)
                responses = ok_responses()
                responses[("GET", "/products/")] = dict(responses[("GET", "/products/")],
                                                        closedSessionInferenceCreationPolicy=policy)
                self.use_api(FakeApi(responses))
                msg = self.start()["systemMessage"]
                if verb:
                    self.assertIn("This product %s new turns on a closed session "
                                  "(closedSessionInferenceCreationPolicy=%s)" % (verb, policy), msg)
                else:
                    self.assertNotIn("closedSessionInferenceCreationPolicy", msg)

    def test_warns_on_auto_close_and_thinking_summaries(self):
        for scope, warned in (("NONE", True), ("DEVELOPMENT", True), ("ALL", False)):
            with self.subTest(scope=scope):
                shutil.rmtree(self.data, True)
                responses = ok_responses()
                responses[("GET", "/products/")] = {"name": "P", "autoCloseScope": scope,
                                                    "autoCloseInactivityMinutes": 30}
                self.use_api(FakeApi(responses))
                msg = self.start()["systemMessage"]
                self.assertEqual("autoCloseScope=%s" % scope in msg, warned)
                self.assertEqual("closes after 30 minutes without activity" in msg, not warned)
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

    def test_only_a_startup_prunes_state(self):
        now = 2000000000.0
        old = os.path.join(self.data, "state", "old.json")
        os.makedirs(os.path.dirname(old))
        open(old, "w").close()
        os.utime(old, (now - gc.STATE_MAX_AGE - 60,) * 2)
        self.use_api(FakeApi(ok_responses()))
        with mock.patch.object(gc.time, "time", lambda: now):
            for source in ("resume", "clear"):
                gc.hook_main(json.dumps(self.hook("SessionStart", source=source)))
                self.assertTrue(os.path.exists(old), source)
            gc.hook_main(json.dumps(self.hook("SessionStart", source="startup")))
        self.assertFalse(os.path.exists(old))

    def test_a_hook_without_a_session_id_does_nothing(self):
        fake = self.use_api(FakeApi(ok_responses()))
        for event in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"):
            with self.subTest(event=event):
                hook = self.hook(event, source="startup", prompt="hi", last_assistant_message="ok")
                del hook["session_id"]
                self.assertIsNone(gc.hook_main(json.dumps(hook)))
        self.assertEqual(fake.calls, [])
        self.assertFalse(os.path.exists(os.path.join(self.data, "state")))


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
                     "create(max_tokens=100000, model=x)", '{"max_tokens": "100000"}',
                     # a value that names a variable is code, not a secret
                     "create(model=m, max_tokens=max_tokens)", "login(user, password=password)",
                     "self.access_token = access_token", "if token==expected_token:",
                     "api_key=settings.API_KEY", "password = get_password(user)", "max_tokens=100000.",
                     # the keyword inside a longer word is not a secret name
                     '{"tokenizer": "cl100k_base"}', "TOKENIZER_PATH=/models/cl100k_base",
                     "tokenizer: cl100k_base", "max_tokens: 4096", "token: true", "password:"):
            with self.subTest(text=text):
                self.assertEqual(gc.redact(text), text)
        self.assertEqual(gc.redact({"max_tokens": "100000"}), {"max_tokens": "100000"})
        self.assertEqual(gc.redact({"tokenizer": "cl100k_base"}), {"tokenizer": "cl100k_base"})

    def test_values_that_look_like_secrets_are_still_redacted(self):
        r = gc.REDACTED
        for text, out in (("f(password=hunter2pass, user=bob)", "f(password=%s, user=bob)" % r),
                          ("connect(token=abcdef123456)", "connect(token=%s)" % r),
                          ("PASSWORD=hunter2pass.", "PASSWORD=%s." % r),
                          ("PASSWORD=ab;cdefgh", "PASSWORD=%s" % r),
                          ("PASSWORD=MyPassword123!", "PASSWORD=%s" % r),
                          ("PASSWORD=Password123", "PASSWORD=%s" % r),
                          ('password="password"', 'password="%s"' % r),
                          ("secretKey=abcdef123456", "secretKey=%s" % r),
                          ('{"accessToken": "abcdef123456"}', '{"accessToken": "%s"}' % r),
                          ("GITHUB_TOKENS=abcdef123456", "GITHUB_TOKENS=%s" % r)):
            with self.subTest(text=text):
                self.assertEqual(gc.redact(text), out)
        self.assertEqual(gc.redact({"clientSecret": "abcdef123456"}), {"clientSecret": r})

    def test_common_secret_forms_are_redacted(self):
        r = gc.REDACTED
        for text, out in (("PASSWORD=supersecret", "PASSWORD=%s" % r),
                          ("DB_PASSWORD=mypassword123", "DB_PASSWORD=%s" % r),
                          ("export API_KEY=sk_live_token_abc123", "export API_KEY=%s" % r),
                          ("password: s3cr3tValue99", "password: %s" % r),
                          ("db:\n  api_key: abcdef123456xyz\n  host: x", "db:\n  api_key: %s\n  host: x" % r),
                          ("- token: 'abc def ghi'", "- token: '%s'" % r),
                          ("{'api_key': 'abcdef123456'}", "{'api_key': '%s'}" % r),
                          ('PASSWORD="correct horse battery" next', 'PASSWORD="%s" next' % r),
                          ('PASSWORD="cut off value\nnext', 'PASSWORD="%s\nnext' % r),
                          ("key gsk-0123456789abcdefABCDEF", "key %s" % r)):
            with self.subTest(text=text):
                self.assertEqual(gc.redact(text), out)

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


def at(second):
    return "2026-01-01T00:00:%06.3fZ" % second


def prompt_line(pid, content="hi", second=0):
    return {"type": "user", "promptId": pid, "timestamp": at(second), "message": {"content": content}}


def tool_call(mid, tid, name="Bash", tool_input=None, second=1):
    return {"type": "assistant", "timestamp": at(second), "message": {
        "id": mid, "model": "claude", "usage": {"input_tokens": 1},
        "content": [{"type": "tool_use", "id": tid, "name": name, "input": tool_input or {"command": "ls"}}]}}


def tool_result(tid, content="ok", second=2, agent_id=None, **block):
    line = {"type": "user", "timestamp": at(second), "message": {"content": [
        dict({"type": "tool_result", "tool_use_id": tid, "content": content}, **block)]}}
    if agent_id:
        line["toolUseResult"] = {"agentId": agent_id}
    return line


def reply_line(mid, text="done", second=3):
    return {"type": "assistant", "timestamp": at(second), "message": {
        "id": mid, "model": "claude", "usage": {"input_tokens": 1}, "content": [{"type": "text", "text": text}]}}


def stop_feedback(second):
    return {"type": "user", "promptId": "p1", "isMeta": True, "timestamp": at(second),
            "message": {"content": "Stop hook feedback"}}


def read_log(data):
    path = os.path.join(data, "hook.log")
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestTraces(Base):
    """The transcript of one turn becomes its spans."""

    def send(self, lines, subagents=None, tail="", prompt="hi", prompt_id="p1", session_id="s1", reply="done"):
        path = self.transcript(lines)
        if tail:
            with open(path, "a", encoding="utf-8") as f:
                f.write(tail)
        for agent_id, sub in (subagents or {}).items():
            sub_dir = os.path.join(self.tmp, session_id, "subagents")
            os.makedirs(sub_dir, exist_ok=True)
            with open(os.path.join(sub_dir, "agent-%s.jsonl" % agent_id), "w", encoding="utf-8") as f:
                f.write("\n".join(json.dumps(x) for x in sub) + "\n")
        self.fake = self.use_api(FakeApi(ok_responses()))
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt=prompt, prompt_id=prompt_id, session_id=session_id)
            self.run_hook("Stop", transcript_path=path, last_assistant_message=reply, prompt_id=prompt_id,
                          session_id=session_id)
        return [t for m, p, b in self.fake.calls if p.startswith("/traces/batch") for t in b["traces"]]

    @staticmethod
    def names(traces):
        return [t["name"] for t in traces]

    def test_every_trace_field_is_redacted(self):
        secret = "gsk_" + "S" * 40
        traces = self.send([
            {"type": "user", "promptId": "p1", "message": {"content": "hi"}},
            {"type": "assistant", "message": {"id": "m1", "content": [
                {"type": "thinking", "thinking": "think " + secret},
                {"type": "text", "text": "text " + secret},
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "echo " + secret}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "error " + secret}]}},
        ])
        gen = next(t for t in traces if t["type"] == "GENERATION")
        tool = next(t for t in traces if t["type"] == "TOOL")
        fields = {"thinking": gen["outputData"]["thinking"], "text": gen["outputData"]["text"],
                  "tool input": tool["inputData"]["input"]["command"], "tool output": tool["outputData"],
                  "tool error": tool["error"]}
        for name, value in fields.items():
            with self.subTest(field=name):
                self.assertIn(gc.REDACTED, value)
                self.assertNotIn(secret, value)

    def test_a_long_trace_field_is_cut_at_20_kb(self):
        traces = self.send([
            {"type": "user", "promptId": "p1", "message": {"content": "hi"}},
            {"type": "assistant", "message": {"id": "m1", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "cat big"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
                                                      "content": "x" * 50000}]}},
        ])
        out = next(t for t in traces if t["type"] == "TOOL")["outputData"]
        self.assertIn("[truncated 30000 bytes]", out)
        self.assertLessEqual(len(out.encode()), 20000 + 40)

    def test_subagents_sidechains_and_split_model_calls(self):
        for name in ("Agent", "Task"):
            with self.subTest(tool=name):
                traces = self.send([
                    {"type": "user", "promptId": "p1", "message": {"content": "hi"}},
                    # one model call written as two lines with the same message id
                    {"type": "assistant", "message": {"id": "m1", "model": "claude", "usage": {"input_tokens": 1},
                                                      "content": [{"type": "text", "text": "part one"}]}},
                    {"type": "assistant", "message": {"id": "m1", "model": "claude", "usage": {"input_tokens": 1},
                                                      "content": [{"type": "tool_use", "id": "t1", "name": name,
                                                                   "input": {"prompt": "look"}}]}},
                    {"type": "assistant", "isSidechain": True, "message": {"id": "side", "content": [
                        {"type": "text", "text": "SIDECHAIN"}]}},
                    {"type": "user", "toolUseResult": {"agentId": "a1"}, "message": {"content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "found"}]}},
                ], subagents={"a1": [
                    {"type": "user", "message": {"content": "look"}},
                    {"type": "assistant", "message": {"id": "sub1", "model": "claude-sub", "content": [
                        {"type": "tool_use", "id": "t9", "name": "Grep", "input": {"pattern": "x"}}]}},
                    {"type": "user", "message": {"content": [
                        {"type": "tool_result", "tool_use_id": "t9", "content": "1"}]}},
                ]}, session_id="s-" + name)
                self.assertEqual(self.names(traces), ["llm:claude", "agent:" + name, "llm:claude-sub", "tool:Grep"])
                self.assertEqual(traces[0]["outputData"]["text"], "part one")
                agent = traces[1]
                self.assertEqual(agent["type"], "AGENT")
                self.assertNotIn("parentTraceId", agent)
                self.assertEqual([t.get("parentTraceId") for t in traces[2:]], [agent["id"]] * 2)
                self.assertNotIn("SIDECHAIN", json.dumps(traces))

    def test_span_latency_and_omitted_thinking_come_from_the_transcript(self):
        traces = self.send([
            prompt_line("p1", second=0),
            {"type": "assistant", "timestamp": at(1.5), "message": {"id": "m1", "model": "claude", "content": [
                {"type": "thinking", "thinking": ""},
                {"type": "redacted_thinking", "data": "abc"},
                {"type": "thinking", "thinking": "plan"},
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}},
            tool_result("t1", second=4),
            reply_line("m2", second=4.25),
        ])
        self.assertEqual([t["latencyMs"] for t in traces], [1500, 2500, 250])
        self.assertEqual(traces[0]["metadata"]["thinking_blocks_omitted"], 2)
        self.assertEqual(traces[0]["outputData"]["thinking"], "plan")

    def test_turn_latency_runs_from_the_prompt_to_the_stop(self):
        clock = [1000.0]

        def create(body):
            clock[0] += 30  # the hook's own time on the API is not part of the turn
            return {"id": "sess_1"}
        responses = ok_responses()
        responses[("POST", "/sessions")] = create
        fake = self.use_api(FakeApi(responses))
        path = self.transcript([prompt_line("p1"), reply_line("m1")])
        with mock.patch.object(gc.time, "time", lambda: clock[0]), mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt="hi")
            clock[0] += 7
            self.run_hook("Stop", transcript_path=path, last_assistant_message="done")
        self.assertEqual(clock[0], 1037)
        self.assertEqual(fake.body("POST", "/inferenceResults")["latency"], 7000)

    def test_only_a_failing_tool_has_an_error(self):
        traces = self.send([
            prompt_line("p1"),
            {"type": "assistant", "timestamp": at(1), "message": {"id": "m1", "model": "claude", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
                {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "false"}}]}},
            tool_result("t1", "fine"),
            tool_result("t2", "E" * 3000, is_error=True),
        ])
        ok, failed = [t for t in traces if t["type"] == "TOOL"]
        self.assertNotIn("error", ok)
        self.assertEqual(failed["error"], "E" * 2000 + "\n…[truncated 1000 bytes]")
        self.assertEqual(failed["outputData"], "E" * 3000)

    def test_a_model_call_lists_the_tools_it_called(self):
        traces = self.send([prompt_line("p1"), tool_call("m1", "t1"), tool_result("t1"), reply_line("m2")])
        self.assertEqual(self.names(traces), ["llm:claude", "tool:Bash", "llm:claude"])
        self.assertEqual(traces[0]["outputData"]["tool_calls"], ["Bash"])
        self.assertIsNone(traces[2]["outputData"].get("tool_calls"))

    def test_a_model_call_split_over_lines_counts_its_tokens_once(self):
        def part(block, output_tokens):
            return {"type": "assistant", "timestamp": at(1), "message": {"id": "m1", "model": "claude", "usage": {
                "input_tokens": 10, "output_tokens": output_tokens, "cache_read_input_tokens": 3},
                "content": [block]}}
        traces = self.send([
            prompt_line("p1"),
            part({"type": "thinking", "thinking": "plan"}, 1),
            part({"type": "text", "text": "running it"}, 2),
            part({"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}, 50),
            tool_result("t1"),
        ])
        self.assertEqual(self.names(traces), ["llm:claude", "tool:Bash"])
        self.assertEqual(self.fake.body("PATCH", "/inferenceResults/ir_1"),
                         {"inputTokens": 10, "outputTokens": 50, "cacheReadInputTokens": 3})

    def test_a_prompt_with_an_image_starts_its_own_turn(self):
        traces = self.send([
            prompt_line("p1", second=0), tool_call("m1", "t1", second=1), tool_result("t1", second=2),
            reply_line("m2", "first", second=3),
            prompt_line("p2", [{"type": "text", "text": "what is this?"}, {"type": "image", "source": {}}], 10),
            tool_call("m3", "t3", "Read", {"file_path": "a.png"}, second=11), tool_result("t3", second=12),
            reply_line("m4", "an image", second=13),
        ], prompt="what is this?", prompt_id="p2", reply="an image")
        self.assertEqual(self.names(traces), ["llm:claude", "tool:Read", "llm:claude"])
        self.assertEqual(traces[1]["inputData"], {"input": {"file_path": "a.png"}})

    def test_a_torn_last_line_is_skipped(self):
        traces = self.send([prompt_line("p1"), tool_call("m1", "t1"), tool_result("t1")], tail='{"type": "assist')
        self.assertEqual(self.names(traces), ["llm:claude", "tool:Bash"])

    def test_a_large_tool_input_becomes_a_cut_json_string(self):
        traces = self.send([prompt_line("p1"),
                            tool_call("m1", "t1", "Write", {"file_path": "x", "content": "y" * 30000}),
                            tool_result("t1")])
        sent = traces[1]["inputData"]["input"]
        self.assertIsInstance(sent, str)
        self.assertTrue(sent.startswith('{"file_path": "x"'))
        self.assertIn("[truncated", sent)
        self.assertLessEqual(len(sent.encode()), gc.MAX_SPAN_BYTES + 40)

    def test_a_tool_result_of_blocks_keeps_text_and_names_the_rest(self):
        traces = self.send([prompt_line("p1"), tool_call("m1", "t1", "Read"),
                            tool_result("t1", [{"type": "text", "text": "a"}, {"type": "image"}])])
        self.assertEqual(traces[1]["outputData"], "a\n[image]")

    def test_a_result_of_a_tool_outside_the_turn_is_skipped(self):
        traces = self.send([prompt_line("p1"), tool_result("t0", "lost"), tool_call("m1", "t1"), tool_result("t1"),
                            reply_line("m2")])
        self.assertEqual(self.names(traces), ["llm:claude", "tool:Bash", "llm:claude"])
        self.assertNotIn("spans failed", read_log(self.data))

    def test_subagent_nesting_stops_at_depth_3(self):
        chain = {"a%d" % i: [tool_call("sub%d" % i, "ta%d" % i, "Agent", {"prompt": "again"}),
                             tool_result("ta%d" % i, "again", agent_id="a%d" % (i + 1))] for i in range(1, 6)}
        traces = self.send([prompt_line("p1"), tool_call("m1", "t1", "Agent", {"prompt": "look"}),
                            tool_result("t1", "found", agent_id="a1")], subagents=chain)
        self.assertEqual(self.names(traces), ["llm:claude", "agent:Agent"] * 4)

    def test_a_resumed_subagent_is_nested_once(self):
        traces = self.send([prompt_line("p1"), tool_call("m1", "t1", "Agent", {"prompt": "look"}),
                            tool_result("t1", "found", agent_id="a1"),
                            tool_call("m2", "t2", "Agent", {"prompt": "go on"}),
                            tool_result("t2", "done", agent_id="a1")],
                           subagents={"a1": [reply_line("sub1"), reply_line("sub2")]})
        self.assertEqual(self.names(traces), ["llm:claude", "agent:Agent", "llm:claude", "llm:claude",
                                              "llm:claude", "agent:Agent"])
        ids = [t["id"] for t in traces]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual([t["parentTraceId"] for t in traces[2:4]], [traces[1]["id"]] * 2)

    def test_a_turn_without_its_prompt_id_starts_at_the_last_real_prompt(self):
        lines = [prompt_line("p1"), tool_call("m1", "t1", second=1), tool_result("t1", second=2),
                 stop_feedback(3), reply_line("m2", second=4)]
        for pid in (None, "unknown"):
            with self.subTest(prompt_id=pid):
                traces = self.send(lines, prompt_id=pid, session_id="s-%s" % pid)
                self.assertEqual(self.names(traces), ["llm:claude", "tool:Bash", "llm:claude"])

    def test_a_cut_inside_a_multibyte_character_keeps_valid_text(self):
        traces = self.send([prompt_line("p1"), tool_call("m1", "t1"), tool_result("t1", "x" + "é" * 10001)],
                           prompt="€" * 50000)
        fields = {
            "prompt": (self.fake.body("POST", "/inferenceResults")["input"]["user_message"], gc.MAX_TURN_BYTES,
                       "€" * 33333),
            "tool output": (traces[1]["outputData"], gc.MAX_SPAN_BYTES, "x" + "é" * 9999),
        }
        for name, (value, limit, kept) in fields.items():
            with self.subTest(field=name):
                self.assertIn("[truncated", value)
                self.assertEqual(value.split("\n…")[0], kept)
                self.assertLessEqual(len(value.encode("utf-8")), limit + 40)
                self.assertNotIn("\ufffd", value)

    def test_a_lone_surrogate_never_loses_the_turn_or_its_spans(self):
        traces = self.send([prompt_line("p1"), tool_call("m1", "t1"), tool_result("t1", "out \ud83d")],
                           prompt="hi \ud83d")
        turn = self.fake.body("POST", "/inferenceResults")
        self.assertEqual(turn["input"]["user_message"], "hi \ud83d")
        self.assertEqual(traces[1]["outputData"], "out \ud83d")
        opener = mock.MagicMock()
        opener.return_value.__enter__.return_value.read.return_value = b"{}"
        with mock.patch.object(gc._OPENER, "open", opener):
            for path, body in (("/inferenceResults", turn), ("/traces/batch", {"traces": traces})):
                REAL_API(gc.load_config(), "POST", path, body)
        sent = [json.loads(c.args[0].data.decode("utf-8")) for c in opener.call_args_list]
        self.assertEqual(sent[0]["input"]["user_message"], "hi ?")
        self.assertEqual(sent[1]["traces"][1]["outputData"], "out ?")

    def test_span_ids_are_the_same_each_time_a_turn_is_built(self):
        lines = [prompt_line("p1"), tool_call("m1", "t1"), tool_result("t1")]
        cfg = gc.load_config()
        first, again = ([s["id"] for s in gc.build_spans(lines, "ir_1", cfg, None, self.tmp, {})] for _ in "12")
        self.assertEqual(first, again)
        self.assertEqual(first, [gc.span_id("ir_1", "gen:m1"), gc.span_id("ir_1", "tool:t1")])
        self.assertNotEqual(gc.span_id("ir_2", "gen:m1"), first[0])


class TestSpanUpload(Base):
    """Spans go up in batches, and a Stop re-fired for the same turn sends only the spans not stored."""

    def first_stop(self, fake, lines, reply="done"):
        self.use_api(fake)
        self.run_hook("UserPromptSubmit", prompt="hi")
        self.stop(lines, reply)

    def stop(self, lines, reply="done", **kw):
        path = self.transcript(lines)
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("Stop", transcript_path=path, last_assistant_message=reply, **kw)

    @staticmethod
    def batches(fake):
        return [b["traces"] for m, p, b in fake.calls if p.startswith("/traces/batch")]

    @staticmethod
    def many_tools(n):
        """One model call with n tool calls: n + 1 spans."""
        return [prompt_line("p1"),
                {"type": "assistant", "timestamp": at(1), "message": {"id": "m1", "model": "claude", "content": [
                    {"type": "tool_use", "id": "t%d" % i, "name": "Bash", "input": {"command": "ls"}}
                    for i in range(n)]}},
                {"type": "user", "timestamp": at(2), "message": {"content": [
                    {"type": "tool_result", "tool_use_id": "t%d" % i, "content": "ok"} for i in range(n)]}}]

    def test_spans_go_in_batches_of_100_and_rejections_are_logged(self):
        responses = ok_responses()
        responses[("POST", "/traces/batch")] = {"partialSuccess": {"errors": [{"traceId": "x", "reason": "bad"}]}}
        fake = FakeApi(responses)
        self.first_stop(fake, self.many_tools(120))
        self.assertEqual([len(b) for b in self.batches(fake)], [100, 21])
        self.assertIn("rejected spans for ir_1", read_log(self.data))

    def test_a_failed_span_upload_keeps_the_turn_and_its_title(self):
        fake = FakeApi(ok_responses(), fail=[("POST", "/traces/batch")])
        self.first_stop(fake, [prompt_line("p1"), tool_call("m1", "t1"), tool_result("t1"),
                               {"type": "custom-title", "customTitle": "T"}])
        self.assertIn("spans failed for ir_1", read_log(self.data))
        titles = [b["metadata"]["title"] for m, p, b in fake.calls if (m, p) == ("PATCH", "/sessions/sess_1")]
        self.assertEqual(titles, ["T"])
        with gc.State("s1") as st:
            self.assertEqual(st["last_turn"]["ir_id"], "ir_1")

    def test_a_refired_stop_sends_only_the_spans_a_failed_batch_lost(self):
        posted, stored = [], []

        def batch(body):
            posted.append([t["id"] for t in body["traces"]])
            if len(posted) == 2:
                raise RuntimeError("POST /traces/batch -> 500")
            stored.extend(posted[-1])
            return {}
        responses = ok_responses()
        responses[("POST", "/traces/batch")] = batch
        lines = self.many_tools(120)
        self.first_stop(FakeApi(responses), lines)
        self.assertEqual(len(stored), 100)
        self.stop(lines, stop_hook_active=True)
        self.assertEqual(len(posted), 3)
        self.assertEqual(posted[2], posted[1])
        self.assertEqual(len(stored), 121)
        self.assertEqual(len(set(stored)), 121)

    def test_a_failed_token_patch_keeps_the_stored_spans(self):
        fake = FakeApi(ok_responses(), fail=[("PATCH", "/inferenceResults/ir_1")])
        lines = [prompt_line("p1"), tool_call("m1", "t1"), tool_result("t1"), reply_line("m2")]
        self.first_stop(fake, lines)
        self.stop(lines, stop_hook_active=True)
        self.assertIn("token counts not sent for ir_1", read_log(self.data))
        self.assertEqual([[t["name"] for t in b] for b in self.batches(fake)],
                         [["llm:claude", "tool:Bash", "llm:claude"]])

    def test_a_tool_still_running_at_the_first_read_is_sent_by_the_refired_stop(self):
        running = [prompt_line("p1"), tool_call("m1", "t1", second=1)]
        done = running + [tool_result("t1", "5 passed", second=4), reply_line("m2", "first reply", second=5),
                          stop_feedback(6), reply_line("m3", "final reply", second=7)]
        fake = FakeApi(ok_responses())
        self.first_stop(fake, running, "first reply")
        self.stop(done, "final reply", stop_hook_active=True)
        first, refired = self.batches(fake)
        self.assertEqual([t["name"] for t in first], ["llm:claude"])
        self.assertEqual([t["name"] for t in refired], ["tool:Bash", "llm:claude", "llm:claude"])
        tool = refired[0]
        self.assertEqual((tool["id"], tool["outputData"], tool["endTime"], tool["latencyMs"]),
                         (gc.span_id("ir_1", "tool:t1"), "5 passed", at(4), 3000))

    def test_the_first_stop_ends_at_the_final_reply(self):
        first = [prompt_line("p1"), reply_line("m1", "first reply", second=1), stop_feedback(2),
                 tool_call("m2", "t2", second=3), tool_result("t2", second=4)]
        fake = FakeApi(ok_responses())
        self.first_stop(fake, first, "first reply")
        self.stop(first + [reply_line("m3", "final reply", second=5)], "final reply", stop_hook_active=True)
        batches = self.batches(fake)
        self.assertEqual([[t["name"] for t in b] for b in batches],
                         [["llm:claude"], ["llm:claude", "tool:Bash", "llm:claude"]])
        self.assertEqual([t["id"] for t in batches[1]],
                         [gc.span_id("ir_1", k) for k in ("gen:m2", "tool:t2", "gen:m3")])

    @staticmethod
    def turn_of(pid, tool, start):
        return [prompt_line(pid, "prompt " + pid, start), tool_call("m" + pid, "t" + pid, tool, second=start + 1),
                tool_result("t" + pid, second=start + 2), reply_line("r" + pid, "done", second=start + 3)]

    @staticmethod
    def offsets(lines):
        """The byte offset of each line that Base.transcript writes."""
        out, pos = [], 0
        for x in lines:
            out.append(pos)
            pos += len(json.dumps(x).encode()) + 1
        return out

    def reads(self):
        """Patches read_jsonl to record the offset of each read; returns the list."""
        starts, real = [], gc.read_jsonl

        def read(path, start=0):
            starts.append(start)
            return real(path, start)
        p = mock.patch.object(gc, "read_jsonl", read)
        p.start()
        self.addCleanup(p.stop)
        return starts

    def test_scan_from_holds_the_offset_of_the_last_sent_prompt(self):
        lines = [{"type": "file-history-snapshot", "snapshot": {}}] + self.turn_of("p1", "Bash", 0)
        fake = FakeApi(ok_responses())
        self.first_stop(fake, lines)
        with gc.State("s1") as st:
            self.assertEqual(st["scan_from"], self.offsets(lines)[1])
        lines += self.turn_of("p2", "Read", 10)
        self.run_hook("UserPromptSubmit", prompt="prompt p2", prompt_id="p2")
        starts = self.reads()
        self.stop(lines, prompt_id="p2")
        self.assertEqual(starts, [self.offsets(lines)[1]])
        self.assertEqual([t["name"] for t in self.batches(fake)[1]], ["llm:claude", "tool:Read", "llm:claude"])
        with gc.State("s1") as st:
            self.assertEqual(st["scan_from"], self.offsets(lines)[5])

    def test_a_stale_or_too_large_scan_from_still_finds_the_prompt(self):
        lines = self.turn_of("p1", "Bash", 0) + self.turn_of("p2", "Read", 10)
        after_prompt = self.offsets(lines)[5]
        for name, scan_from, reads in (("stale", after_prompt, [after_prompt, 0]), ("past-the-end", 10 ** 9, [0])):
            with self.subTest(scan_from=name):
                session = "s-" + name
                with gc.State(session) as st:
                    st["scan_from"] = scan_from
                fake = self.use_api(FakeApi(ok_responses()))
                self.run_hook("UserPromptSubmit", prompt="prompt p2", prompt_id="p2", session_id=session)
                starts = self.reads()
                self.stop(lines, prompt_id="p2", session_id=session)
                self.assertEqual(starts, reads)
                self.assertEqual([t["name"] for b in self.batches(fake) for t in b],
                                 ["llm:claude", "tool:Read", "llm:claude"])


class TestResume(Base):
    """`claude --resume` of a session whose state file is gone (pruned after 7 days, or
    the plugin reinstalled) must keep sending turns to the session it already has."""

    NEWEST = "/inferenceResults?sessionIds=sess_old&sort=index&sort=desc&limit=1"

    def resume(self, turns, found=None, fail=(), path=None, sortable=True):
        """`turns` are the session's stored turns; the server answers the sorted read with the
        one of the highest index, or refuses it when not `sortable`."""
        newest = sorted(turns, key=lambda t: t.get("index", -1), reverse=True)[:1]
        responses = {("GET", self.NEWEST): newest, **ok_responses()}
        responses[("GET", "/sessions")] = [{"id": "sess_old", "customId": "s1"}] if found is None else found
        responses[("GET", "/inferenceResults")] = turns
        if not sortable:
            fail = list(fail) + [("GET", self.NEWEST, gc.ApiError("GET", self.NEWEST, 400, b"bad sort"))]
        fake = self.use_api(FakeApi(responses, fail=fail))
        self.run_hook("UserPromptSubmit", prompt="hi")
        self.run_hook("Stop", last_assistant_message="ok", **({"transcript_path": path} if path else {}))
        return fake

    def test_resumed_session_is_found_and_continues_its_turns(self):
        fake = self.resume([{"id": "a", "index": 0}, {"id": "b", "index": 1}, {"id": "c", "index": 2}])
        self.assertEqual(fake.paths("GET"), ["/sessions?customIds=s1&productIds=prod_1", self.NEWEST])
        body = fake.body("POST", "/inferenceResults")
        self.assertEqual((body["sessionId"], body["index"]), ("sess_old", 3))
        self.assertNotIn("/sessions", fake.paths("POST"))

    def test_a_platform_that_cannot_sort_counts_every_turn(self):
        fake = self.resume([{"id": "a", "index": 0}, {"id": "b", "index": 4}], sortable=False)
        self.assertIn("/inferenceResults?sessionIds=sess_old", fake.paths("GET"))
        self.assertEqual(fake.body("POST", "/inferenceResults")["index"], 5)

    def test_the_newest_of_two_sessions_with_the_custom_id_is_continued(self):
        # customId is unique per version only, so each version can hold a session of the conversation
        fake = self.resume([{"id": "a", "index": 0}], found=[
            {"id": "sess_new", "customId": "s1", "createdAt": "2026-02-01T00:00:00Z"},
            {"id": "sess_old", "customId": "s1", "createdAt": "2026-01-01T00:00:00Z"}])
        self.assertIn("/inferenceResults?sessionIds=sess_new&sort=index&sort=desc&limit=1", fake.paths("GET"))
        self.assertEqual(fake.body("POST", "/inferenceResults")["sessionId"], "sess_new")
        self.assertNotIn("/sessions", fake.paths("POST"))

    def test_a_session_with_another_custom_id_is_not_reused(self):
        fake = self.resume([], found=[{"id": "sess_other", "customId": "other"}])
        self.assertEqual(fake.body("POST", "/inferenceResults")["sessionId"], "sess_1")

    def test_a_failed_lookup_creates_only_a_new_conversation(self):
        prompt = {"type": "user", "promptId": "p1", "message": {"content": "hi"}}
        earlier = {"type": "user", "promptId": "p0", "message": {"content": "before"}}
        for lines, created in (([prompt], True), ([earlier, prompt], False)):
            with self.subTest(created=created):
                shutil.rmtree(self.data, True)
                fake = self.resume([], fail=[("GET", "/sessions")], path=self.transcript(lines))
                self.assertEqual("/sessions" in fake.paths("POST"), created)
                self.assertEqual("/inferenceResults" in fake.paths("POST"), created)
                with open(os.path.join(self.data, "hook.log"), encoding="utf-8") as f:
                    log = f.read()
                self.assertIn("so it is created" if created else "this turn is not sent", log)

    def test_an_unreadable_state_file_is_started_again(self):
        # a hook killed, or a full disk, between truncate and the end of the write
        for content in ('{"index": 3, "galtea_session_id": "sess_o', "[1, 2]"):
            with self.subTest(content=content):
                os.makedirs(os.path.join(self.data, "state"), exist_ok=True)
                with open(os.path.join(self.data, "state", "s1.json"), "w", encoding="utf-8") as f:
                    f.write(content)
                fake = self.resume([{"id": "a", "index": 0}, {"id": "b", "index": 1}])
                body = fake.body("POST", "/inferenceResults")
                self.assertEqual((body["sessionId"], body["index"]), ("sess_old", 2))
                with open(os.path.join(self.data, "hook.log"), encoding="utf-8") as f:
                    self.assertIn("unreadable state", f.read())

    def test_a_new_product_gets_its_own_session(self):
        fake = self.resume([], found=[])
        os.environ["CLAUDE_PLUGIN_OPTION_PRODUCT_ID"] = "prod_2"
        fake.responses[("POST", "/sessions")] = {"id": "sess_2"}
        self.run_hook("UserPromptSubmit", prompt="again")
        self.run_hook("Stop", last_assistant_message="ok")
        self.assertIn("/sessions?customIds=s1&productIds=prod_2", fake.paths("GET"))
        self.assertEqual([b["productId"] for m, p, b in fake.calls if (m, p) == ("POST", "/sessions")],
                         ["prod_1", "prod_2"])
        turns = [(b["sessionId"], b["index"]) for m, p, b in fake.calls if p == "/inferenceResults"]
        self.assertEqual(turns, [("sess_1", 0), ("sess_2", 0)])

    def test_next_index_skips_a_gap(self):
        fake = self.resume([{"id": "a", "index": 0}, {"id": "b", "index": 4}])
        self.assertEqual(fake.body("POST", "/inferenceResults")["index"], 5)

    def test_a_refused_create_logs_the_dropped_turn(self):
        collision = gc.ApiError("POST", "/sessions", 400, b'{"error":"A Session with the same customId: '
                                                          b"'s1' already exists for version with ID: 'v1'.\"}")
        fake = self.resume([], found=[], fail=[("POST", "/sessions", collision)])
        self.assertNotIn("/inferenceResults", fake.paths("POST"))
        with open(os.path.join(self.data, "hook.log"), encoding="utf-8") as f:
            log = f.read()
        self.assertIn("turn not sent for session s1", log)
        self.assertIn("already exists", log)

    def test_session_creation_ends_before_a_waiting_hook_takes_over(self):
        fake = self.use_api(FakeApi(ok_responses()))
        start = time.time()
        self.run_hook("UserPromptSubmit", prompt="hi")
        self.run_hook("Stop", last_assistant_message="ok")
        create = [d for (m, p, b), d in zip(fake.calls, fake.deadlines) if p.startswith("/sessions")]
        self.assertEqual(len(create), 2)  # the lookup, then the create
        for deadline in create:
            self.assertIsNotNone(deadline)
            self.assertLessEqual(deadline, time.time() + gc.CREATE_BUDGET)
            self.assertGreaterEqual(deadline, start + gc.CREATE_BUDGET)
        self.assertGreater(gc.WAIT, gc.CREATE_BUDGET)
        self.assertGreaterEqual(gc.CREATE_BUDGET, 30)  # room for one call at the default timeout

    def test_a_new_conversation_is_created_after_one_lookup(self):
        fake = self.resume([], found=[])
        self.assertEqual(fake.paths("GET"), ["/sessions?customIds=s1&productIds=prod_1"])
        self.assertEqual(fake.body("POST", "/inferenceResults")["sessionId"], "sess_1")


class TestSessionState(Base):
    """The state file follows the Galtea session: a session that is gone, a failed create or
    finish, and a product change each leave a state the next hook can use."""

    def hook_log(self):
        with open(os.path.join(self.data, "hook.log"), encoding="utf-8") as f:
            return f.read()

    def send(self, prompt="hi", **stop):
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt=prompt)
            self.run_hook("Stop", last_assistant_message="ok", **stop)

    @staticmethod
    def new_sessions(responses, *ids):
        sessions = iter(ids)
        responses[("POST", "/sessions")] = lambda body: {"id": next(sessions)}
        return responses

    def test_a_404_of_an_older_session_keeps_the_newer_one(self):
        last = {"prompt_id": "p1", "ir_id": "ir_9", "sent": ["gen:m1"]}
        with gc.State("s1") as st:
            st.update(galtea_session_id="sess_2", product_id="prod_1", index=3, last_turn=last)
        gc.forget_session("s1", "sess_1")
        with gc.State("s1") as st:
            self.assertEqual((st["galtea_session_id"], st["index"], st["last_turn"]), ("sess_2", 3, last))
        gc.forget_session("s1", "sess_2")
        with gc.State("s1") as st:
            self.assertEqual((st.get("galtea_session_id"), st["index"], st.get("last_turn")), (None, 0, None))

    def test_a_turn_refused_with_404_twice_leaves_no_empty_session(self):
        err = gc.ApiError("POST", "/inferenceResults", 404, b"session not found")
        responses = self.new_sessions(ok_responses(), "sess_1", "sess_2")
        fake = self.use_api(FakeApi(responses, fail=[("POST", "/inferenceResults", err)]))
        self.send()
        self.assertEqual(fake.paths("POST"), ["/sessions", "/inferenceResults", "/sessions", "/inferenceResults"])
        self.assertEqual([b["sessionId"] for m, p, b in fake.calls if p == "/inferenceResults"], ["sess_1", "sess_2"])
        self.assertEqual(fake.paths("DELETE"), ["/sessions/sess_2"])
        self.assertIn("turn not sent for session s1", self.hook_log())
        with gc.State("s1") as st:
            self.assertEqual((st["index"], st.get("galtea_session_id")), (0, None))

    def test_a_failed_create_lets_the_next_turn_create_at_once(self):
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            if seconds == 0.1:
                raise AssertionError("the claim loop waited")
        fake = self.use_api(FakeApi(ok_responses(), fail=[("POST", "/sessions")]))
        with mock.patch.object(gc.time, "sleep", sleep):
            self.run_hook("UserPromptSubmit", prompt="hi")
            self.run_hook("Stop", last_assistant_message="ok")
            fake.fail = ()
            self.run_hook("UserPromptSubmit", prompt="again")
            self.run_hook("Stop", last_assistant_message="ok")
        self.assertNotIn(0.1, sleeps)
        self.assertEqual(fake.paths("POST").count("/sessions"), 2)
        self.assertEqual([(b["sessionId"], b["index"]) for m, p, b in fake.calls if p == "/inferenceResults"],
                         [("sess_1", 0)])

    def test_a_claim_after_the_session_was_dropped_creates_it_again(self):
        real = gc.ensure_session
        dropped = []

        def ensure_then_drop(cfg, hook):
            sid = real(cfg, hook)
            if not dropped:  # drop_empty_session of another Stop runs between the two locks
                dropped.append(sid)
                with gc.State("s1") as st:
                    gc.reset_session_state(st)
            return sid
        fake = self.use_api(FakeApi(self.new_sessions(ok_responses(), "sess_1", "sess_2")))
        with mock.patch.object(gc, "ensure_session", ensure_then_drop):
            self.send()
        self.assertEqual(dropped, ["sess_1"])
        self.assertEqual(fake.paths("POST").count("/sessions"), 2)
        self.assertEqual([(b["sessionId"], b["index"]) for m, p, b in fake.calls if p == "/inferenceResults"],
                         [("sess_2", 0)])

    def test_a_product_change_forgets_the_old_session(self):
        with gc.State("s1") as st:
            st.update(galtea_session_id="sess_1", product_id="prod_1", index=4, metadata={"title": "Old"},
                      title_sent=["Old", "custom"], finished=True,
                      last_turn={"prompt_id": "p1", "ir_id": "ir_1", "sent": []})
        os.environ["CLAUDE_PLUGIN_OPTION_PRODUCT_ID"] = "prod_2"
        self.use_api(FakeApi(self.new_sessions(ok_responses(), "sess_2")))
        self.assertEqual(gc.ensure_session(gc.load_config(), self.hook("Stop")), "sess_2")
        with gc.State("s1") as st:
            self.assertEqual({k: st.get(k) for k in ("galtea_session_id", "product_id", "index", "title_sent",
                                                      "finished", "last_turn")},
                             {"galtea_session_id": "sess_2", "product_id": "prod_2", "index": 0,
                              "title_sent": None, "finished": None, "last_turn": None})

    def test_a_failed_finish_is_sent_again_at_the_next_end(self):
        attempts = []

        def finish(body):
            attempts.append(body)
            if len(attempts) == 1:
                raise RuntimeError("PATCH /sessions/sess_1/finish -> 500")
            return {}
        responses = ok_responses()
        responses[("PATCH", "/sessions/sess_1/finish")] = finish
        self.use_api(FakeApi(responses))
        self.send()
        for _ in range(3):
            self.run_hook("SessionEnd", reason="other")
        self.assertEqual(len(attempts), 2)
        self.assertIn("PATCH /sessions/sess_1/finish -> 500", self.hook_log())

    def test_the_finish_names_the_end_reason(self):
        fake = self.use_api(FakeApi(ok_responses()))
        self.send()
        self.run_hook("SessionEnd", reason="prompt_input_exit")
        self.send(prompt="again")
        self.run_hook("SessionEnd")
        self.assertEqual([b for m, p, b in fake.calls if p == "/sessions/sess_1/finish"],
                         [{"stoppingReason": "claude_code:prompt_input_exit"},
                          {"stoppingReason": "claude_code:other"}])

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_session_metadata_names_the_repository_and_the_user(self):
        gitconfig = os.path.join(self.tmp, "gitconfig")
        open(gitconfig, "w").close()
        os.environ.update(GIT_CONFIG_GLOBAL=gitconfig, GIT_CONFIG_NOSYSTEM="1")
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t")
        for args in (["init", "-q", "-b", "main"], ["config", "user.email", "dev@example.com"],
                     ["remote", "add", "origin", "https://user:pass@example.com/org/repo.git"],
                     ["commit", "-q", "--allow-empty", "-m", "x"]):
            subprocess.run(["git", "-C", repo] + args, check=True, env=env, capture_output=True)
        commit = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], check=True, env=env,
                                capture_output=True, text=True).stdout.strip()
        self.assertRegex(commit, r"^[0-9a-f]{40}$")
        fake = self.use_api(FakeApi(ok_responses()))
        self.send(cwd=repo)
        meta = fake.body("POST", "/sessions")["metadata"]
        keys = ("git_repo", "git_branch", "git_commit", "source", "cwd", "user_email")
        self.assertEqual({k: meta.get(k) for k in keys},
                         {"git_repo": "https://example.com/org/repo.git", "git_branch": "main", "git_commit": commit,
                          "source": "claude-code", "cwd": repo, "user_email": "dev@example.com"})
        self.assertNotIn("user_email", gc.session_body(gc.load_config(), "s2", self.tmp)["metadata"])

    def test_a_remote_url_loses_its_user_in_every_form(self):
        for url, out in (("alice@github.com:org/repo.git", "github.com:org/repo.git"),
                         ("ssh://git@github.com/org/repo.git", "ssh://github.com/org/repo.git"),
                         ("https://github.com/org/repo.git", "https://github.com/org/repo.git"),
                         ("github.com:org/repo.git", "github.com:org/repo.git")):
            with self.subTest(url=url):
                self.assertEqual(gc.strip_userinfo(url), out)


class TestTitle(Base):
    """The Claude Code session name goes to session metadata as `title` and `title_source`."""

    def lines(self, *titles):
        out = [{"type": "user", "promptId": "p1", "message": {"content": "hi"}}]
        for kind, text in titles:
            key = "customTitle" if kind == "custom-title" else "aiTitle"
            out.append({"type": kind, key: text, "sessionId": "s1"})
        return out

    def turn_with(self, fake_responses, path, **stop):
        fake = self.use_api(FakeApi(fake_responses))
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt="hi")
            self.run_hook("Stop", transcript_path=path, last_assistant_message="ok", **stop)
        return fake

    @staticmethod
    def title_patches(fake):
        return [b["metadata"] for m, p, b in fake.calls if m == "PATCH" and p == "/sessions/sess_1"]

    def test_custom_title_is_sent_with_the_full_metadata(self):
        path = self.transcript(self.lines(("ai-title", "Auto name"), ("custom-title", "Old"),
                                          ("custom-title", "Fix the login bug")))
        fake = self.turn_with(ok_responses(), path)
        created = fake.body("POST", "/sessions")["metadata"]
        self.assertEqual(self.title_patches(fake),
                         [dict(created, title="Fix the login bug", title_source="custom")])
        self.assertEqual(created["source"], "claude-code")

    def test_ai_title_is_the_fallback(self):
        path = self.transcript(self.lines(("ai-title", "First guess"), ("ai-title", "Refactor the parser")))
        fake = self.turn_with(ok_responses(), path)
        meta = self.title_patches(fake)[0]
        self.assertEqual((meta["title"], meta["title_source"]), ("Refactor the parser", "ai"))

    def test_no_title_sends_nothing(self):
        fake = self.turn_with(ok_responses(), self.transcript(self.lines()))
        self.assertEqual(self.title_patches(fake), [])

    def test_title_is_sent_again_only_when_it_changes(self):
        path = self.transcript(self.lines(("custom-title", "Fix the login bug")))
        fake = self.turn_with(ok_responses(), path)
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt="again")
            self.run_hook("Stop", transcript_path=path, last_assistant_message="ok")
        self.assertEqual(len(self.title_patches(fake)), 1)
        with open(path, "a", encoding="utf-8") as f:  # /rename after the last turn
            f.write(json.dumps({"type": "custom-title", "customTitle": "Login fixed", "sessionId": "s1"}) + "\n")
        self.run_hook("SessionEnd", reason="other", transcript_path=path)
        self.assertEqual([m["title"] for m in self.title_patches(fake)], ["Fix the login bug", "Login fixed"])
        self.assertEqual(self.title_patches(fake)[1]["source"], "claude-code")
        paths = fake.paths("PATCH")
        self.assertLess(paths.index("/sessions/sess_1"), paths.index("/sessions/sess_1/finish"))

    def test_title_is_redacted(self):
        path = self.transcript(self.lines(("custom-title", "debug gsk_" + "k" * 30)))
        fake = self.turn_with(ok_responses(), path)
        self.assertEqual(self.title_patches(fake)[0]["title"], "debug " + gc.REDACTED)

    def test_resumed_session_keeps_its_stored_metadata(self):
        responses = ok_responses()
        responses = dict([(("GET", "/sessions/sess_old"), {"id": "sess_old", "metadata": {
            "git_branch": "feature", "plugin_version": "0.0.9"}}),
            (("GET", "/sessions"), [{"id": "sess_old", "customId": "s1"}])] + list(responses.items()))
        fake = self.use_api(FakeApi(responses))
        path = self.transcript(self.lines(("custom-title", "Resumed work")))
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt="hi")
            self.run_hook("Stop", transcript_path=path, last_assistant_message="ok")
        patches = [b["metadata"] for m, p, b in fake.calls if m == "PATCH" and p == "/sessions/sess_old"]
        self.assertEqual(patches, [{"git_branch": "feature", "plugin_version": "0.0.9",
                                    "title": "Resumed work", "title_source": "custom"}])

    def test_a_resumed_session_with_no_metadata_gets_its_title(self):
        responses = dict([(("GET", "/sessions/sess_old"), {"id": "sess_old", "metadata": None}),
                          (("GET", "/sessions"), [{"id": "sess_old", "customId": "s1"}])]
                         + list(ok_responses().items()))
        fake = self.turn_with(responses, self.transcript(self.lines(("custom-title", "Resumed work"))))
        patches = [b["metadata"] for m, p, b in fake.calls if m == "PATCH" and p == "/sessions/sess_old"]
        self.assertEqual(patches, [{"title": "Resumed work", "title_source": "custom"}])

    @staticmethod
    def titles(fake):
        return [(p, b["metadata"]["title"]) for m, p, b in fake.calls if m == "PATCH" and "metadata" in b]

    def test_the_session_of_a_new_product_gets_the_title(self):
        path = self.transcript(self.lines(("custom-title", "Fix the login bug")))
        responses = ok_responses()
        sessions = iter(["sess_1", "sess_2"])
        responses[("POST", "/sessions")] = lambda body: {"id": next(sessions)}
        fake = self.turn_with(responses, path)
        os.environ["CLAUDE_PLUGIN_OPTION_PRODUCT_ID"] = "prod_2"
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt="again")
            self.run_hook("Stop", transcript_path=path, last_assistant_message="ok")
        self.assertEqual(self.titles(fake), [("/sessions/sess_1", "Fix the login bug"),
                                             ("/sessions/sess_2", "Fix the login bug")])

    def test_a_session_created_again_after_a_404_gets_the_title(self):
        path = self.transcript(self.lines(("custom-title", "Fix the login bug")))
        turns = []

        def turn(body):
            turns.append(body["sessionId"])
            if len(turns) == 2:
                raise gc.ApiError("POST", "/inferenceResults", 404, b"session not found")
            return {"id": "ir_%d" % len(turns)}
        responses = ok_responses()
        sessions = iter(["sess_1", "sess_2"])
        responses[("POST", "/sessions")] = lambda body: {"id": next(sessions)}
        responses[("POST", "/inferenceResults")] = turn
        fake = self.turn_with(responses, path)
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt="again")
            self.run_hook("Stop", transcript_path=path, last_assistant_message="ok")
        self.assertEqual(turns, ["sess_1", "sess_1", "sess_2"])
        self.assertEqual(self.titles(fake), [("/sessions/sess_1", "Fix the login bug"),
                                             ("/sessions/sess_2", "Fix the login bug")])

    def test_a_title_failure_never_blocks_the_turn(self):
        def patch(body):
            if "metadata" in body:
                raise RuntimeError("PATCH /sessions/sess_1 -> 500")
            return {}
        responses = ok_responses()
        responses[("PATCH", "/sessions/sess_1")] = patch
        path = self.transcript(self.lines(("custom-title", "Fix the login bug")))
        fake = self.turn_with(responses, path)
        self.run_hook("SessionEnd", reason="other", transcript_path=path)
        self.assertIn("/inferenceResults", fake.paths("POST"))
        self.assertIn("/sessions/sess_1/finish", fake.paths("PATCH"))
        with open(os.path.join(self.data, "hook.log"), encoding="utf-8") as f:
            self.assertIn("title not sent for session s1", f.read())


class TestConcurrency(Base):
    """Async hooks of one session run at the same time. The state lock is held only
    around state reads and writes, never across an API call."""

    def run_in_thread(self, event, **kw):
        t = threading.Thread(target=gc.hook_main, args=(json.dumps(self.hook(event, **kw)),), daemon=True)
        t.start()
        # a thread that outlived its test would call the next test's fake API
        self.addCleanup(self.assert_finished, t)
        return t

    def assert_finished(self, thread):
        thread.join(5)
        self.assertFalse(thread.is_alive(), "a hook thread outlived its test")

    def test_state_lock_blocks_a_second_opener(self):
        started, entered = threading.Event(), threading.Event()

        def second():
            started.set()
            with gc.State("s1"):
                entered.set()
        with gc.State("s1"):
            t = threading.Thread(target=second, daemon=True)
            t.start()
            self.addCleanup(self.assert_finished, t)
            self.assertTrue(started.wait(2))
            self.assertFalse(entered.wait(0.3))
        self.assertTrue(entered.wait(2))

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
        self.run_hook("UserPromptSubmit", prompt="one", prompt_id="p1")
        self.run_hook("UserPromptSubmit", prompt="two", prompt_id="p2")
        first = self.run_in_thread("Stop", prompt_id="p1", last_assistant_message="1")
        self.assertTrue(started.wait(2))
        second = self.run_in_thread("Stop", prompt_id="p2", last_assistant_message="2")
        first.join(5)
        second.join(5)
        self.assertEqual(fake.paths("POST").count("/sessions"), 1)
        self.assertEqual([b["sessionId"] for m, p, b in fake.calls if p == "/inferenceResults"], ["sess_1"] * 2)

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

    def test_session_end_leaves_a_session_with_a_turn_in_flight_to_auto_close(self):
        # finishing now would let monitors score the session before this turn's traces arrive
        first_in, release = threading.Event(), threading.Event()

        def turn(body):
            first_in.set()
            release.wait(5)
            return {"id": "ir_%d" % body["index"]}
        responses = ok_responses()
        responses[("POST", "/inferenceResults")] = turn
        fake = self.use_api(FakeApi(responses))
        release.set()
        self.run_hook("UserPromptSubmit", prompt="one")
        self.run_hook("Stop", last_assistant_message="1")
        release.clear()
        first_in.clear()
        self.run_hook("UserPromptSubmit", prompt="two")
        stop = self.run_in_thread("Stop", last_assistant_message="2")
        self.assertTrue(first_in.wait(2))
        with mock.patch.object(gc.time, "sleep", side_effect=AssertionError("SessionEnd waited")):
            self.run_hook("SessionEnd", reason="other")
        release.set()
        stop.join(5)
        self.assertNotIn("/sessions/sess_1/finish", fake.paths("PATCH"))
        with open(os.path.join(self.data, "hook.log"), encoding="utf-8") as f:
            self.assertIn("session s1 is still sending a turn at its end; auto-close will close it", f.read())

    def test_session_end_does_not_wait_for_an_interrupted_turn(self):
        fake = self.use_api(FakeApi(ok_responses()))
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt="one")
            self.run_hook("Stop", last_assistant_message="1")
        self.run_hook("UserPromptSubmit", prompt="interrupted, so no Stop")
        with mock.patch.object(gc.time, "sleep", side_effect=AssertionError("SessionEnd waited")):
            self.run_hook("SessionEnd", reason="other")
        self.assertIn("/sessions/sess_1/finish", fake.paths("PATCH"))

    def test_a_stop_killed_long_ago_does_not_block_the_finish(self):
        fake = self.use_api(FakeApi(ok_responses()))
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt="one")
            self.run_hook("Stop", last_assistant_message="1")
        with gc.State("s1") as st:
            st["inflight"] = {"killed": time.time() - gc.INFLIGHT_MAX - 1}
        self.run_hook("SessionEnd", reason="other")
        self.assertIn("/sessions/sess_1/finish", fake.paths("PATCH"))

    def sent_turns(self, fake):
        return [(b["input"]["user_message"], b["actualOutput"], b["index"])
                for m, p, b in fake.calls if m == "POST" and p == "/inferenceResults"]

    def test_a_queued_prompt_keeps_each_turn_with_its_own_prompt(self):
        # a queued prompt's hook can take the state lock before the previous turn's Stop
        fake = self.use_api(FakeApi(ok_responses()))
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt="ONE", prompt_id="p1")
            self.run_hook("UserPromptSubmit", prompt="TWO", prompt_id="p2")
            self.run_hook("Stop", prompt_id="p1", last_assistant_message="reply one")
            self.run_hook("Stop", prompt_id="p2", last_assistant_message="reply two")
        self.assertEqual(self.sent_turns(fake), [("ONE", "reply one", 0), ("TWO", "reply two", 1)])

    def stop_without_ids(self, prompts, reply, **kw):
        """A Stop from a Claude Code that sends no prompt ids; the transcript holds `prompts`."""
        lines = []
        for text in prompts:
            lines += [{"type": "user", "message": {"content": text}},
                      {"type": "assistant", "message": {"id": "m_" + text, "content": [{"type": "text", "text": "r"}]}}]
        self.run_hook("Stop", prompt_id=None, last_assistant_message=reply,
                      transcript_path=self.transcript(lines), **kw)

    def test_without_prompt_ids_each_stop_takes_the_prompt_of_its_transcript(self):
        fake = self.use_api(FakeApi(ok_responses()))
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt="ONE", prompt_id=None)
            self.run_hook("UserPromptSubmit", prompt="TWO", prompt_id=None)  # queued
            self.stop_without_ids(["ONE"], "reply one")
            self.stop_without_ids(["ONE", "TWO"], "reply two")
            self.run_hook("UserPromptSubmit", prompt="THREE", prompt_id=None)
            self.run_hook("Stop", prompt_id="p3", last_assistant_message="reply three")
        self.assertEqual(self.sent_turns(fake), [("ONE", "reply one", 0), ("TWO", "reply two", 1),
                                                 ("THREE", "reply three", 2)])

    def test_without_prompt_ids_an_interrupted_prompt_is_dropped(self):
        # a turn interrupted with Esc gets no Stop
        fake = self.use_api(FakeApi(ok_responses()))
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt="A", prompt_id=None)
            self.run_hook("UserPromptSubmit", prompt="B", prompt_id=None)
            self.stop_without_ids(["A", "B"], "reply to B")
            self.run_hook("UserPromptSubmit", prompt="C", prompt_id=None)
            self.stop_without_ids(["A", "B", "C"], "reply to C")
        self.assertEqual(self.sent_turns(fake), [("B", "reply to B", 0), ("C", "reply to C", 1)])
        with open(os.path.join(self.data, "hook.log"), encoding="utf-8") as f:
            self.assertIn("a Stop dropped 1 earlier prompts that got no Stop", f.read())

    def test_without_prompt_ids_a_refired_stop_leaves_a_queued_prompt(self):
        fake = self.use_api(FakeApi(ok_responses()))
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt="ONE", prompt_id=None)
            self.stop_without_ids(["ONE"], "reply one")
            self.run_hook("UserPromptSubmit", prompt="TWO", prompt_id=None)  # queued
            self.stop_without_ids(["ONE"], "reply one, continued", stop_hook_active=True)
            self.stop_without_ids(["ONE", "TWO"], "reply two")
        self.assertEqual(self.sent_turns(fake), [("ONE", "reply one", 0), ("TWO", "reply two", 1)])
        self.assertIn(("PATCH", "/inferenceResults/ir_1", {"actualOutput": "reply one, continued"}), fake.calls)

    def test_unsent_prompts_are_bounded(self):
        # an interrupted turn gets no Stop, so its prompt is never taken
        self.use_api(FakeApi(ok_responses()))
        for i in range(gc.PENDING_MAX + 5):
            self.run_hook("UserPromptSubmit", prompt="p%d" % i, prompt_id="p%d" % i)
        with gc.State("s1") as st:
            self.assertEqual([p["prompt"] for p in st["prompts"]],
                             ["p%d" % i for i in range(5, gc.PENDING_MAX + 5)])
        with open(os.path.join(self.data, "hook.log"), encoding="utf-8") as f:
            self.assertEqual(f.read().count("session s1 dropped its oldest unsent prompt"), 5)

    def continued_turn(self):
        """A turn, then Claude continuing it after another plugin's Stop hook blocked."""
        first = [
            {"type": "user", "promptId": "p1", "message": {"content": "hi"}},
            {"type": "assistant", "message": {"id": "m1", "model": "claude", "usage": {"input_tokens": 10},
                                              "content": [{"type": "text", "text": "first reply"}]}},
        ]
        more = [
            {"type": "user", "promptId": "p1", "isMeta": True, "message": {"content": "Stop hook feedback"}},
            {"type": "assistant", "message": {"id": "m2", "model": "claude", "usage": {"input_tokens": 5},
                                              "content": [{"type": "tool_use", "id": "t2", "name": "Bash",
                                                           "input": {"command": "make test"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "content": "ok"}]}},
        ]
        return first, more

    def test_a_stop_after_a_blocking_stop_hook_updates_its_turn(self):
        first, more = self.continued_turn()
        path = os.path.join(self.tmp, "t.jsonl")
        fake = self.use_api(FakeApi(ok_responses()))
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt="hi")
            self.transcript(first)
            self.run_hook("Stop", transcript_path=path, last_assistant_message="first reply")
            self.transcript(first + more)
            self.run_hook("Stop", transcript_path=path, last_assistant_message="final reply", stop_hook_active=True)
        self.assertEqual(fake.paths("POST").count("/inferenceResults"), 1)
        patches = [b for m, p, b in fake.calls if (m, p) == ("PATCH", "/inferenceResults/ir_1")]
        self.assertIn({"actualOutput": "final reply"}, patches)
        self.assertEqual(patches[-1], {"inputTokens": 15, "outputTokens": 0, "cacheReadInputTokens": 0})
        batches = [[t["name"] for t in b["traces"]] for m, p, b in fake.calls if p.startswith("/traces/batch")]
        self.assertEqual(batches, [["llm:claude"], ["llm:claude", "tool:Bash"]])

    def test_a_stop_after_a_blocking_stop_hook_waits_for_the_turn(self):
        first, more = self.continued_turn()
        path = self.transcript(first + more)
        release, first_in, waiting = threading.Event(), threading.Event(), threading.Event()
        real_sleep = time.sleep

        def turn(body):
            first_in.set()
            release.wait(5)
            return {"id": "ir_1"}

        def sleep(seconds):
            waiting.set()
            real_sleep(0.01)
        responses = ok_responses()
        responses[("POST", "/inferenceResults")] = turn
        fake = self.use_api(FakeApi(responses))
        self.run_hook("UserPromptSubmit", prompt="hi")
        with mock.patch.object(gc.time, "sleep", sleep):
            stop = self.run_in_thread("Stop", transcript_path=path, last_assistant_message="first reply")
            self.assertTrue(first_in.wait(2))
            refire = self.run_in_thread("Stop", transcript_path=path, last_assistant_message="final reply",
                                        stop_hook_active=True)
            self.assertTrue(waiting.wait(2))  # the re-fired Stop waits while the turn is being sent
            release.set()
            stop.join(5)
            refire.join(5)
        self.assertIn(("PATCH", "/inferenceResults/ir_1", {"actualOutput": "final reply"}), fake.calls)

    def test_a_stop_with_no_stored_prompt_is_logged(self):
        fake = self.use_api(FakeApi(ok_responses()))
        self.run_hook("Stop", last_assistant_message="ok", stop_hook_active=False)
        self.run_hook("Stop", last_assistant_message="ok", stop_hook_active=True)
        with open(os.path.join(self.data, "hook.log"), encoding="utf-8") as f:
            log = f.read()
        self.assertIn("Stop of session s1 has no stored prompt", log)
        self.assertIn("Stop of session s1 continued a turn that was not sent", log)
        self.assertEqual(fake.calls, [])

    def test_a_refused_turn_is_logged_and_frees_its_index(self):
        err = gc.ApiError("POST", "/inferenceResults", 400, b"bad")
        self.use_api(FakeApi(ok_responses(), fail=[("POST", "/inferenceResults", err)]))
        gc.hook_main(json.dumps(self.hook("UserPromptSubmit", prompt="hi")))
        gc.hook_main(json.dumps(self.hook("Stop", last_assistant_message="ok")))
        with open(os.path.join(self.data, "hook.log"), encoding="utf-8") as f:
            self.assertIn("turn not sent for session s1", f.read())
        with gc.State("s1") as st:
            self.assertEqual(st["index"], 0)

    def test_a_turn_the_server_may_have_stored_keeps_its_index(self):
        # a timeout or a 5xx can come after the server stored the turn: a gap is harmless, a duplicate is not
        for err in (socket.timeout("timed out"), urllib.error.URLError("reset"),
                    gc.ApiError("POST", "/inferenceResults", 502, b"bad gateway")):
            with self.subTest(err=repr(err)):
                fake = self.use_api(FakeApi(ok_responses(), fail=[("POST", "/inferenceResults", err)]))
                gc.hook_main(json.dumps(self.hook("UserPromptSubmit", prompt="hi")))
                gc.hook_main(json.dumps(self.hook("Stop", last_assistant_message="ok")))
                fake.fail = ()
                gc.hook_main(json.dumps(self.hook("UserPromptSubmit", prompt="next")))
                gc.hook_main(json.dumps(self.hook("Stop", last_assistant_message="ok")))
                failed, sent = [b["index"] for m, p, b in fake.calls if p == "/inferenceResults"]
                self.assertEqual(sent, failed + 1)

    def hook_log(self):
        with open(os.path.join(self.data, "hook.log"), encoding="utf-8") as f:
            return f.read()

    def continue_after_a_turn(self, fake, tail=(), **refire):
        """The turn of continued_turn, then its re-fired Stop; `tail` follows it in the transcript."""
        first, more = self.continued_turn()
        path = os.path.join(self.tmp, "t.jsonl")
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt="hi")
            self.transcript(first)
            self.run_hook("Stop", transcript_path=path, last_assistant_message="first reply")
            self.transcript(first + more + list(tail))
            self.run_hook("Stop", **dict(dict(transcript_path=path, last_assistant_message="final reply",
                                              stop_hook_active=True), **refire))
        return [[t["name"] for t in b["traces"]] for m, p, b in fake.calls if p.startswith("/traces/batch")]

    def refire_on_a_clock(self, inflight_age):
        """A re-fired Stop while another Stop of the session holds an inflight entry of that age.
        Returns the seconds it slept and the seconds that passed."""
        fake = self.use_api(FakeApi(ok_responses()))
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt="hi")
            self.run_hook("Stop", last_assistant_message="first reply")
        start = time.time()
        clock, sleeps = [start], []

        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds
        with gc.State("s1") as st:
            st["inflight"] = {"other": start - inflight_age}
        with mock.patch.object(gc.time, "time", lambda: clock[0]), mock.patch.object(gc.time, "sleep", sleep):
            self.run_hook("Stop", last_assistant_message="final reply", stop_hook_active=True)
        self.assertIn(("PATCH", "/inferenceResults/ir_1", {"actualOutput": "final reply"}), fake.calls)
        return sleeps, clock[0] - start

    def test_a_refired_stop_does_not_wait_for_a_killed_stop(self):
        sleeps, _ = self.refire_on_a_clock(gc.INFLIGHT_MAX + 1)
        self.assertEqual(sleeps, [])

    def test_a_refired_stop_waits_at_most_continue_wait(self):
        sleeps, waited = self.refire_on_a_clock(0)
        self.assertEqual(set(sleeps), {0.2})
        self.assertGreater(waited, gc.CONTINUE_WAIT)
        self.assertLessEqual(waited, gc.CONTINUE_WAIT + 0.2 + 1e-6)

    def test_a_refired_stop_keeps_a_newer_last_turn(self):
        newer = {"prompt_id": "p2", "ir_id": "ir_2", "sent": ["gen:m9"]}
        batches = []

        def batch(body):
            batches.append(body)
            if len(batches) == 2:  # the next turn is stored while the re-fired Stop posts its spans
                with gc.State("s1") as st:
                    st["last_turn"] = dict(newer)
            return {}
        responses = ok_responses()
        responses[("POST", "/traces/batch")] = batch
        fake = self.use_api(FakeApi(responses))
        self.assertEqual(self.continue_after_a_turn(fake), [["llm:claude"], ["llm:claude", "tool:Bash"]])
        with gc.State("s1") as st:
            self.assertEqual(st["last_turn"], newer)

    def test_a_refired_stop_of_another_prompt_leaves_the_sent_turn(self):
        fake = self.use_api(FakeApi(ok_responses()))
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt="hi")
            self.run_hook("Stop", last_assistant_message="first reply")
            self.run_hook("Stop", prompt_id="p2", last_assistant_message="other reply", stop_hook_active=True)
        self.assertEqual(fake.paths("PATCH"), [])
        self.assertIn("Stop of session s1 continued a turn that was not sent", self.hook_log())

    def test_a_refired_stop_with_no_prompt_id_continues_the_sent_turn(self):
        fake = self.use_api(FakeApi(ok_responses()))
        queued = {"type": "user", "promptId": "p2", "message": {"content": "next"}}
        batches = self.continue_after_a_turn(fake, tail=[queued], prompt_id=None)
        self.assertEqual(batches, [["llm:claude"], ["llm:claude", "tool:Bash"]])
        patches = [b for m, p, b in fake.calls if (m, p) == ("PATCH", "/inferenceResults/ir_1")]
        self.assertEqual(patches[1:], [{"actualOutput": "final reply"},
                                       {"inputTokens": 15, "outputTokens": 0, "cacheReadInputTokens": 0}])

    def test_a_continued_reply_is_redacted_and_cut(self):
        text, secret = SAMPLES["anthropic_key"]
        fake = self.use_api(FakeApi(ok_responses()))
        self.continue_after_a_turn(fake, last_assistant_message=text + " " + "x" * (gc.MAX_TURN_BYTES + 5000))
        out = next(b["actualOutput"] for m, p, b in fake.calls if m == "PATCH" and "actualOutput" in b)
        self.assertNotIn(secret, out)
        self.assertTrue(out.startswith(gc.REDACTED + " x"), out[:40])
        self.assertIn("[truncated", out)
        self.assertLessEqual(len(out.encode()), gc.MAX_TURN_BYTES + 40)

    def test_a_failed_reply_update_still_sends_the_continued_spans(self):
        def patch(body):
            if "actualOutput" in body:
                raise RuntimeError("PATCH /inferenceResults/ir_1 -> 503")
            return {}
        responses = ok_responses()
        responses[("PATCH", "/inferenceResults/ir_1")] = patch
        fake = self.use_api(FakeApi(responses))
        self.assertEqual(self.continue_after_a_turn(fake), [["llm:claude"], ["llm:claude", "tool:Bash"]])
        self.assertIn("reply not updated for ir_1", self.hook_log())

    def test_a_refired_stop_before_the_first_stop_sends_the_turn(self):
        fake = self.use_api(FakeApi(ok_responses()))
        with mock.patch.object(gc.time, "sleep"):
            self.run_hook("UserPromptSubmit", prompt="hi")
            self.run_hook("Stop", last_assistant_message="final reply", stop_hook_active=True)
            self.run_hook("Stop", last_assistant_message="first reply")
        self.assertEqual(self.sent_turns(fake), [("hi", "final reply", 0)])
        self.assertIn("Stop of session s1 has no stored prompt", self.hook_log())

    def test_a_stop_takes_only_its_own_stored_prompt(self):
        fake = self.use_api(FakeApi(ok_responses()))
        self.run_hook("UserPromptSubmit", prompt="interrupted", prompt_id="p1")
        self.run_hook("Stop", prompt_id="p2", last_assistant_message="ok")
        self.assertEqual(fake.calls, [])
        self.assertIn("Stop of session s1 has no stored prompt", self.hook_log())
        self.run_hook("UserPromptSubmit", prompt="again", prompt_id="p1")
        with gc.State("s1") as st:
            self.assertEqual([(p["prompt_id"], p["prompt"]) for p in st["prompts"]], [("p1", "again")])


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

    def run_sh_without_python(self, stdin, *args, stubs=None):
        """run.sh with a PATH that holds no Python, only the `stubs` (name -> shell script body)."""
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir, exist_ok=True)
        for tool in ("dirname", "uname", "dd"):
            path = shutil.which(tool)
            if path and not os.path.exists(os.path.join(bindir, tool)):
                os.symlink(path, os.path.join(bindir, tool))
        for name, script in (stubs or {}).items():
            with open(os.path.join(bindir, name), "w") as f:
                f.write("#!/bin/sh\n" + script.replace("PYTHON", '"%s"' % sys.executable) + "\n")
            os.chmod(os.path.join(bindir, name), 0o755)
        r = subprocess.run([shutil.which("sh"), os.path.join(PLUGIN, "scripts", "run.sh")] + list(args),
                           env={"PATH": bindir}, cwd=self.tmp, input=stdin, capture_output=True, text=True,
                           timeout=30)
        self.assertEqual(r.returncode, 0)
        return r.stdout

    @unittest.skipUnless(shutil.which("sh"), "needs sh")
    def test_run_sh_without_python_is_silent_except_at_session_start(self):
        for event in ("UserPromptSubmit", "Stop", "SessionEnd"):
            with self.subTest(event=event):
                stdin = json.dumps({"session_id": "s1", "hook_event_name": event, "prompt": '"SessionStart"'})
                self.assertEqual(self.run_sh_without_python(stdin, "hook"), "")
        self.assertEqual(self.run_sh_without_python("{}", "hook"), "")
        for sep in (",", ", "):
            with self.subTest(sep=sep):
                stdin = sep.join(['{"session_id": "s1"', '"hook_event_name":"SessionStart"', '"source":"startup"}'])
                out = self.run_sh_without_python(stdin.replace('":"', '": "') if sep == ", " else stdin, "hook")
                self.assertEqual(len(out.splitlines()), 1)
                self.assertIn("needs Python 3.9 or later", json.loads(out)["systemMessage"])
        self.assertIn("needs Python 3.9", self.run_sh_without_python("", "mute"))

    @unittest.skipIf(os.name == "nt" or not shutil.which("sh"), "needs a POSIX sh")
    def test_run_sh_falls_back_to_python_when_python3_fails_the_version_check(self):
        out = self.run_sh_without_python("", "unmute", self.data,
                                         stubs={"python3": "exit 1", "python": 'exec PYTHON "$@"'})
        self.assertIn("on again for", out)

    @unittest.skipIf(os.name == "nt" or not shutil.which("sh"), "needs a POSIX sh")
    def test_run_sh_falls_back_to_the_py_launcher_with_dash_3(self):
        out = self.run_sh_without_python("", "unmute", self.data,
                                         stubs={"py": '[ "$1" = -3 ] || exit 1\nshift\nexec PYTHON "$@"'})
        self.assertIn("on again for", out)

    def test_manifest_hooks_fit_the_check_budget(self):
        with open(os.path.join(PLUGIN, "hooks", "hooks.json"), encoding="utf-8") as f:
            hooks = json.load(f)["hooks"]
        self.assertEqual(set(hooks), set(gc.HANDLERS))
        for event, groups in hooks.items():
            with self.subTest(event=event):
                (hook,) = [h for g in groups for h in g["hooks"]]
                if event == "SessionStart":
                    self.assertNotIn("async", hook)
                    self.assertGreater(hook["timeout"], gc.CHECK_BUDGET + gc.CLEANUP_BUDGET + 2)
                else:
                    self.assertIs(hook["async"], True)

    def test_manifest_user_config_matches_the_options_the_script_reads(self):
        with open(os.path.join(PLUGIN, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        with open(os.path.join(PLUGIN, "scripts", "galtea_cc.py"), encoding="utf-8") as f:
            read = set(re.findall(r'"CLAUDE_PLUGIN_OPTION_(\w+)"', f.read()))
        self.assertEqual({k.upper() for k in manifest["userConfig"]}, read)
        self.assertNotIn("version", manifest)

    def test_each_skill_runs_only_its_own_command(self):
        for name in ("mute", "unmute"):
            with self.subTest(skill=name):
                with open(os.path.join(PLUGIN, "skills", name, "SKILL.md"), encoding="utf-8") as f:
                    front = dict(line.split(": ", 1) for line in f.read().split("---")[1].strip().splitlines())
                self.assertEqual(front["disable-model-invocation"], "true")
                self.assertRegex(front["allowed-tools"], r'^Bash\(sh [^ ()]*run\.sh" %s \*\)$' % name)


if __name__ == "__main__":
    unittest.main()
