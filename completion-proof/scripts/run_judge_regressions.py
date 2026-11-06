#!/usr/bin/env python3
"""Run the CP01-CP08 judge suite against current and pre-fix code.

The pre-fix tree is exported from git (no shared worktree mutation) and the
current judge test is copied in. Old failures/timeouts are expected evidence;
new code must pass. No publication or external service is used.
"""

from __future__ import annotations

import argparse
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
TEST = PLUGIN_ROOT / "tests" / "test_judge_regressions.py"


def run_suite(root: Path, timeout: int, tests: list[str] | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERMES_")}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(root)})
    names = tests or ["tests.test_judge_regressions"]
    unittest_args = []
    for name in names:
        if name == "tests.test_judge_regressions":
            unittest_args.append(name)
        else:
            unittest_args.append(
                f"tests.test_judge_regressions.JudgeRegressionTests.{name}"
            )
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", *unittest_args, "-v"],
            cwd=str(root),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "returncode": completed.returncode,
            "timed_out": False,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "elapsed_s": round(time.monotonic() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": None,
            "timed_out": True,
            "stdout": (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            "stderr": (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or ""),
            "elapsed_s": round(time.monotonic() - started, 3),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-ref", default="027c954")
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "evidence" / "judge-regressions"))
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument(
        "--tests",
        default="",
        help="comma-separated JudgeRegressionTests methods; default runs the full module",
    )
    args = parser.parse_args()
    selected = [item.strip() for item in args.tests.split(",") if item.strip()]
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    archive = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "archive", args.old_ref],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )
    if archive.returncode != 0 or not archive.stdout:
        print(f"cannot export old ref {args.old_ref}", file=sys.stderr)
        return 2
    old_root = Path(tempfile.mkdtemp(prefix="cp-judge-old-"))
    try:
        with tarfile.open(fileobj=__import__("io").BytesIO(archive.stdout), mode="r:") as tar:
            tar.extractall(old_root, filter="data")
        old_plugin = old_root / "completion-proof"
        if not (old_plugin / "completion_proof").is_dir():
            old_plugin = old_root
        tests_dir = old_plugin / "tests"
        tests_dir.mkdir(exist_ok=True)
        shutil.copy2(TEST, tests_dir / "test_judge_regressions.py")
        if selected:
            old_runs = [run_suite(old_plugin, args.timeout, [name]) for name in selected]
            old = {
                "returncode": 0 if all(run["returncode"] == 0 and not run["timed_out"] for run in old_runs) else 1,
                "timed_out": any(run["timed_out"] for run in old_runs),
                "stdout": "\n".join(run["stdout"] for run in old_runs),
                "stderr": "\n".join(run["stderr"] for run in old_runs),
                "elapsed_s": round(sum(run["elapsed_s"] for run in old_runs), 3),
            }
        else:
            old = run_suite(old_plugin, args.timeout)
    finally:
        shutil.rmtree(old_root, ignore_errors=True)

    if selected:
        new_runs = [run_suite(PLUGIN_ROOT, args.timeout, [name]) for name in selected]
        new = {
            "returncode": 0 if all(run["returncode"] == 0 and not run["timed_out"] for run in new_runs) else 1,
            "timed_out": any(run["timed_out"] for run in new_runs),
            "stdout": "\n".join(run["stdout"] for run in new_runs),
            "stderr": "\n".join(run["stderr"] for run in new_runs),
            "elapsed_s": round(sum(run["elapsed_s"] for run in new_runs), 3),
        }
    else:
        new = run_suite(PLUGIN_ROOT, args.timeout)
    for name, result in (("old", old), ("new", new)):
        (out / f"{name}.txt").write_text(
            (result.get("stdout") or "") + "\n" + (result.get("stderr") or ""), encoding="utf-8"
        )
        print(f"{name}: returncode={result['returncode']} timed_out={result['timed_out']} "
              f"elapsed={result['elapsed_s']}s")
    old_failed = old["timed_out"] or old["returncode"] not in (0,)
    new_passed = (not new["timed_out"]) and new["returncode"] == 0
    # Count test outcomes from unittest output.
    def count(text: str) -> int:
        match = re.search(r"Ran (\d+) tests", text or "")
        return int(match.group(1)) if match else 0

    summary = {
        "schema": "completion-proof-judge-regressions-v1",
        "old_ref": args.old_ref,
        "old_failed": old_failed,
        "new_passed": new_passed,
        "old_timed_out": old["timed_out"],
        "new_timed_out": new["timed_out"],
        "old_tests": len(selected) if selected else count(old.get("stdout", "") + old.get("stderr", "")),
        "new_tests": len(selected) if selected else count(new.get("stdout", "") + new.get("stderr", "")),
        "expectation": "old fails or times out; new passes",
        "scope": "CP01-CP08 synthetic fixtures only; no secrets or external services",
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if old_failed and new_passed else 1


if __name__ == "__main__":
    sys.exit(main())
