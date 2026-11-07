#!/usr/bin/env python3
"""Fresh-install smoke test from the built archive, in a throwaway HERMES_HOME.

Usage:
    python3 scripts/verify_archive.py --archive evidence/package/completion-proof-0.1.5.tar.gz \
        --template-home "${TMPDIR:?}/cp-hermes-template" --out evidence/package/fresh-install.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from completion_proof.sandbox_env import sandbox_env


def run(hermes, home, userhome, project, args, timeout=120, tmpdir=None, extra_env=None):
    values = {
        "HERMES_HOME": str(home),
        "HOME": str(userhome),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if tmpdir:
        values["TMPDIR"] = str(tmpdir)
    if extra_env:
        values.update(extra_env)
    env = sandbox_env(values)
    completed = subprocess.run(
        [hermes, *args],
        cwd=str(project),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    stdout = completed.stdout
    if list(args[:2]) == ["plugins", "list"] and completed.returncode == 0:
        # Keep only this plugin's entry: the full bundled catalog includes many
        # third-party URLs and is irrelevant to the fresh-install proof.
        try:
            entries = json.loads(stdout)
            ours = [entry for entry in entries if entry.get("name") == "completion-proof"]
            stdout = json.dumps(
                {"plugin_count": len(entries), "completion_proof": ours}, indent=2, sort_keys=True
            )
        except ValueError:
            pass
    return {
        "args": args,
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": completed.stderr,
    }


def _hook_test(hermes, home, userhome, project, plugin_dir, tmpdir) -> dict:
    """Run one real one-shot agent turn against the packaged copy."""
    import yaml

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    log_path = tmpdir / "mock_requests.jsonl"
    mock = plugin_dir / "tests" / "fixtures" / "mock_llm_server.py"
    server = subprocess.Popen(
        [sys.executable, str(mock), str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=sandbox_env(
            {
                "MOCK_SCENARIO": "agent_hook",
                "MOCK_LLM_LOG": str(log_path),
                "TMPDIR": str(tmpdir),
            }
        ),
    )
    started = time.monotonic()
    result = {"ok": False, "returncode": None, "nudge_detected": False, "error": ""}
    try:
        line = server.stdout.readline().strip()
        if line != str(port):
            result["error"] = f"mock server did not start: {line!r}"
            return result
        config_path = home / "config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        config["model"]["base_url"] = f"http://127.0.0.1:{port}/v1"
        config_path.write_text(yaml.safe_dump(config, sort_keys=False))
        completed = run(
            hermes,
            home,
            userhome,
            project,
            [
                "-z",
                "Create src/new.py with VALUE = 2. Then say done.",
                "--provider",
                "openai",
                "--model",
                "mock",
                "--yolo",
                "--cli",
            ],
            timeout=150,
            tmpdir=tmpdir,
            extra_env={
                "OPENAI_API_KEY": "dummy",
                "OPENAI_BASE_URL": f"http://127.0.0.1:{port}/v1",
                "MOCK_LLM_LOG": str(log_path),
            },
        )
        result["returncode"] = completed["returncode"]
        result["stdout"] = completed["stdout"][-2000:]
        result["stderr"] = completed["stderr"][-2000:]
        requests = []
        if log_path.exists():
            for line in log_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    requests.append(json.loads(line))
        chat = [
            request for request in requests
            if request.get("path", "").endswith("/chat/completions")
        ]
        joined = "\n".join(
            str(message.get("content") or "")
            for request in chat
            for message in (request.get("body") or {}).get("messages") or []
        )
        result["nudge_detected"] = "[Completion Proof]" in joined
        result["chat_requests"] = len(chat)
        dbs = list((home / "plugin-data").rglob("data.db"))
        if dbs:
            connection = sqlite3.connect(dbs[0])
            try:
                result["receipts"] = connection.execute(
                    "SELECT check_id, status FROM receipts"
                ).fetchall()
                result["verifications"] = connection.execute(
                    "SELECT source, summary_status FROM verifications"
                ).fetchall()
                result["observations"] = [
                    row[0] for row in connection.execute("SELECT tool_name FROM observations")
                ]
            finally:
                connection.close()
        result["ok"] = (
            completed["returncode"] == 0
            and result["nudge_detected"]
            and all(receipt == ("unit", "PASS") for receipt in result.get("receipts", []))
            and bool(result.get("receipts"))
            and ("tool", "PASS") in result.get("verifications", [])
            and "write_file" in result.get("observations", [])
            and "proof_verify" in result.get("observations", [])
        )
        result["elapsed_s"] = round(time.monotonic() - started, 3)
        return result
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        pid_file = home / "gateway.pid"
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text().strip()), 9)
            except (OSError, ValueError):
                pass
        time.sleep(0.5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--template-home", default=os.environ.get("CP_HERMES_TEMPLATE_HOME", ""))
    parser.add_argument("--out", required=True)
    parser.add_argument("--hook-test", action="store_true", help="also exercise native pre_verify against the packaged copy")
    args = parser.parse_args()
    hermes = shutil.which("hermes")
    if not hermes:
        print("hermes not found", file=sys.stderr)
        return 2
    archive = Path(args.archive).resolve()
    if not archive.exists():
        print(f"archive not found: {archive}", file=sys.stderr)
        return 2
    template = Path(args.template_home).resolve() if args.template_home else None
    if template and not (template / "installs").is_dir():
        print(f"template home has no installs/: {template}", file=sys.stderr)
        return 2

    result = {"archive": archive.name, "steps": [], "skipped_reason": ""}
    try:
        tmp_context = tempfile.TemporaryDirectory(prefix="cp-archive-", ignore_cleanup_errors=True)
    except TypeError:  # Python < 3.12
        tmp_context = tempfile.TemporaryDirectory(prefix="cp-archive-")
    with tmp_context as tmp:
        base = Path(tmp)
        tmpdir = base / "tmp"
        tmpdir.mkdir()
        home = base / "home"
        home.mkdir()
        if template:
            os.symlink(template / "installs", home / "installs")
        userhome = base / "userhome"
        userhome.mkdir()
        project = base / "proj"
        (project / "src").mkdir(parents=True)
        (project / "tests").mkdir()
        (project / "src" / "app.py").write_text("VALUE = 1\n")
        (project / "tests" / "test_app.py").write_text(
            "import unittest\n\nclass T(unittest.TestCase):\n"
            "    def test_ok(self):\n        self.assertTrue(True)\n"
        )
        with tarfile.open(archive, "r:gz") as tar:
            try:
                tar.extractall(home / "plugins", filter="data")
            except TypeError:  # Python < 3.12
                tar.extractall(home / "plugins")
        plugin_dir = home / "plugins" / "completion-proof"
        if not plugin_dir.is_dir():
            print("archive did not extract completion-proof/", file=sys.stderr)
            return 2

        import yaml

        config = {
            "updates": {"check": False},
            "model": {
                "provider": "openai",
                "name": "mock",
                "base_url": "http://127.0.0.1:9/v1",
                "api_key": "dummy",
                "streaming": False,
            },
            "agent": {"coding_context": "on"},
            "tools": {"tool_search": {"enabled": "off"}},
            "plugins": {
                "enabled": ["completion-proof"],
                "entries": {
                    "completion-proof": {
                        "enabled": True,
                        "settings": {
                            "workspace": str(project),
                            "checks": [
                                {
                                    "id": "unit",
                                    "argv": [
                                        sys.executable,
                                        "-m",
                                        "unittest",
                                        "discover",
                                        "-s",
                                        "tests",
                                        "-v",
                                    ],
                                    "source_globs": ["src/**/*.py"],
                                    "test_globs": ["tests/**/*.py"],
                                    "timeout_seconds": 30,
                                }
                            ],
                        },
                    }
                },
            }
        }
        (home / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
        invoke = lambda *args: run(  # noqa: E731
            hermes, home, userhome, project, list(args), tmpdir=tmpdir
        )
        result["steps"].append(invoke("plugins", "list", "--json"))
        result["steps"].append(invoke("plugins", "doctor", "completion-proof", "--ci"))
        result["steps"].append(invoke("completion-proof", "verify", "--json"))
        result["steps"].append(invoke("completion-proof", "status", "--json"))
        result["steps"].append(invoke("plugins", "validate", str(plugin_dir), "--json"))
        if args.hook_test:
            result["hook_test"] = _hook_test(
                hermes, home, userhome, project, plugin_dir, tmpdir
            )
            # The CLI config used the model port placeholder; the hook test writes
            # its own config with the live mock port.
        pid_file = home / "gateway.pid"
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text().strip()), 9)
            except (OSError, ValueError):
                pass

    result["ok"] = all(step["returncode"] == 0 for step in result["steps"][:4]) and all(
        step["returncode"] == 0 for step in result["steps"] if step["args"][:2] != ["plugins", "doctor"]
    )
    if args.hook_test:
        result["ok"] = result["ok"] and bool(result.get("hook_test", {}).get("ok"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "ok": result["ok"], "steps": len(result["steps"])}, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
