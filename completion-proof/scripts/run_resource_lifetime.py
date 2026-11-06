#!/usr/bin/env python3
"""Compare owned-root FD lifetime on 0.1.3 vs current with the same fixture."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parent
TEST = PLUGIN_ROOT / "tests" / "test_resource_lifetime.py"
DEFAULT_TESTS = (
    "test_owned_handle_closes_on_byte_budget_gate_failures_and_recovers",
    "test_owned_handle_closes_on_deadline_and_repeated_selection_errors",
)


def run(root: Path, names: list[str], timeout: int) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERMES_")}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(root)})
    args = [sys.executable, "-m", "unittest"]
    args += [f"tests.test_resource_lifetime.ResourceLifetimeTests.{name}" for name in names]
    args.append("-v")
    started = time.monotonic()
    try:
        done = subprocess.run(
            args, cwd=str(root), env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout,
        )
        return {
            "returncode": done.returncode, "timed_out": False,
            "stdout": done.stdout, "stderr": done.stderr,
            "elapsed_s": round(time.monotonic() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": None, "timed_out": True,
            "stdout": (exc.stdout or ""), "stderr": (exc.stderr or ""),
            "elapsed_s": round(time.monotonic() - started, 3),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-ref", default="0c3c313")
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "evidence" / "resource-lifetime"))
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "archive", args.old_ref],
        stdin=subprocess.DEVNULL, capture_output=True, check=False,
    )
    if archive.returncode != 0 or not archive.stdout:
        print(f"cannot export old ref {args.old_ref}", file=sys.stderr)
        return 2
    old_root = Path(tempfile.mkdtemp(prefix="cp-fd-old-"))
    try:
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as tar:
            tar.extractall(old_root, filter="data")
        old_plugin = old_root / "completion-proof"
        if not (old_plugin / "completion_proof").is_dir():
            old_plugin = old_root
        (old_plugin / "tests").mkdir(exist_ok=True)
        shutil.copy2(TEST, old_plugin / "tests" / "test_resource_lifetime.py")
        old = run(old_plugin, list(DEFAULT_TESTS), args.timeout)
    finally:
        shutil.rmtree(old_root, ignore_errors=True)
    new = run(PLUGIN_ROOT, list(DEFAULT_TESTS), args.timeout)
    for name, result in (("old", old), ("new", new)):
        (out / f"{name}.txt").write_text(
            (result.get("stdout") or "") + "\n" + (result.get("stderr") or ""),
            encoding="utf-8",
        )
        print(f"{name}: returncode={result['returncode']} timed_out={result['timed_out']} "
              f"elapsed={result['elapsed_s']}s")
    summary = {
        "schema": "completion-proof-resource-lifetime-v1",
        "old_ref": args.old_ref,
        "tests": list(DEFAULT_TESTS),
        "old_failed": old["timed_out"] or old["returncode"] != 0,
        "new_passed": (not new["timed_out"]) and new["returncode"] == 0,
        "old_timed_out": old["timed_out"],
        "new_timed_out": new["timed_out"],
        "expectation": "old leaks FDs/fails; new stable/green with the same fixture",
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["old_failed"] and summary["new_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
