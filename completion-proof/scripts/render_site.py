#!/usr/bin/env python3
"""Derive charts and a small data file from committed benchmark raw JSON.

No number on the marketing page is typed by hand: this script reads
``evidence/benchmark/raw.json`` and writes deterministic SVGs plus
``site/assets/data.js``. Run ``scripts/benchmark.py`` first.

Usage:
    python3 scripts/render_site.py [--raw evidence/benchmark/raw.json]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parent
CONDITIONS = [
    "direct_command_baseline",
    "engine_verify_observe_no_store",
    "engine_verify_enabled",
    "engine_status_fresh",
    "engine_pre_verify_stale_gate",
]
LABELS = {
    "direct_command_baseline": "Direct command",
    "engine_verify_observe_no_store": "Observe (no ledger)",
    "engine_verify_enabled": "Engine verify",
    "engine_status_fresh": "Status read",
    "engine_pre_verify_stale_gate": "Gate (stale)",
}


def pct(values: List[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    index = (len(ordered) - 1) * fraction
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    weight = index - low
    return round(ordered[low] * (1 - weight) + ordered[high] * weight, 3)


def summarize(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_condition: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_condition.setdefault(str(row.get("condition")), []).append(row)
    result = []
    for condition in CONDITIONS:
        items = by_condition.get(condition, [])
        if not items:
            continue
        wall = [float(item["wall_ms"]) for item in items]
        cpu = [float(item.get("cpu_ms", 0)) for item in items]
        parent_cpu = [float(item.get("parent_cpu_ms", item.get("cpu_ms", 0))) for item in items]
        child_cpu = [float(item.get("child_cpu_ms", 0)) for item in items]
        total_cpu = [float(item.get("total_cpu_ms", 0)) for item in items]
        peaks = [float(item["python_peak_kb"]) for item in items if item.get("python_peak_kb") is not None]
        rss = [float(item["rss_peak_kb"]) for item in items if item.get("rss_peak_kb") is not None]
        result.append(
            {
                "condition": condition,
                "label": LABELS.get(condition, condition),
                "count": len(items),
                "wall_p50_ms": pct(wall, 0.50),
                "wall_p95_ms": pct(wall, 0.95),
                "wall_mean_ms": round(statistics.fmean(wall), 3),
                "cpu_p50_ms": pct(cpu, 0.50),
                "parent_cpu_p50_ms": pct(parent_cpu, 0.50),
                "child_cpu_p50_ms": pct(child_cpu, 0.50),
                "total_cpu_p50_ms": pct(total_cpu, 0.50),
                "python_peak_p50_kb": pct(peaks, 0.50) if peaks else None,
                "python_peak_p95_kb": pct(peaks, 0.95) if peaks else None,
                "rss_peak_p50_kb": pct(rss, 0.50) if rss else None,
                "rss_peak_p95_kb": pct(rss, 0.95) if rss else None,
                "db_bytes_growth_sum": sum(int(item.get("db_bytes_delta", 0)) for item in items),
            }
        )
    return result


def bar_chart(
    summaries: List[Dict[str, Any]],
    value_key: str,
    title: str,
    unit: str,
    width: int = 720,
    height: int = 320,
) -> str:
    values = [float(item.get(value_key) or 0) for item in summaries]
    maximum = max(values + [0.001])
    margin_left = 190
    chart_width = width - margin_left - 120
    bar_height = 34
    gap = 16
    body = []
    for index, item in enumerate(summaries):
        value = float(item.get(value_key) or 0)
        y = 48 + index * (bar_height + gap)
        bar = chart_width * (value / maximum)
        body.append(
            f'<text x="{margin_left - 12}" y="{y + 22}" text-anchor="end" '
            f'fill="#c3cee8" font-family="Inter, system-ui, sans-serif" font-size="13">'
            f"{_xml(item['label'])}</text>"
        )
        body.append(
            f'<rect x="{margin_left}" y="{y}" width="{max(2, bar):.1f}" height="{bar_height}" '
            f'rx="5" fill="#7ce2b5" opacity="{0.95 - index * 0.12:.2f}" />'
        )
        body.append(
            f'<text x="{margin_left + bar + 8}" y="{y + 22}" fill="#e8ecf8" '
            f'font-family="Inter, system-ui, sans-serif" font-size="12">'
            f"{value:.2f} {unit}</text>"
        )
    height = max(height, 60 + len(summaries) * (bar_height + gap))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{_xml(title)}">'
        f'<rect width="{width}" height="{height}" fill="#121a31" rx="12" />'
        f'<text x="24" y="30" fill="#e8ecf8" font-family="Inter, system-ui, sans-serif" '
        f'font-size="15" font-weight="600">{_xml(title)}</text>'
        + "".join(body)
        + "</svg>\n"
    )


def _xml(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", default=str(REPO_ROOT / "evidence" / "benchmark" / "raw.json"))
    parser.add_argument("--site", default=str(PLUGIN_ROOT / "site"))
    args = parser.parse_args()
    raw_path = Path(args.raw)
    if not raw_path.exists():
        print(f"missing benchmark raw data: {raw_path}", file=sys.stderr)
        return 2
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    rows = payload.get("rows") or []
    summaries = summarize(rows)
    if not summaries:
        print("no benchmark rows found", file=sys.stderr)
        return 2

    site = Path(args.site)
    charts = site / "assets" / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    wall_svg = bar_chart(summaries, "wall_p50_ms", "Wall-clock p50 per operation (ms)", "ms")
    memory_svg = bar_chart(
        summaries,
        "python_peak_p50_kb",
        "Python allocation peak p50 (KiB, tracemalloc)",
        "KiB",
    )
    rss_svg = bar_chart(
        summaries,
        "rss_peak_p50_kb",
        "Process-tree RSS peak p50 (KiB, psutil sampling)",
        "KiB",
    )
    (charts / "wall.svg").write_text(wall_svg, encoding="utf-8")
    (charts / "memory.svg").write_text(memory_svg, encoding="utf-8")
    (charts / "rss.svg").write_text(rss_svg, encoding="utf-8")
    # Keep the derived charts next to the raw benchmark inputs as well, so the
    # evidence bundle is self-contained outside the site tree.
    evidence_charts = raw_path.parent / "charts"
    evidence_charts.mkdir(parents=True, exist_ok=True)
    (evidence_charts / "wall.svg").write_text(wall_svg, encoding="utf-8")
    (evidence_charts / "memory.svg").write_text(memory_svg, encoding="utf-8")
    (evidence_charts / "rss.svg").write_text(rss_svg, encoding="utf-8")
    data = {
        "schema": "completion-proof-site-data-v1",
        "generated_at": payload.get("generated_at"),
        "source": "evidence/benchmark/raw.json",
        "environment": payload.get("environment", {}),
        "conditions": summaries,
    }
    (site / "assets" / "data.js").write_text(
        "window.CP_BENCHMARK = " + json.dumps(data, indent=2, sort_keys=True) + ";\n",
        encoding="utf-8",
    )
    summary_path = raw_path.parent / "summary.json"
    summary_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    env = payload.get("environment", {})
    lines = [
        "# Resource report (deterministic engine only)",
        "",
        "Generated from `evidence/benchmark/raw.json` by `scripts/render_site.py`.",
        "These are engine-local operations, not a full Hermes agent run.",
        "",
        "## Method",
        "",
        "- Platform: `{}` ({})".format(env.get("platform", "unknown"), env.get("machine", "unknown")),
        "- Python: `{}`; psutil: `{}`".format(env.get("python", "unknown"), env.get("psutil_version", "unknown")),
        "- RSS: {}".format(env.get("resource_method", "not recorded")),
        "- CPU: {}".format(env.get("cpu_scope_note", "not recorded")),
        "- Scope: {}".format(env.get("measurement_scope", "not recorded")),
        "- Full-agent tokens: **not measured**. `pre_verify` nudges can add turns, so the zero",
        "  engine LLM calls (`llm_calls_by_engine: 0`) is not a claim of zero total tokens.",
        "- Disk: SQLite `db_bytes_delta` per operation; archive size is in `evidence/package/`.",
        "",
        "## Results",
        "",
        "| Condition | n | wall p50/p95 ms | parent CPU p50 ms | child CPU p50 ms | total CPU p50 ms | RSS peak p50/p95 KiB | Python alloc p50 KiB | DB growth sum B |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            "| {label} | {n} | {w50}/{w95} | {pc} | {cc} | {tc} | {r50}/{r95} | {py} | {db} |".format(
                label=row["label"],
                n=row["count"],
                w50=row["wall_p50_ms"],
                w95=row["wall_p95_ms"],
                pc=row.get("parent_cpu_p50_ms", 0),
                cc=row.get("child_cpu_p50_ms", 0),
                tc=row.get("total_cpu_p50_ms", 0),
                r50=row.get("rss_peak_p50_kb", "n/a"),
                r95=row.get("rss_peak_p95_kb", "n/a"),
                py=row.get("python_peak_p50_kb", "n/a"),
                db=row.get("db_bytes_growth_sum", 0),
            )
        )
    lines += [
        "",
        "## Limits",
        "",
        "- `total_cpu_ms` is parent + reaped child CPU, not a whole-system measurement.",
        "- RSS is sampled process-tree resident memory; it is not peak-per-process and not the",
        "  RSS of a full agent session.",
        "- A full-agent token/latency benchmark was not performed. The engine itself makes no LLM",
        "  or network call; the `pre_verify` hook can cause extra agent turns, whose token cost is",
        "  unmeasured here.",
        "",
    ]
    (raw_path.parent / "resource-report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"charts": [
        str(charts / "wall.svg"),
        str(charts / "memory.svg"),
        str(charts / "rss.svg"),
    ], "summary": str(summary_path), "conditions": summaries}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
