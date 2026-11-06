# Resource report (deterministic engine only)

Generated from `evidence/benchmark/raw.json` by `scripts/render_site.py`.
These are engine-local operations, not a full Hermes agent run.

## Method

- Platform: `Linux-6.18.33.2-microsoft-standard-WSL2-x86_64-with-glibc2.39` (x86_64)
- Python: `3.14.9`; psutil: `7.2.2`
- RSS: process-tree RSS sampled every 10 ms with psutil (parent + recursive children); parent CPU via time.process_time_ns; child CPU via resource.getrusage(RUSAGE_CHILDREN) deltas; RSS is resident memory, not peak-per-process; python_peak_kb is a separate tracemalloc Python-allocation pass
- CPU: Parent and child CPU are reported separately; total_cpu_ms is their sum. RSS sampling includes engine-local child checks, not a full Hermes agent.
- Scope: deterministic engine operations; child command CPU included per row; no Hermes agent turn, no model request
- Full-agent tokens: **not measured**. `pre_verify` nudges can add turns, so the zero
  engine LLM calls (`llm_calls_by_engine: 0`) is not a claim of zero total tokens.
- Disk: SQLite `db_bytes_delta` per operation; archive size is in `evidence/package/`.

## Results

| Condition | n | wall p50/p95 ms | parent CPU p50 ms | child CPU p50 ms | total CPU p50 ms | RSS peak p50/p95 KiB | Python alloc p50 KiB | DB growth sum B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Direct command | 20 | 32.145/32.379 | 6.411 | 22.665 | 29.225 | 36996.0/37261.6 | 53.404 | 0 |
| Observe (no ledger) | 20 | 44.778/59.743 | 30.348 | 22.181 | 53.105 | 38522.0/38641.6 | 1100.598 | 0 |
| Engine verify | 20 | 52.727/54.912 | 31.98 | 21.996 | 54.355 | 38264.0/38684.2 | 1100.676 | 0 |
| Status read | 20 | 14.174/15.524 | 17.435 | 0.0 | 17.435 | 26048.0/26056.0 | None | 0 |
| Gate (stale) | 20 | 14.128/15.815 | 17.369 | 0.0 | 17.369 | 26068.0/26068.2 | None | 0 |

## Limits

- `total_cpu_ms` is parent + reaped child CPU, not a whole-system measurement.
- RSS is sampled process-tree resident memory; it is not peak-per-process and not the
  RSS of a full agent session.
- A full-agent token/latency benchmark was not performed. The engine itself makes no LLM
  or network call; the `pre_verify` hook can cause extra agent turns, whose token cost is
  unmeasured here.
