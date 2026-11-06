"""Real-Hermes integration tests (opt-in; no network, no operator profile).

These tests load the plugin through the actual Hermes CLI in a throwaway
``HERMES_HOME``, run ``plugins doctor`` / ``plugins validate``, exercise the real
CLI subcommand, and drive a real one-shot agent turn against a local mock
OpenAI-compatible server to fire the native ``pre_verify`` hook.

The fresh Hermes home bootstraps an install tree; to keep this fast, point
``CP_HERMES_TEMPLATE_HOME`` at an existing throwaway home whose ``installs``
directory can be symlinked. The tests are skipped otherwise:

    CP_HERMES_TEMPLATE_HOME="${TMPDIR:?}/cp-hermes-template" \
    python3 -m unittest tests.test_hermes_integration -v
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from completion_proof.sandbox_env import sandbox_env

MOCK_SERVER = PLUGIN_ROOT / "tests" / "fixtures" / "mock_llm_server.py"

# Honor TMPDIR/tempfile defaults; never write test files into a real profile.
_TEST_TMP_ROOT = Path(tempfile.mkdtemp(prefix="completion-proof-tests-"))
tempfile.tempdir = str(_TEST_TMP_ROOT)
os.environ["TMPDIR"] = str(_TEST_TMP_ROOT)
atexit.register(shutil.rmtree, _TEST_TMP_ROOT, True)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class HermesIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hermes = shutil.which("hermes")
        if not cls.hermes:
            raise unittest.SkipTest("hermes CLI not installed")
        template = os.environ.get("CP_HERMES_TEMPLATE_HOME", "").strip()
        if not template:
            raise unittest.SkipTest("set CP_HERMES_TEMPLATE_HOME to a prepared throwaway home")
        cls.template = Path(template).resolve()
        if not (cls.template / "installs").is_dir():
            raise unittest.SkipTest(f"{cls.template}/installs is missing")

    def setUp(self):
        try:
            self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        except TypeError:  # Python < 3.12
            self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        os.symlink(self.template / "installs", self.home / "installs")
        self.userhome = self.base / "userhome"
        self.userhome.mkdir()
        self.plugins = self.home / "plugins"
        self.plugins.mkdir()
        shutil.copytree(
            PLUGIN_ROOT,
            self.plugins / "completion-proof",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "site", "evidence", "dist"),
        )
        self.proj = self.base / "proj"
        (self.proj / "src").mkdir(parents=True)
        (self.proj / "tests").mkdir()
        (self.proj / "src" / "app.py").write_text("VALUE = 1\n")
        (self.proj / "tests" / "test_app.py").write_text(
            "import unittest\n\nclass T(unittest.TestCase):\n"
            "    def test_ok(self):\n        self.assertTrue(True)\n"
        )
        self.evidence = Path(os.environ.get("CP_EVIDENCE_DIR", "")).resolve() if os.environ.get("CP_EVIDENCE_DIR") else None
        self.model_port = _free_port()
        self._write_config(self.model_port)
        self.server = None
        self.addCleanup(self._stop_gateway)

    def _write_config(self, port: int, *, tool_search: str = "off"):
        check = {
            "id": "unit",
            "description": "fixture unittest",
            "argv": [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            "cwd": ".",
            "timeout_seconds": 30,
            "source_globs": ["src/**/*.py"],
            "test_globs": ["tests/**/*.py"],
            "artifact_globs": [],
        }
        config = {
            "updates": {"check": False},
            "model": {
                "provider": "openai",
                "name": "mock",
                "base_url": f"http://127.0.0.1:{port}/v1",
                "api_key": "dummy",
                "streaming": False,
            },
            "agent": {"coding_context": "on"},
            "tools": {"tool_search": {"enabled": tool_search}},
            "plugins": {
                "enabled": ["completion-proof"],
                "entries": {
                    "completion-proof": {
                        "enabled": True,
                        "settings": {"workspace": str(self.proj), "checks": [check]},
                    }
                },
            },
        }
        import yaml

        (self.home / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))

    def _env(self):
        # Deliberate allowlist: no operator credentials, profile switches or
        # unrelated HERMES_* variables reach the child. Dummy local model values
        # are explicit.
        return sandbox_env(
            {
                "HERMES_HOME": str(self.home),
                "HOME": str(self.userhome),
                "TMPDIR": str(_TEST_TMP_ROOT),
                "PYTHONDONTWRITEBYTECODE": "1",
                "OPENAI_API_KEY": "dummy",
                "OPENAI_BASE_URL": f"http://127.0.0.1:{self.model_port}/v1",
                "MOCK_LLM_LOG": str(self.base / "mock_requests.jsonl"),
            }
        )

    def _save(self, relative: str, text: str) -> None:
        if self.evidence is None:
            return
        target = self.evidence / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def _run(self, *args, timeout=90):
        completed = subprocess.run(
            [self.hermes, *args],
            cwd=str(self.proj),
            env=self._env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return completed

    def _stop_gateway(self):
        if self.server:
            self.server.terminate()
            try:
                self.server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server.kill()
        pid_file = self.home / "gateway.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 9)
            except (OSError, ValueError):
                pass
        # Let managed LSP/child processes release files before TemporaryDirectory
        # cleanup; cleanup errors are ignored as well.
        time.sleep(1.0)

    def _start_mock(self, scenario="simple"):
        port = self.model_port
        env = sandbox_env(
            {
                "MOCK_SCENARIO": scenario,
                "MOCK_LLM_LOG": str(self.base / "mock_requests.jsonl"),
                "TMPDIR": str(_TEST_TMP_ROOT),
            }
        )
        self.server = subprocess.Popen(
            [sys.executable, str(MOCK_SERVER), str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        line = self.server.stdout.readline().strip()
        self.assertEqual(line, str(port))
        time.sleep(0.2)

    def test_plugin_doctor_and_validate(self):
        doctor = self._run("plugins", "doctor", str(self.plugins / "completion-proof"), "--ci")
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        self.assertIn("4 tool(s), 2 hook(s)", doctor.stdout)
        self._save("install/doctor.txt", doctor.stdout + doctor.stderr)
        validate = self._run("plugins", "validate", str(self.plugins / "completion-proof"), "--json")
        self.assertEqual(validate.returncode, 0, validate.stderr)
        self._save("install/validate.json", validate.stdout)
        report = json.loads(validate.stdout)
        checks = {item["name"]: item for item in report["checks"]}
        self.assertTrue(checks["security scan"]["ok"], checks["security scan"])
        self.assertEqual(checks["security scan"]["detail"], "safe")
        self.assertTrue(checks["declared tools"]["ok"])
        self.assertTrue(checks["declared hooks"]["ok"])

    def test_cli_verify_status_and_staleness(self):
        listed = self._run("plugins", "list", "--json")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        names = [entry["name"] for entry in json.loads(listed.stdout)]
        self.assertIn("completion-proof", names)

        verified = self._run("completion-proof", "verify", "--json")
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self._save("install/cli-verify.json", verified.stdout)
        payload = json.loads(verified.stdout)
        self.assertEqual(payload["summary_status"], "PASS")
        self.assertEqual(payload["results"][0]["status"], "PASS")

        fresh = self._run("completion-proof", "status", "--json")
        self.assertEqual(fresh.returncode, 0, fresh.stderr)
        self._save("install/cli-status-fresh.json", fresh.stdout)
        row = json.loads(fresh.stdout)["checks"][0]
        self.assertEqual(row["status"], "PASS")
        self.assertTrue(row["fresh"])

        (self.proj / "src" / "app.py").write_text("VALUE = 2\n")
        stale = self._run("completion-proof", "status", "--json")
        self.assertEqual(stale.returncode, 0, stale.stderr)
        self._save("install/cli-status-stale.json", stale.stdout)
        row = json.loads(stale.stdout)["checks"][0]
        self.assertEqual(row["status"], "STALE")
        self.assertFalse(row["fresh"])

    def test_real_agent_turn_fires_pre_verify_and_records_receipt(self):
        self._write_config(self.model_port, tool_search="off")
        self._start_mock("agent_hook")
        completed = self._run(
            "-z",
            "Create src/new.py with VALUE = 2. Then say done.",
            "--provider",
            "openai",
            "--model",
            "mock",
            "--yolo",
            "--cli",
            timeout=150,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self._save("hooks/agent-turn.txt", completed.stdout + completed.stderr)
        self.assertTrue((self.proj / "src" / "new.py").exists(), "mock tool call did not run")

        log = self.base / "mock_requests.jsonl"
        requests = [json.loads(line) for line in log.read_text().splitlines() if line]
        summary_lines = []
        for request in requests:
            if not request.get("path", "").endswith("/chat/completions"):
                continue
            body = request.get("body") or {}
            messages = []
            for message in body.get("messages") or []:
                if not isinstance(message, dict) or message.get("role") == "system":
                    continue
                messages.append(
                    {
                        "role": message.get("role"),
                        "content": str(message.get("content") or "")[:4000],
                    }
                )
            joined = "\n".join(str(m.get("content") or "") for m in body.get("messages") or [])
            summary_lines.append(
                json.dumps(
                    {
                        "n": request.get("n"),
                        "path": request.get("path"),
                        "stream": body.get("stream"),
                        "nudge_detected": "[Completion Proof]" in joined,
                        "tool_names": [
                            t.get("function", {}).get("name")
                            for t in (body.get("tools") or [])
                            if isinstance(t, dict)
                        ],
                        "messages": messages,
                    },
                    sort_keys=True,
                )
            )
        self._save("hooks/mock_requests.jsonl", "\n".join(summary_lines) + "\n")
        chat_requests = [r for r in requests if r["path"].endswith("/chat/completions")]
        nudged = [
            r for r in chat_requests
            if "[Completion Proof]" in "\n".join(
                str(m.get("content") or "") for m in r["body"].get("messages", [])
            )
        ]
        self.assertTrue(nudged, "the native pre_verify hook never fired")

        dbs = list((self.home / "plugin-data").rglob("data.db"))
        self.assertEqual(len(dbs), 1)
        connection = sqlite3.connect(dbs[0])
        try:
            receipts = connection.execute(
                "SELECT check_id, status FROM receipts"
            ).fetchall()
            observations = connection.execute(
                "SELECT tool_name FROM observations"
            ).fetchall()
            verifications = connection.execute(
                "SELECT source, summary_status FROM verifications"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(receipts, [("unit", "PASS")])
        self.assertIn(("tool", "PASS"), verifications)
        observed_names = {name for (name,) in observations}
        self.assertIn("write_file", observed_names)
        self.assertIn("proof_verify", observed_names)
        self._save(
            "hooks/receipts.json",
            json.dumps(
                {
                    "receipts": receipts,
                    "verifications": verifications,
                    "observations": sorted(observed_names),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )


if __name__ == "__main__":
    unittest.main()
