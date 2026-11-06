"""Unit and adversarial tests for the deterministic proof engine.

Run from the plugin root:

    python3 -m unittest discover -s tests -v

These tests need no Hermes installation and never touch the network.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from completion_proof.config import settings_from_mapping
from completion_proof.engine import ProofEngine
from completion_proof.fingerprint import glob_match, hash_file
from completion_proof.models import ConfigError, ScopeError, StoreCorruptError, UnknownError
from completion_proof.store import Store


def python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


class Fixture:
    def __init__(self, base: Path):
        self.base = base
        self.root = base / "proj"
        (self.root / "src").mkdir(parents=True)
        (self.root / "tests").mkdir(parents=True)
        (self.root / "dist").mkdir()
        (self.root / "src" / "app.py").write_text("VALUE = 1\n")
        (self.root / "tests" / "test_app.py").write_text(
            "import unittest\n\nclass T(unittest.TestCase):\n"
            "    def test_ok(self):\n        self.assertTrue(True)\n"
        )
        self.db_path = base / "data.db"
        self.cleanups = []

    def check(self, **overrides):
        value = {
            "id": "unit",
            "description": "fixture",
            "argv": python("print('ok')"),
            "cwd": ".",
            "timeout_seconds": 10,
            "source_globs": ["src/**/*.py"],
            "test_globs": ["tests/**/*.py"],
            "artifact_globs": [],
        }
        value.update(overrides)
        return value

    def settings(self, checks=None, **overrides):
        raw = {
            "workspace": str(self.root),
            "checks": checks if checks is not None else [self.check()],
        }
        raw.update(overrides)
        return settings_from_mapping(raw, base_dir=self.root)

    def engine(self, checks=None, **overrides):
        store = Store.open_path(self.db_path)
        self.cleanups.append(store.close)
        return ProofEngine(self.settings(checks, **overrides), store)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = Fixture(Path(self.tmp.name))
        self.addCleanup(lambda: [cleanup() for cleanup in reversed(self.fx.cleanups)])

    def test_pass_records_scope_and_receipt(self):
        engine = self.fx.engine()
        payload = engine.verify()
        self.assertEqual(payload["summary_status"], "PASS")
        receipt = payload["results"][0]
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["exit_code"], 0)
        self.assertEqual(receipt["input_file_count"], 2)
        self.assertTrue(receipt["input_fingerprint"])
        self.assertIn("2 files", receipt["scope_summary"])
        status = engine.status()
        self.assertEqual(status["checks"][0]["status"], "PASS")
        self.assertTrue(status["checks"][0]["fresh"])

    def test_false_pass_from_text_is_fail(self):
        engine = self.fx.engine(
            checks=[self.fx.check(argv=python("print('all tests passed'); raise SystemExit(1)"))]
        )
        payload = engine.verify()
        self.assertEqual(payload["summary_status"], "FAIL")
        receipt = payload["results"][0]
        self.assertEqual(receipt["status"], "FAIL")
        self.assertIn("passed", receipt["stdout_excerpt"])
        self.assertEqual(engine.status()["checks"][0]["status"], "FAIL")

    def test_timeout_is_unknown_and_child_killed(self):
        marker = self.fx.root / "child_marker.txt"
        code = (
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', "
            f"\"import time; time.sleep(3); open({str(marker)!r}, 'w').write('late')\"])\n"
            "time.sleep(30)\n"
        )
        engine = self.fx.engine(
            checks=[self.fx.check(argv=python(code), timeout_seconds=1)]
        )
        started = time.monotonic()
        payload = engine.verify()
        elapsed = time.monotonic() - started
        self.assertEqual(payload["summary_status"], "UNKNOWN")
        receipt = payload["results"][0]
        self.assertEqual(receipt["status"], "UNKNOWN")
        self.assertTrue(receipt["timed_out"])
        self.assertLess(elapsed, 15, "timeout did not bound the check")
        time.sleep(3.5)
        self.assertFalse(marker.exists(), "child process survived the group kill")

    def test_parent_exit_with_child_holding_pipe_is_bounded_and_unknown(self):
        if os.name != "posix":
            self.skipTest("process-group semantics are POSIX-specific")
        marker = self.fx.root / "child_marker.txt"
        code = (
            "import subprocess, sys\n"
            "subprocess.Popen([sys.executable, '-c', "
            f"\"import time, pathlib; time.sleep(10); "
            f"pathlib.Path({str(marker)!r}).write_text('late')\"])\n"
            "print('parent done', flush=True)\n"
        )
        engine = self.fx.engine(
            checks=[self.fx.check(argv=python(code), timeout_seconds=30)]
        )
        started = time.monotonic()
        payload = engine.verify()
        elapsed = time.monotonic() - started
        receipt = payload["results"][0]
        self.assertEqual(receipt["status"], "UNKNOWN")
        self.assertIn("output_incomplete", receipt["reason"])
        self.assertTrue(receipt["metadata"]["pipe_incomplete"])
        self.assertLess(elapsed, 15, "reader drain was not bounded")
        time.sleep(1.5)
        self.assertFalse(marker.exists(), "inherited child survived process-group kill")

    def test_large_output_hash_covers_the_whole_stream(self):
        import hashlib

        body = "y" * 1_000_000
        code = "import sys; body = 'y' * 1000000; sys.stdout.write(body); sys.stdout.write('END')"
        engine = self.fx.engine(
            checks=[self.fx.check(argv=python(code), timeout_seconds=30)]
        )
        payload = engine.verify()
        receipt = payload["results"][0]
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(
            receipt["stdout_sha256"],
            hashlib.sha256((body + "END").encode()).hexdigest(),
        )
        self.assertEqual(receipt["metadata"]["stdout_total_bytes"], len(body) + 3)
        self.assertTrue(receipt["output_truncated"])
        self.assertTrue(receipt["metadata"]["output_complete"])

    def test_missing_artifact_is_unknown(self):
        engine = self.fx.engine(
            checks=[self.fx.check(artifact_globs=["dist/result.json"])]
        )
        payload = engine.verify()
        self.assertEqual(payload["summary_status"], "UNKNOWN")
        self.assertIn("artifact missing", payload["results"][0]["reason"].lower())

    def test_artifact_produced_by_check_is_hashed(self):
        engine = self.fx.engine(
            checks=[
                self.fx.check(
                    argv=python("open('dist/result.json','w').write('{}')"),
                    artifact_globs=["dist/result.json"],
                )
            ]
        )
        payload = engine.verify()
        self.assertEqual(payload["summary_status"], "PASS")
        files = payload["results"][0]["files"]
        self.assertTrue(any(f["path"] == "dist/result.json" for f in files))

    def test_stale_after_source_edit(self):
        engine = self.fx.engine()
        self.assertEqual(engine.verify()["summary_status"], "PASS")
        (self.fx.root / "src" / "app.py").write_text("VALUE = 2\n")
        row = engine.status()["checks"][0]
        self.assertEqual(row["status"], "STALE")
        self.assertFalse(row["fresh"])
        self.assertNotEqual(row["input_fingerprint"], row["current_fingerprint"])

    def test_stale_after_test_edit(self):
        engine = self.fx.engine()
        self.assertEqual(engine.verify()["summary_status"], "PASS")
        (self.fx.root / "tests" / "test_app.py").write_text("# changed\n")
        self.assertEqual(engine.status()["checks"][0]["status"], "STALE")

    def test_stale_after_check_config_change(self):
        engine = self.fx.engine()
        self.assertEqual(engine.verify()["summary_status"], "PASS")
        other = self.fx.engine(checks=[self.fx.check(argv=python("print('changed')"))])
        self.assertEqual(other.status()["checks"][0]["status"], "STALE")

    def test_source_change_during_check_is_unknown(self):
        engine = self.fx.engine(
            checks=[
                self.fx.check(
                    argv=python("open('src/app.py','w').write('VALUE = 99\\n')")
                )
            ]
        )
        payload = engine.verify()
        self.assertEqual(payload["summary_status"], "UNKNOWN")
        self.assertIn("changed while", payload["results"][0]["reason"])

    def test_empty_scope_is_unknown(self):
        engine = self.fx.engine(
            checks=[self.fx.check(source_globs=["src/missing/**/*.py"], test_globs=[])]
        )
        payload = engine.verify()
        self.assertEqual(payload["summary_status"], "UNKNOWN")
        self.assertIn("scope_pattern_missing", payload["results"][0]["reason"])

    def test_symlink_escape_is_unknown(self):
        outside = Path(self.tmp.name) / "outside.py"
        outside.write_text("SECRET = 1\n")
        link = self.fx.root / "src" / "leak.py"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        engine = self.fx.engine()
        payload = engine.verify()
        self.assertEqual(payload["summary_status"], "UNKNOWN")
        self.assertIn("path_escape", payload["results"][0]["reason"])

    def test_cannot_escape_with_dotdot_glob(self):
        with self.assertRaises(ConfigError) as raised:
            self.fx.settings(checks=[self.fx.check(source_globs=["../*.py"])])
        self.assertTrue(any(".." in error for error in raised.exception.errors))

    def test_file_changed_during_hash_is_unknown(self):
        import types

        target = self.fx.root / "src" / "app.py"
        first = types.SimpleNamespace(st_size=4, st_mtime_ns=1, st_ino=1)
        second = types.SimpleNamespace(st_size=5, st_mtime_ns=2, st_ino=1)
        with mock.patch(
            "completion_proof.fingerprint.Path.stat", side_effect=[first, second] * 3
        ):
            with self.assertRaises(UnknownError) as raised:
                hash_file(target)
        self.assertEqual(raised.exception.reason, "file_changed_during_hash")

    def test_duplicate_receipt_is_annotated_but_never_hides_latest(self):
        engine = self.fx.engine()
        first = engine.verify()["results"][0]
        second = engine.verify()["results"][0]
        self.assertEqual(first["status"], "PASS")
        self.assertTrue(second["duplicate_of"])
        self.assertTrue(second["stored"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(engine.store.stats()["receipts"], 2)
        self.assertEqual(engine.status()["checks"][0]["status"], "PASS")

    def test_output_redaction_and_cap(self):
        code = (
            "print('API_KEY=example-redaction-value-1234567890')\n"
            "print('x' * 10000)\n"
        )
        engine = self.fx.engine(
            checks=[self.fx.check(argv=python(code))],
            max_output_bytes=512,
        )
        receipt = engine.verify()["results"][0]
        self.assertTrue(receipt["output_truncated"])
        self.assertNotIn("super-secret-value", receipt["stdout_excerpt"])
        self.assertIn("[REDACTED]", receipt["stdout_excerpt"])
        self.assertLessEqual(len(receipt["stdout_excerpt"].encode()), 700)

    def test_redacts_json_and_quoted_secret_shapes(self):
        code = (
            "print('{\"password\": \"hunter2\", \"token\": \"abc\"}')\n"
            "print(\"PASSWORD='hunter2' API_KEY=abcdef\")\n"
        )
        engine = self.fx.engine(checks=[self.fx.check(argv=python(code))])
        excerpt = engine.verify()["results"][0]["stdout_excerpt"]
        self.assertNotIn("hunter2", excerpt)
        self.assertNotIn("abcdef", excerpt)
        self.assertIn("[REDACTED]", excerpt)

    def test_store_output_false_keeps_hashes_only(self):
        engine = self.fx.engine(store_output=False)
        receipt = engine.verify()["results"][0]
        self.assertEqual(receipt["stdout_excerpt"], "")
        self.assertTrue(receipt["stdout_sha256"])

    def test_pre_verify_bounded_and_relevant(self):
        engine = self.fx.engine()
        nudge = engine.pre_verify(coding=True, attempt=0, changed_paths=["src/app.py"])
        self.assertIsNotNone(nudge)
        self.assertEqual(nudge["action"], "continue")
        self.assertIn("proof_verify", nudge["message"])
        self.assertIn("tool_call", nudge["message"])
        self.assertIsNone(
            engine.pre_verify(coding=True, attempt=1, changed_paths=["src/app.py"])
        )
        self.assertIsNone(
            engine.pre_verify(coding=False, attempt=0, changed_paths=["src/app.py"])
        )
        self.assertIsNone(
            engine.pre_verify(coding=True, attempt=0, changed_paths=["README.md"])
        )

    def test_pre_verify_silent_after_fresh_pass(self):
        engine = self.fx.engine()
        self.assertEqual(engine.verify()["summary_status"], "PASS")
        self.assertIsNone(
            engine.pre_verify(coding=True, attempt=0, changed_paths=["src/app.py"])
        )

    def test_pre_verify_nudges_after_fail(self):
        engine = self.fx.engine(
            checks=[self.fx.check(argv=python("raise SystemExit(3)"))]
        )
        self.assertEqual(engine.verify()["summary_status"], "FAIL")
        nudge = engine.pre_verify(coding=True, attempt=0, changed_paths=["src/app.py"])
        self.assertIsNotNone(nudge)
        self.assertIn("FAIL", nudge["message"])

    def test_no_checks_never_nudges(self):
        engine = self.fx.engine(checks=[])
        self.assertIsNone(
            engine.pre_verify(coding=True, attempt=0, changed_paths=["src/app.py"])
        )

    def test_workspace_isolation(self):
        other_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(other_tmp.cleanup)
        other = Fixture(Path(other_tmp.name))
        engine_a = self.fx.engine()
        engine_b = other.engine()
        self.assertEqual(engine_a.verify()["summary_status"], "PASS")
        self.assertEqual(engine_b.status()["checks"][0]["status"], "UNKNOWN")
        self.assertEqual(engine_b.verify()["summary_status"], "PASS")
        self.assertEqual(engine_a.store.stats()["receipts"], 1)
        self.assertEqual(engine_b.store.stats()["receipts"], 1)

    def test_store_corrupt_database_is_reported(self):
        bad = Path(self.tmp.name) / "bad.db"
        bad.write_bytes(b"not a sqlite database\x00" * 100)
        with self.assertRaises(StoreCorruptError):
            Store.open_path(bad)

    def test_store_retention(self):
        engine = self.fx.engine()
        for index in range(5):
            engine.settings = self.fx.settings(
                checks=[self.fx.check(argv=python(f"print({index})"))]
            )
            engine.verify()
        before = engine.store.stats()["receipts"]
        self.assertGreaterEqual(before, 1)
        engine.store.gc(retention_days=0, max_per_check=2)
        self.assertLessEqual(engine.store.stats()["receipts"], 2)

    def test_concurrent_store_writes(self):
        store = Store.open_path(self.fx.db_path)
        self.addCleanup(store.close)
        errors = []

        def worker(index: int):
            try:
                for item in range(5):
                    store.add_observation(
                        tool_name=f"t{index}", duration_ms=item, status="ok"
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(store.stats()["observations"], 20)

    def test_report_contains_limits(self):
        engine = self.fx.engine()
        engine.verify()
        report = engine.report()
        self.assertIn("What PASS does not prove", report)
        self.assertIn("not cryptographically", report)


class FingerprintAndConfigTests(unittest.TestCase):
    def test_glob_match(self):
        self.assertTrue(glob_match("src/app.py", "src/**/*.py"))
        self.assertTrue(glob_match("src/deep/app.py", "src/**/*.py"))
        self.assertFalse(glob_match("src/app.txt", "src/**/*.py"))
        self.assertTrue(glob_match("a.py", "*.py"))
        self.assertFalse(glob_match("a/b.py", "*.py"))
        self.assertTrue(glob_match("a/b.py", "**/*.py"))

    def test_config_rejects_unsafe_shapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            cases = [
                {"checks": [{"id": "x", "argv": "echo hi", "source_globs": ["*.py"]}]},
                {"checks": [{"id": "x", "argv": ["echo"], "source_globs": []}]},
                {"checks": [{"id": "bad id", "argv": ["echo"], "source_globs": ["*.py"]}]},
                {"checks": [{"id": "x", "argv": ["echo"], "source_globs": ["/etc/passwd"]}]},
                {"checks": [{"id": "x", "argv": ["echo"], "source_globs": ["../*.py"]}]},
                {"checks": [{"id": "x", "argv": ["echo"], "source_globs": ["*.py"], "shell": True}]},
                {"checks": [{"id": "x", "argv": ["echo"], "source_globs": ["*.py"], "env": {"A": "B"}}]},
                {"checks": [{"id": "x", "argv": ["echo"], "source_globs": ["*.py"]},
                            {"id": "x", "argv": ["true"], "source_globs": ["*.py"]}]},
                {"checks": "not-a-list"},
            ]
            for raw in cases:
                with self.subTest(raw=raw), self.assertRaises(ConfigError):
                    settings_from_mapping(raw, base_dir=base)

    def test_relative_workspace_setting_is_resolved_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "proj"
            (project / "src").mkdir(parents=True)
            (project / "src" / "app.py").write_text("x = 1\n")
            settings = settings_from_mapping(
                {
                    "workspace": "proj",
                    "checks": [
                        {
                            "id": "unit",
                            "argv": python("print('ok')"),
                            "source_globs": ["src/**/*.py"],
                        }
                    ],
                },
                base_dir=root,
            )
            store = Store.open_path(root / "data.db")
            try:
                engine = ProofEngine(settings, store)
                payload = engine.verify()
            finally:
                store.close()
            self.assertEqual(payload["summary_status"], "PASS")

    def test_config_tolerates_unknown_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            warnings = []
            settings_from_mapping(
                {"workspace": tmp, "future_key": 1},
                base_dir=Path(tmp),
                warnings=warnings,
            )
            self.assertTrue(any("future_key" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
