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
| Direct command | 20 | 32.077/33.774 | 5.268 | 22.012 | 27.364 | 36852.0/37039.4 | 53.404 | 0 |
| Observe (no ledger) | 20 | 48.724/56.706 | 34.349 | 22.297 | 56.623 | 38554.0/38616.0 | 1100.598 | 0 |
| Engine verify | 20 | 51.78/62.824 | 30.072 | 22.334 | 52.492 | 38206.0/38730.0 | 1100.676 | 0 |
| Status read | 20 | 11.909/12.61 | 15.525 | 0.0 | 15.525 | 26052.0/26060.0 | None | 0 |
| Gate (stale) | 20 | 11.915/12.659 | 15.494 | 0.0 | 15.494 | 26068.0/26068.0 | None | 0 |

## Limits

- `total_cpu_ms` is parent + reaped child CPU, not a whole-system measurement.
- RSS is sampled process-tree resident memory; it is not peak-per-process and not the
  RSS of a full agent session.
- A full-agent token/latency benchmark was not performed. The engine itself makes no LLM
  or network call; the `pre_verify` hook can cause extra agent turns, whose token cost is
  unmeasured here.
