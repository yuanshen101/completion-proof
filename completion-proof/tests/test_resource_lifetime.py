"""Resource-lifetime regressions for owned workspace root descriptors.

Linux-only FD counting via /proc/self/fd; no host limit changes and no real
secrets. The same fixture is exported against 0.1.3 to show the leak.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from completion_proof.fingerprint import compute_fingerprint, secure_hash_file, select_files

try:
    from completion_proof.fingerprint import open_root_handle
except ImportError:
    open_root_handle = None

from tests.test_engine import Fixture, python


def fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))


class ResourceLifetimeTests(unittest.TestCase):
    def setUp(self):
        if os.name != "posix" or not Path("/proc/self/fd").is_dir():
            self.skipTest("requires POSIX /proc/self/fd")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = Fixture(Path(self.tmp.name))
        (self.fx.root / "src" / "app.py").write_text("x=1\n")

    def _settle(self):
        # Let the runtime/observation background work reach a quiescent state.
        time.sleep(0.02)

    def test_owned_handle_closes_on_byte_budget_gate_failures_and_recovers(self):
        engine = self.fx.engine(max_hash_bytes=1024, gate_deadline_ms=25)
        self.assertEqual(engine.verify()["summary_status"], "PASS")
        engine.status()  # warm the read connection and ledger
        self._settle()
        (self.fx.root / "src" / "app.py").write_text("x" * 2048 + "\n")
        before = fd_count()
        for _ in range(100):
            engine.pre_verify(
                coding=True, attempt=0, changed_paths=["src/app.py"]
            )
        after = fd_count()
        self.assertLessEqual(after - before, 3, "owned root FD leaked on byte-budget failures")
        (self.fx.root / "src" / "app.py").write_text("x=1\n")
        self.assertEqual(engine.verify()["summary_status"], "PASS")

    def test_owned_handle_closes_on_deadline_and_repeated_selection_errors(self):
        check = self.fx.settings().checks[0]
        before = fd_count()
        for _ in range(100):
            with self.assertRaises(Exception):
                compute_fingerprint(
                    self.fx.root,
                    check,
                    include_artifacts=False,
                    deadline=time.monotonic() - 1,
                )
        after = fd_count()
        self.assertLessEqual(after - before, 3, "owned root FD leaked on deadline failures")

    def test_owned_handle_closes_on_fifo_hash_errors(self):
        selection = select_files(
            self.fx.root, [("source", ["src/**/*.py"])], [], max_files=10
        )
        target = self.fx.root / selection.files[0][0]
        target.unlink()
        os.mkfifo(target)
        before = fd_count()
        try:
            for _ in range(100):
                with self.assertRaises(Exception):
                    secure_hash_file(self.fx.root, selection.files[0][0])
            after = fd_count()
            self.assertLessEqual(after - before, 3, "root FD leaked on FIFO errors")
        finally:
            target.unlink()
            target.write_text("x=1\n")

    def test_run_check_owner_closes_exactly_once_and_preserves_synthetic_victim(self):
        engine = self.fx.engine(max_hash_bytes=1024)
        (self.fx.root / "src" / "app.py").write_text("x" * 2048 + "\n")
        victim_path = self.fx.base / "synthetic-victim.txt"
        victim_path.write_text("victim\n")
        state = {"closes": 0, "victim_fd": None, "handle": None}

        class TrackingHandle:
            def __init__(self, handle):
                self._handle = handle

            def __getattr__(self, name):
                return getattr(self._handle, name)

            def close(self):
                state["closes"] += 1
                self._handle.close()
                if state["closes"] == 1:
                    # The first owner close frees the root fd; this real
                    # allocation reuses the number. A second close would hit it.
                    state["victim_fd"] = os.open(str(victim_path), os.O_RDONLY)

        def factory(root):
            handle = open_root_handle(root)
            state["handle"] = handle
            return TrackingHandle(handle)

        try:
            with mock.patch("completion_proof.fingerprint.open_root_handle", side_effect=factory):
                payload = engine.verify()
            self.assertEqual(payload["summary_status"], "UNKNOWN")
            self.assertEqual(state["closes"], 1, "run_check closed the owner handle more than once")
            self.assertIsNotNone(state["victim_fd"])
            os.fstat(state["victim_fd"])  # must still be valid after the owner close
        finally:
            if state["victim_fd"] is not None:
                try:
                    os.close(state["victim_fd"])
                except OSError:
                    pass
            if state["handle"] is not None:
                state["handle"].close()

    def test_borrowed_handle_survives_hash_error(self):
        if open_root_handle is None:
            self.fail("open_root_handle is missing")
        check = self.fx.settings().checks[0]
        (self.fx.root / "src" / "app.py").write_text("x" * 2048 + "\n")
        handle = open_root_handle(self.fx.root)
        try:
            with self.assertRaises(Exception):
                compute_fingerprint(
                    self.fx.root,
                    check,
                    include_artifacts=False,
                    max_bytes=1024,
                    root_handle=handle,
                )
            os.fstat(handle.fd)  # borrowed descriptor remains the caller's
        finally:
            handle.close()

    def test_caller_owned_handle_stays_open_until_owner_closes(self):
        if open_root_handle is None:
            self.fail("open_root_handle is missing")
        check = self.fx.settings().checks[0]
        handle = open_root_handle(self.fx.root)
        try:
            compute_fingerprint(
                self.fx.root,
                check,
                include_artifacts=False,
                root_handle=handle,
            )
            # The caller-owned descriptor must still be usable after compute.
            os.fstat(handle.fd)
        finally:
            handle.close()
