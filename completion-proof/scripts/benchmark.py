#!/usr/bin/env python3
"""Reproducible benchmark: direct command baseline vs the Completion Proof engine.

Measurements are deterministic-engine measurements, not full agent runs. No LLM
call is made and no network request is attempted (a socket guard records any
attempt; the engine has none). Raw per-iteration rows are written to JSON and
CSV; charts and percentages are derived by scripts/render_site.py, never typed by
hand.

Usage:
    python3 scripts/benchmark.py --iterations 20 --seed 20261105 \
        --out-dir evidence/benchmark
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import resource
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path
from typing import Any, Dict, List

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from completion_proof.config import settings_from_mapping
from completion_proof.engine import ProofEngine, filtered_environment
from completion_proof.store import Store

try:
    import psutil
except ImportError:  # optional: fall back to /proc for RSS
    psutil = None


def _process_tree_rss_kb() -> float:
    if psutil is not None:
        try:
            process = psutil.Process()
            total = process.memory_info().rss
            for child in process.children(recursive=True):
                try:
                    total += child.memory_info().rss
                except (psutil.Error, OSError):
                    pass
            return total / 1024.0
        except (psutil.Error, OSError):
            return 0.0
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return float(line.split()[1])
    except OSError:
        pass
    return 0.0


def _child_cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return float(usage.ru_utime + usage.ru_stime)


class RssSampler(threading.Thread):
    """Sample process-tree RSS while an operation runs."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self._stop = threading.Event()
        self.samples: List[float] = []

    def run(self) -> None:
        while not self._stop.is_set():
            value = _process_tree_rss_kb()
            if value >= 0:
                self.samples.append(value)
            self._stop.wait(0.01)

    def stop(self) -> None:
        self._stop.set()
        self.join(timeout=2.0)


def make_fixture(root: Path, file_count: int) -> None:
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    for index in range(file_count):
        (root / "src" / f"module_{index:03d}.py").write_text(
            f"VALUE_{index} = {index}\n"
        )
    (root / "tests" / "test_fixture.py").write_text(
        "import unittest\n\nclass T(unittest.TestCase):\n"
        "    def test_ok(self):\n        self.assertTrue(True)\n"
    )


def check_config() -> Dict[str, Any]:
    return {
        "id": "unit",
        "description": "benchmark fixture",
        "argv": [sys.executable, "-c", "print('benchmark-ok')"],
        "cwd": ".",
        "timeout_seconds": 30,
        "source_globs": ["src/**/*.py"],
        "test_globs": ["tests/**/*.py"],
        "artifact_globs": [],
    }


def settings(root: Path, *, dedupe: int = 0):
    raw = {
        "workspace": str(root),
        "checks": [check_config()],
        "dedupe_window_seconds": dedupe,
        "max_receipts_per_check": 10000,
        "retention_days": 0,
    }
    return settings_from_mapping(raw, base_dir=root)


class NullStore:
    """Store-shaped no-op used for the observe-only benchmark condition."""

    def insert_verification(self, payload):
        return None

    def update_verification(self, payload):
        return None

    def insert_receipt(self, receipt):
        return None

    def find_recent_run_key(self, run_key, since_iso):
        return None

    def stats(self):
        return {"receipts": 0, "verifications": 0, "observations": 0, "schema_version": 1}


def run_direct(root: Path, argv: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=str(root),
        env=filtered_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )


def timed(fn):
    started = time.perf_counter_ns()
    cpu_started = time.process_time_ns()
    result = fn()
    return result, (time.perf_counter_ns() - started) / 1_000_000, (
        time.process_time_ns() - cpu_started
    ) / 1_000_000


def peak_memory_pass(operation, iterations: int) -> List[float]:
    peaks: List[float] = []
    for _ in range(iterations):
        tracemalloc.start()
        operation()
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks.append(peak / 1024.0)
    return peaks


def measure(
    condition: str,
    operation,
    *,
    iterations: int,
    db_path: Path | None = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for index in range(iterations):
        db_before = db_path.stat().st_size if db_path and db_path.exists() else 0
        rss_before = _process_tree_rss_kb()
        child_cpu_before = _child_cpu_seconds()
        sampler = RssSampler()
        sampler.start()
        _, wall_ms, parent_cpu_ms = timed(operation)
        sampler.stop()
        child_cpu_ms = (_child_cpu_seconds() - child_cpu_before) * 1000.0
        db_after = db_path.stat().st_size if db_path and db_path.exists() else 0
        rss_peak = max(sampler.samples or [rss_before])
        rows.append(
            {
                "condition": condition,
                "iteration": index,
                "wall_ms": round(wall_ms, 3),
                "cpu_ms": round(parent_cpu_ms, 3),  # backward-compatible parent CPU
                "parent_cpu_ms": round(parent_cpu_ms, 3),
                "child_cpu_ms": round(max(0.0, child_cpu_ms), 3),
                "total_cpu_ms": round(max(0.0, parent_cpu_ms + child_cpu_ms), 3),
                "rss_baseline_kb": round(rss_before, 3),
                "rss_peak_kb": round(rss_peak, 3),
                "rss_delta_kb": round(max(0.0, rss_peak - rss_before), 3),
                "db_bytes_after": db_after,
                "db_bytes_delta": db_after - db_before,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20261105)
    parser.add_argument("--files", type=int, default=40)
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "evidence" / "benchmark"))
    args = parser.parse_args()
    iterations = max(1, min(args.iterations, 200))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    network_attempts = {"count": 0}
    real_socket = socket.socket

    def guarded_socket(*socket_args, **socket_kwargs):
        network_attempts["count"] += 1
        raise OSError("network is blocked in the Completion Proof benchmark")

    with tempfile.TemporaryDirectory(prefix="cp-bench-") as tmp:
        root = Path(tmp) / "proj"
        make_fixture(root, args.files)
        db_path = Path(tmp) / "data.db"
        store = Store.open_path(db_path)
        engine = ProofEngine(settings(root), store)
        argv = [sys.executable, "-c", "print('benchmark-ok')"]

        socket.socket = guarded_socket  # type: ignore[assignment]
        try:
            direct_rows = measure(
                "direct_command_baseline",
                lambda: run_direct(root, argv),
                iterations=iterations,
            )
            observe_engine = ProofEngine(settings(root), NullStore())
            observe_rows = measure(
                "engine_verify_observe_no_store",
                lambda: observe_engine.verify(),
                iterations=iterations,
            )
            engine_rows = measure(
                "engine_verify_enabled",
                lambda: engine.verify(),
                iterations=iterations,
                db_path=db_path,
            )
            # A fresh receipt exists now; measure the read path and the gate path.
            status_rows = measure(
                "engine_status_fresh",
                lambda: engine.status(),
                iterations=iterations,
            )
            (root / "src" / "module_000.py").write_text("VALUE_0 = 999\n")
            gate_rows = measure(
                "engine_pre_verify_stale_gate",
                lambda: engine.pre_verify(
                    coding=True, attempt=0, changed_paths=["src/module_000.py"]
                ),
                iterations=iterations,
            )
        finally:
            socket.socket = real_socket  # type: ignore[assignment]

        direct_peaks = peak_memory_pass(
            lambda: run_direct(root, argv), iterations
        )
        engine_peaks = peak_memory_pass(lambda: engine.verify(), iterations)
        observe_peaks = peak_memory_pass(lambda: observe_engine.verify(), iterations)
        for row, peak in zip(direct_rows, direct_peaks):
            row["python_peak_kb"] = round(peak, 3)
        for row, peak in zip(observe_rows, observe_peaks):
            row["python_peak_kb"] = round(peak, 3)
        for row, peak in zip(engine_rows, engine_peaks):
            row["python_peak_kb"] = round(peak, 3)

        all_rows = direct_rows + observe_rows + engine_rows + status_rows + gate_rows
        store_stats = store.stats()
        db_bytes = db_path.stat().st_size if db_path.exists() else 0
        store.close()

    try:
        git_commit = subprocess.run(
            ["git", "-C", str(PLUGIN_ROOT.parent), "rev-parse", "HEAD"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        git_commit = ""
    from completion_proof.version import __version__

    environment = {
        "git_commit": git_commit,
        "plugin_version": __version__,
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count(),
        "seed": args.seed,
        "files_per_fixture": args.files,
        "iterations": iterations,
        "network_attempts_observed": network_attempts["count"],
        "llm_calls_by_engine": 0,
        "full_agent_tokens": "not_measured",
        "full_agent_nudges_can_add_turns": True,
        "measurement_scope": (
            "deterministic engine operations; child command CPU included per row; "
            "no Hermes agent turn, no model request"
        ),
        "agent_run_measurements": "not_measured",
        "fixture": "generated source/test files plus a short Python command that exits 0",
        "resource_method": (
            "process-tree RSS sampled every 10 ms with psutil (parent + recursive children); "
            "parent CPU via time.process_time_ns; child CPU via resource.getrusage(RUSAGE_CHILDREN) "
            "deltas; RSS is resident memory, not peak-per-process; python_peak_kb is a separate "
            "tracemalloc Python-allocation pass"
        ),
        "psutil_version": getattr(psutil, "__version__", "unavailable"),
        "child_process_maxrss_kb": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        "cpu_scope_note": (
            "Parent and child CPU are reported separately; total_cpu_ms is their sum. "
            "RSS sampling includes engine-local child checks, not a full Hermes agent."
        ),
        "db_bytes": db_bytes,
        "store_stats": store_stats,
        "command": (
            f"python3 scripts/benchmark.py --iterations {iterations} "
            f"--seed {args.seed} --files {args.files}"
        ),
        "cwd": ".",
    }
    payload = {
        "schema": "completion-proof-benchmark-v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": environment,
        "rows": all_rows,
        "summary": {},
    }
    raw_json = out_dir / "raw.json"
    raw_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (out_dir / "raw.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "condition",
            "iteration",
            "wall_ms",
            "cpu_ms",
            "parent_cpu_ms",
            "child_cpu_ms",
            "total_cpu_ms",
            "rss_baseline_kb",
            "rss_peak_kb",
            "rss_delta_kb",
            "db_bytes_after",
            "db_bytes_delta",
            "python_peak_kb",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(all_rows)
    print(json.dumps({"raw_json": str(raw_json), "rows": len(all_rows), "environment": environment}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
