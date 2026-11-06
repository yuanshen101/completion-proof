#!/usr/bin/env python3
"""Capture a reproducible environment record for the evidence bundle."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parent


def run(command, timeout=30):
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"command": command, "error": f"{type(exc).__name__}: {exc}"}


def wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def main() -> int:
    repo = run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"])
    branch = run(["git", "-C", str(REPO_ROOT), "rev-parse", "--abbrev-ref", "HEAD"])
    status = run(["git", "-C", str(REPO_ROOT), "status", "--porcelain"])
    hermes = run(["hermes", "--version"], timeout=60)
    version = (PLUGIN_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    record = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "plugin": {
            "name": "completion-proof",
            "version": version,
            "requires_hermes": ">=0.22.7",
        },
        "git": {
            "commit": repo.get("stdout", ""),
            "branch": branch.get("stdout", ""),
            "working_tree_clean": not bool(status.get("stdout", "")),
        },
        "hermes": {
            "version_output": hermes.get("stdout", ""),
            "error": hermes.get("stderr", ""),
        },
        "python": {
            "version": sys.version.split()[0],
            "executable_kind": "venv" if sys.prefix != sys.base_prefix else "system",
        },
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "wsl": wsl(),
            "windows_native": platform.system() == "Windows",
        },
        "notes": [
            "The test environment is Linux (WSL2); native Windows was NOT tested.",
            "No network was used by the deterministic engine; benchmark records zero socket attempts.",
            "No LLM call is made by the proof engine.",
        ],
    }
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
