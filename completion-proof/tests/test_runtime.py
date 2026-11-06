"""Tests for the Hermes-facing runtime with a fake plugin context.

These tests do not require Hermes; they inject a minimal ``plugins.plugin_storage``
module so the public storage import path behaves like the real one. A separate
integration test exercises an actual installed Hermes copy.
"""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from completion_proof.runtime import Runtime, register


class FakeCtx:
    def __init__(self, settings, base: Path):
        self.settings = settings
        self.base = base
        self.tools = {}
        self.hooks = {}
        self.cli_commands = {}
        self.commands = {}
        self.written = []

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def set_config(self, key, value):
        self.settings[key] = value
        self.written.append((key, value))

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
        self.cli_commands[name] = {
            "help": help,
            "setup_fn": setup_fn,
            "handler_fn": handler_fn,
            "description": description,
        }

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands[name] = {"handler": handler, "description": description}


@contextlib.contextmanager
def fake_storage(db_path: Path):
    fake_plugins = types.ModuleType("plugins")
    fake_plugins.__path__ = []  # mark as package for submodule import
    fake_storage_mod = types.ModuleType("plugins.plugin_storage")

    def plugin_db(name):
        return sqlite3.connect(str(db_path), check_same_thread=False)

    fake_storage_mod.plugin_db = plugin_db
    sys.modules["plugins"] = fake_plugins
    sys.modules["plugins.plugin_storage"] = fake_storage_mod
    try:
        yield
    finally:
        sys.modules.pop("plugins.plugin_storage", None)
        sys.modules.pop("plugins", None)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "proj"
        (self.root / "src").mkdir(parents=True)
        (self.root / "tests").mkdir(parents=True)
        (self.root / "src" / "app.py").write_text("VALUE = 1\n")
        (self.root / "tests" / "test_app.py").write_text("# test\n")
        self.db = self.base / "data.db"
        self.settings = {
            "workspace": str(self.root),
            "checks": [
                {
                    "id": "unit",
                    "argv": [sys.executable, "-c", "print('ok')"],
                    "source_globs": ["src/**/*.py"],
                    "test_globs": ["tests/**/*.py"],
                }
            ],
        }

    def _runtime(self, settings=None):
        storage = fake_storage(self.db)
        storage.__enter__()
        self.addCleanup(storage.__exit__, None, None, None)
        ctx = FakeCtx(dict(settings or self.settings), self.base)
        runtime = Runtime(ctx)
        self.addCleanup(self._close, runtime)
        return runtime, ctx

    def _registered(self, settings=None):
        storage = fake_storage(self.db)
        storage.__enter__()
        self.addCleanup(storage.__exit__, None, None, None)
        ctx = FakeCtx(dict(settings or self.settings), self.base)
        register(ctx)
        return ctx

    @staticmethod
    def _close(runtime):
        runtime.close()

    def test_registers_tools_hooks_cli_and_slash(self):
        ctx = self._registered()
        self.assertIn("completion-proof", ctx.tools["proof_verify"]["toolset"])
        self.assertEqual(
            sorted(ctx.tools), ["proof_list_checks", "proof_report", "proof_status", "proof_verify"]
        )
        self.assertEqual(sorted(ctx.hooks), ["post_tool_call", "pre_verify"])
        self.assertIn("completion-proof", ctx.cli_commands)
        self.assertIn("proof", ctx.commands)

    def test_tool_list_status_verify_report(self):
        runtime, _ctx = self._runtime()
        listed = json.loads(runtime.tool_list_checks({}))
        self.assertTrue(listed["ok"])
        self.assertEqual(listed["checks"][0]["id"], "unit")
        status = json.loads(runtime.tool_status({}))
        self.assertEqual(status["checks"][0]["status"], "UNKNOWN")
        verified = json.loads(runtime.tool_verify({}))
        self.assertEqual(verified["summary_status"], "PASS")
        self.assertTrue(verified["ok"])
        status = json.loads(runtime.tool_status({}))
        self.assertEqual(status["checks"][0]["status"], "PASS")
        report = json.loads(runtime.tool_report({"format": "markdown"}))
        self.assertIn("Completion Proof report", report["report"])

    def test_tool_rejects_unknown_check_and_ignores_command_inputs(self):
        runtime, _ctx = self._runtime()
        result = json.loads(runtime.tool_verify({"check_ids": ["nope"]}))
        self.assertFalse(result["ok"])
        self.assertIn("unknown check", result["error"])
        # Extra keys cannot smuggle a command: the handler ignores them.
        result = json.loads(
            runtime.tool_verify(
                {"check_ids": ["unit"], "argv": ["rm", "-rf", "/"], "cwd": "/", "shell": True}
            )
        )
        self.assertEqual(result["summary_status"], "PASS")
        self.assertEqual(result["results"][0]["metadata"]["argv"][-1], "print('ok')")

    def test_slash_and_cli(self):
        runtime, _ctx = self._runtime()
        self.assertIn("Completion Proof report", runtime.slash("status"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = runtime.cli(["verify", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["summary_status"], "PASS")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = runtime.cli(["status"])
        self.assertEqual(code, 0)
        self.assertIn("PASS", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = runtime.cli(["checks"])
        self.assertEqual(code, 0)
        self.assertIn("unit", out.getvalue())

    def test_disabled_check_cannot_execute_via_tool_or_cli(self):
        marker = self.base / "disabled_marker.txt"
        settings = dict(self.settings)
        settings["checks"] = [
            {
                "id": "unit",
                "argv": [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('ran')"],
                "source_globs": ["src/**/*.py"],
                "test_globs": ["tests/**/*.py"],
                "enabled": False,
            }
        ]
        runtime, _ctx = self._runtime(settings)
        result = json.loads(runtime.tool_verify({"check_ids": ["unit"]}))
        self.assertFalse(result["ok"])
        self.assertIn("disabled", result["error"])
        self.assertFalse(marker.exists())
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = runtime.cli(["verify", "--check", "unit"])
        self.assertEqual(code, 2)
        self.assertIn("disabled", stderr.getvalue())
        self.assertFalse(marker.exists())

    def test_config_error_degrades_without_crashing(self):
        ctx = self._registered(
            {"workspace": str(self.root), "checks": [{"id": "bad", "argv": "echo hi"}]}
        )
        # Registration still happens so the user can see the error through the tools.
        self.assertIn("proof_verify", ctx.tools)
        result = json.loads(ctx.tools["proof_verify"]["handler"]({}))
        self.assertFalse(result["ok"])
        self.assertIn("invalid configuration", result["error"])

    def test_init_write_uses_fixed_template(self):
        runtime, ctx = self._runtime({"workspace": str(self.root), "checks": []})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = runtime.cli(["init", "--write"])
        self.assertEqual(code, 0)
        self.assertTrue(any(key == "checks" for key, _value in ctx.written))
        written = ctx.written[-1][1]
        self.assertEqual(written[0]["id"], "tests")
        self.assertIsInstance(written[0]["argv"], list)

    def test_hook_bounded(self):
        runtime, _ctx = self._runtime()
        nudge = runtime.on_pre_verify(
            coding=True, attempt=0, changed_paths=["src/app.py"]
        )
        self.assertIsNotNone(nudge)
        self.assertIsNone(
            runtime.on_pre_verify(coding=True, attempt=1, changed_paths=["src/app.py"])
        )
        # Non-code turn: never nudges.
        self.assertIsNone(
            runtime.on_pre_verify(coding=False, attempt=0, changed_paths=["src/app.py"])
        )

    def test_observation_cannot_create_evidence(self):
        runtime, _ctx = self._runtime()
        runtime.on_post_tool_call(
            tool_name="terminal",
            args={"command": "pytest -q"},
            result=json.dumps({"exit_code": 0, "output": "1 passed"}),
            duration_ms=12,
            status="ok",
        )
        state = runtime.engine.status()
        self.assertEqual(state["checks"][0]["status"], "UNKNOWN")
        nudge = runtime.on_pre_verify(coding=True, attempt=0, changed_paths=["src/app.py"])
        self.assertIsNotNone(nudge)

    def test_cp07_observation_hook_does_not_wait_on_sqlite_lock(self):
        runtime, _ctx = self._runtime()
        locker = sqlite3.connect(str(self.db), timeout=1)
        locker.execute("BEGIN EXCLUSIVE")
        try:
            started = time.monotonic()
            runtime.on_post_tool_call(
                tool_name="terminal",
                args={"command": "pytest -q"},
                result=json.dumps({"exit_code": 0}),
                duration_ms=1,
                status="ok",
            )
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.5, "post_tool_call waited on the SQLite lock")
        finally:
            locker.rollback()
            locker.close()
        runtime.flush_observations()

    def test_observation_is_untrusted_context(self):
        runtime, _ctx = self._runtime()
        runtime.on_post_tool_call(
            tool_name="terminal",
            args={"command": "pytest -q"},
            result=json.dumps({"exit_code": 0, "output": "1 passed"}),
            duration_ms=12,
            status="ok",
        )
        self.assertTrue(runtime.flush_observations())
        state = runtime.engine.raw_state()
        self.assertEqual(state["observations_untrusted"][0]["tool_name"], "terminal")
        self.assertEqual(
            state["observations_untrusted"][0]["trust"], "UNTRUSTED_CONTEXT"
        )


if __name__ == "__main__":
    unittest.main()
