"""Judge-derived adversarial regressions CP01-CP08.

These tests target the concrete BLOCK findings against the pre-fix code. Run with:

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_judge_regressions -v

The same file is used by `scripts/run_judge_regressions.py` against the old
commit to record FAIL-old / PASS-new evidence.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from completion_proof.config import settings_from_mapping
from completion_proof.engine import ProofEngine, filtered_environment
from completion_proof.fingerprint import compute_fingerprint, select_files

try:
    from completion_proof.fingerprint import secure_hash_file
except ImportError:  # pre-CP06 old code
    secure_hash_file = None
from completion_proof.models import ConfigError, UnknownError
from completion_proof.runtime import Runtime
from completion_proof.store import Store

from tests.test_engine import Fixture, python
from tests.test_runtime import FakeCtx, fake_storage


class JudgeRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = Fixture(__import__("pathlib").Path(self.tmp.name))

    def test_cp01_child_environment_is_minimal_allowlist(self):
        synthetic = {
            "DATABASE_URL": "postgres://synthetic",
            "GITHUB_PAT": "synthetic-token",
            "GOOGLE_APPLICATION_CREDENTIALS": "/synthetic/creds.json",
            "PYTHONPATH": "/synthetic/injection",
            "HERMES_YOLO_MODE": "1",
            "HERMES_NONINTERACTIVE": "1",
            "OPENAI_API_KEY": "synthetic",
            "HTTP_PROXY": "http://synthetic-proxy",
        }
        code = (
            "import json, os; print(json.dumps({k: os.environ.get(k) for k in "
            + repr(list(synthetic) + ["PATH", "HOME"]) + "}))"
        )
        with mock.patch.dict(os.environ, synthetic, clear=False):
            engine = self.fx.engine(checks=[self.fx.check(argv=python(code))])
            receipt = engine.verify()["results"][0]
        self.assertEqual(receipt["status"], "PASS")
        child = json.loads(receipt["stdout_excerpt"])
        self.assertTrue(child.get("PATH"))
        self.assertTrue(child.get("HOME"))
        for key in synthetic:
            self.assertIsNone(child.get(key), f"forbidden env {key} leaked to child")

    def test_cp01_filtered_environment_function_is_allowlist(self):
        synthetic = {
            "DATABASE_URL": "postgres://synthetic",
            "GITHUB_PAT": "synthetic",
            "GOOGLE_APPLICATION_CREDENTIALS": "/synthetic",
            "PYTHONPATH": "/synthetic",
            "HERMES_YOLO_MODE": "1",
            "HERMES_NONINTERACTIVE": "1",
            "OPENAI_API_KEY": "synthetic",
            "HTTP_PROXY": "http://synthetic",
        }
        with mock.patch.dict(os.environ, synthetic, clear=False):
            env = filtered_environment()
        self.assertTrue(env.get("PATH"))
        self.assertTrue(env.get("HOME"))
        for key in synthetic:
            self.assertFalse(key in env, f"forbidden env {key} leaked")

    def test_cp02_latest_execution_status_survives_dedupe(self):
        flag = self.fx.base / "external_flag.txt"
        code = (
            "import pathlib, sys; "
            f"sys.exit(0 if pathlib.Path({str(flag)!r}).read_text().strip() == 'pass' else 1)"
        )
        engine = self.fx.engine(checks=[self.fx.check(argv=python(code))])
        for value in ("fail", "pass", "fail"):
            flag.write_text(value + "\n")
            engine.verify()
        state = engine.status()["checks"][0]
        self.assertEqual(state["status"], "FAIL")
        self.assertFalse(state["fresh"])
        self.assertGreaterEqual(engine.store.stats()["receipts"], 3)

    def test_cp02_pass_fail_pass_latest_is_pass(self):
        flag = self.fx.base / "external_flag2.txt"
        code = (
            "import pathlib, sys; "
            f"sys.exit(0 if pathlib.Path({str(flag)!r}).read_text().strip() == 'pass' else 1)"
        )
        engine = self.fx.engine(checks=[self.fx.check(argv=python(code))])
        for value in ("pass", "fail", "pass"):
            flag.write_text(value + "\n")
            engine.verify()
        self.assertEqual(engine.status()["checks"][0]["status"], "PASS")

    def test_cp03_escaped_child_keeps_engine_return_bounded(self):
        if os.name != "posix":
            self.skipTest("process groups are POSIX-specific")
        pid_file = self.fx.base / "escaped.pid"
        code = (
            "import pathlib, subprocess, sys\n"
            "child = subprocess.Popen([sys.executable, '-c', "
            "'import time; time.sleep(30)'], start_new_session=True, "
            "stdout=sys.stdout, stderr=sys.stderr)\n"
            f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))\n"
            "print('parent done', flush=True)\n"
        )
        engine = self.fx.engine(
            checks=[self.fx.check(argv=python(code), timeout_seconds=5)]
        )
        started = time.monotonic()
        try:
            payload = engine.verify()
            elapsed = time.monotonic() - started
            receipt = payload["results"][0]
            self.assertEqual(receipt["status"], "UNKNOWN")
            self.assertTrue(receipt["metadata"]["pipe_incomplete"])
            self.assertLess(elapsed, 8, "escaped child made the engine hang")
        finally:
            if pid_file.exists():
                try:
                    os.kill(int(pid_file.read_text()), signal.SIGKILL)
                except (OSError, ValueError):
                    pass

    def test_cp04_disabled_check_cannot_execute(self):
        marker = self.fx.base / "disabled_marker.txt"
        engine = self.fx.engine(
            checks=[
                self.fx.check(
                    argv=python(f"open({str(marker)!r}, 'w').write('ran')"),
                    enabled=False,
                )
            ]
        )
        with self.assertRaises(ConfigError):
            engine.verify(["unit"])
        self.assertFalse(marker.exists(), "disabled check executed via explicit id")

    def test_cp05_shared_ledger_is_workspace_scoped(self):
        other_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(other_tmp.cleanup)
        other = Fixture(__import__("pathlib").Path(other_tmp.name))
        engine_a = self.fx.engine()
        self.assertEqual(engine_a.verify()["summary_status"], "PASS")
        store_b = Store.open_path(self.fx.db_path)
        self.addCleanup(store_b.close)
        settings_b = settings_from_mapping(
            {"workspace": str(other.root), "checks": [self.fx.check()]},
            base_dir=other.root,
        )
        engine_b = ProofEngine(settings_b, store_b)
        row = engine_b.status()["checks"][0]
        self.assertNotEqual(row["status"], "PASS")
        self.assertFalse(row["fresh"])

    def test_cp06r_root_replacement_after_selection_is_unknown(self):
        outside = self.fx.base / "outside-root"
        (outside / "src").mkdir(parents=True)
        (outside / "src" / "app.py").write_text("OUTSIDE = True\n")
        saved = self.fx.base / "workspace-saved"
        real_select = select_files

        def race(*args, **kwargs):
            result = real_select(*args, **kwargs)
            self.fx.root.rename(saved)
            self.fx.root.symlink_to(outside, target_is_directory=True)
            return result

        try:
            with mock.patch(
                "completion_proof.fingerprint.select_files", side_effect=race
            ):
                with self.assertRaises(UnknownError) as caught:
                    compute_fingerprint(
                        self.fx.root,
                        self.fx.settings().checks[0],
                        include_artifacts=False,
                    )
            self.assertIn("workspace_replaced", str(caught.exception))
        finally:
            try:
                self.fx.root.unlink()
            except OSError:
                pass
            if saved.exists():
                saved.rename(self.fx.root)

    def test_cp06r_actual_verify_does_not_pass_outside_root(self):
        outside = self.fx.base / "outside-verify"
        (outside / "src").mkdir(parents=True)
        (outside / "src" / "app.py").write_text("OUTSIDE = True\n")
        saved = self.fx.base / "workspace-verify-saved"
        marker = self.fx.base / "outside_was_hashed.txt"
        real_select = select_files
        real_hash = secure_hash_file

        def race(*args, **kwargs):
            result = real_select(*args, **kwargs)
            self.fx.root.rename(saved)
            self.fx.root.symlink_to(outside, target_is_directory=True)
            return result

        def guarded(*args, **kwargs):
            value = real_hash(*args, **kwargs)
            with open(marker, "a", encoding="utf-8") as handle:
                handle.write(args[1] + "\n")
            return value

        try:
            engine = self.fx.engine()
            with mock.patch(
                "completion_proof.fingerprint.select_files", side_effect=race
            ), mock.patch(
                "completion_proof.fingerprint.secure_hash_file", side_effect=guarded
            ):
                payload = engine.verify()
            self.assertEqual(payload["summary_status"], "UNKNOWN")
            self.assertIn("workspace_replaced", payload["results"][0]["reason"])
            self.assertFalse(marker.exists(), "verify hashed the replaced outside root")
        finally:
            try:
                self.fx.root.unlink()
            except OSError:
                pass
            if saved.exists():
                saved.rename(self.fx.root)

    def test_cp06r_direct_hash_refuses_swapped_root(self):
        selection = select_files(
            self.fx.root, [("source", ["src/**/*.py"])], [], max_files=10
        )
        relative = selection.files[0][0]
        outside = self.fx.base / "outside-direct"
        (outside / "src").mkdir(parents=True)
        (outside / "src" / "app.py").write_text("OUTSIDE = True\n")
        saved = self.fx.base / "workspace-direct-saved"
        self.fx.root.rename(saved)
        try:
            self.fx.root.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(UnknownError):
                secure_hash_file(self.fx.root, relative)
        finally:
            try:
                self.fx.root.unlink()
            except OSError:
                pass
            saved.rename(self.fx.root)

    def test_cp06_secure_hash_refuses_swapped_parent(self):
        link_target = self.fx.base / "outside"
        link_target.mkdir()
        (link_target / "leak.py").write_text("SECRET = 'outside'\n")
        if secure_hash_file is None:
            self.fail("secure_hash_file is missing on the pre-fix code")
        selection = select_files(
            self.fx.root,
            [("source", ["src/**/*.py"])],
            [],
            max_files=10,
        )
        relative = selection.files[0][0]
        src = self.fx.root / "src"
        moved = self.fx.root / "src_original"
        src.rename(moved)
        try:
            src.symlink_to(link_target, target_is_directory=True)
            with self.assertRaises(UnknownError):
                secure_hash_file(self.fx.root, relative)
        finally:
            try:
                src.unlink()
            except OSError:
                pass
            moved.rename(src)

    def test_cp06_sensitive_path_is_refused(self):
        (self.fx.root / ".env").write_text("SYNTHETIC=1\n")
        engine = self.fx.engine(
            checks=[self.fx.check(source_globs=["**/*"], test_globs=[])]
        )
        payload = engine.verify()
        self.assertEqual(payload["summary_status"], "UNKNOWN")
        self.assertIn("sensitive_path", payload["results"][0]["reason"])

    def test_cp07a_fifo_after_selection_does_not_block(self):
        if os.name != "posix":
            self.skipTest("FIFO is POSIX-specific")
        selection = select_files(
            self.fx.root, [("source", ["src/**/*.py"])], [], max_files=10
        )
        relative = selection.files[0][0]
        target = self.fx.root / relative
        target.unlink()
        os.mkfifo(target)
        started = time.monotonic()
        try:
            with self.assertRaises(UnknownError):
                secure_hash_file(self.fx.root, relative)
            self.assertLess(time.monotonic() - started, 1.0)
        finally:
            target.unlink()
            target.write_text("VALUE = 1\n")

    def test_cp07a_pre_verify_with_injected_fifo_returns_promptly(self):
        if os.name != "posix":
            self.skipTest("FIFO is POSIX-specific")
        real_select = select_files

        def race(*args, **kwargs):
            result = real_select(*args, **kwargs)
            target = self.fx.root / "src" / "app.py"
            target.unlink()
            os.mkfifo(target)
            return result

        try:
            with mock.patch(
                "completion_proof.fingerprint.select_files", side_effect=race
            ):
                started = time.monotonic()
                payload = self.fx.engine().pre_verify(
                    coding=True,
                    attempt=0,
                    changed_paths=["src/app.py"],
                )
                elapsed = time.monotonic() - started
            self.assertIsNotNone(payload)
            self.assertLess(elapsed, 1.0)
        finally:
            target = self.fx.root / "src" / "app.py"
            if target.exists():
                target.unlink()
            target.write_text("VALUE = 1\n")

    def test_cp07_sparse_large_file_is_bounded_before_read(self):
        large = self.fx.root / "src" / "large.bin"
        with large.open("wb") as handle:
            handle.truncate(512 * 1024 * 1024)
        engine = self.fx.engine(
            checks=[self.fx.check(source_globs=["src/large.bin"], test_globs=[])],
            max_hash_bytes=1024,
        )
        started = time.monotonic()
        payload = engine.verify()
        elapsed = time.monotonic() - started
        self.assertEqual(payload["summary_status"], "UNKNOWN")
        self.assertIn("hash_budget", payload["results"][0]["reason"])
        self.assertLess(elapsed, 2.0)

    def test_cp07_gate_deadline_returns_bounded_unknown(self):
        engine = self.fx.engine()
        engine.verify()
        started = time.monotonic()
        state = engine.status(deadline_monotonic=time.monotonic() - 1)
        elapsed = time.monotonic() - started
        self.assertEqual(state["checks"][0]["status"], "UNKNOWN")
        self.assertLess(elapsed, 1.0)

    def test_cp07_post_tool_call_does_not_wait_on_sqlite_lock(self):
        db = self.fx.base / "runtime.db"
        storage = fake_storage(db)
        storage.__enter__()
        try:
            ctx = FakeCtx(
                {
                    "workspace": str(self.fx.root),
                    "checks": [self.fx.check()],
                },
                self.fx.base,
            )
            runtime = Runtime(ctx)
            self.addCleanup(runtime.close)
            locker = __import__("sqlite3").connect(str(db), timeout=1)
            locker.execute("BEGIN EXCLUSIVE")
            try:
                started = time.monotonic()
                runtime.on_post_tool_call(
                    tool_name="terminal",
                    args={"command": "pytest -q"},
                    result='{"exit_code": 0}',
                    duration_ms=1,
                    status="ok",
                )
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 0.5)
            finally:
                locker.rollback()
                locker.close()
            runtime.flush_observations()
        finally:
            storage.__exit__(None, None, None)

    def test_cp07b_gate_does_not_wait_on_writer_plus_observation_lock(self):
        db = self.fx.base / "gate-lock.db"
        storage = fake_storage(db)
        storage.__enter__()
        try:
            settings = {
                "workspace": str(self.fx.root),
                "gate_deadline_ms": 25,
                "checks": [
                    {
                        "id": "unit",
                        "argv": [sys.executable, "-c", "print('ok')"],
                        "source_globs": ["src/**/*.py"],
                        "test_globs": ["tests/**/*.py"],
                    }
                ],
            }
            ctx = FakeCtx(settings, self.fx.base)
            runtime = Runtime(ctx)
            event = threading.Event()
            original = runtime.engine.store.add_observation

            def observed(**kwargs):
                event.set()
                return original(**kwargs)

            runtime.engine.store.add_observation = observed
            writer = __import__("sqlite3").connect(str(db), timeout=0.1)
            writer.execute("BEGIN IMMEDIATE")
            try:
                runtime.on_post_tool_call(
                    tool_name="terminal",
                    args={"command": "pytest -q"},
                    result='{"exit_code": 0}',
                    duration_ms=1,
                    status="ok",
                )
                self.assertTrue(event.wait(1.0), "observation worker did not reach the store")
                started = time.monotonic()
                runtime.on_pre_verify(
                    coding=True,
                    attempt=0,
                    changed_paths=["src/app.py"],
                )
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 0.25)
            finally:
                writer.rollback()
                writer.close()
                runtime.close()
        finally:
            storage.__exit__(None, None, None)

    def test_cp08_missing_declared_test_scope_is_unknown(self):
        engine = self.fx.engine(
            checks=[
                self.fx.check(
                    test_globs=["tests/missing/**/*.py"],
                    source_globs=["src/**/*.py"],
                )
            ]
        )
        payload = engine.verify()
        self.assertEqual(payload["summary_status"], "UNKNOWN")
        self.assertIn("scope_pattern_missing", payload["results"][0]["reason"])

    def test_cp08_missing_declared_source_file_is_unknown(self):
        engine = self.fx.engine(
            checks=[
                self.fx.check(
                    source_globs=["src/missing.py"],
                    test_globs=[],
                )
            ]
        )
        self.assertEqual(engine.verify()["summary_status"], "UNKNOWN")

    def test_cp08_source_only_scope_is_legitimate(self):
        engine = self.fx.engine(
            checks=[self.fx.check(source_globs=["src/**/*.py"], test_globs=[])]
        )
        self.assertEqual(engine.verify()["summary_status"], "PASS")

    def test_cp08_optional_scope_is_explicit(self):
        engine = self.fx.engine(
            checks=[
                self.fx.check(
                    test_globs=["tests/missing/**/*.py"],
                    source_globs=["src/**/*.py"],
                    allow_missing_patterns=True,
                )
            ]
        )
        self.assertEqual(engine.verify()["summary_status"], "PASS")


if __name__ == "__main__":
    unittest.main()
