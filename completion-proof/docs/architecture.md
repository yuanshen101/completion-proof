# Architecture

## Scope

Completion Proof is a native Hermes plugin. It turns an operator-configured check
run into a local evidence receipt that is bound to the SHA-256 of the files the
operator declared as the check's scope. It is deliberately not a test runner,
not a CI system, and not an attestation service.

## Components

```
completion-proof/
  __init__.py                  # thin native entrypoint: register(ctx)
  plugin.yaml                  # manifest v2, truthful tools/hooks, config schema
  completion_proof/
    config.py                  # trusted-config validation (argv, globs, limits)
    fingerprint.py             # glob expansion, symlink containment, file hashing
    engine.py                  # execution, freshness, statuses, gate, observations
    store.py                   # SQLite ledger, WAL/busy handling, retention
    runtime.py                 # Hermes registration, tools, CLI, slash, hooks
    schemas.py                 # model-facing tool schemas (no argv/cwd/env)
    redact.py                  # bounded output + best-effort redaction
    models.py                  # immutable data shapes, status semantics
    version.py                 # version/schema constants
  tests/                       # unit, adversarial, real-Hermes integration
  scripts/                     # package, benchmark, charts, evidence, demo
  site/                        # local static marketing page + generated charts
  docs/                        # architecture, threat model, acceptance, submission
  evidence/                    # committed real command output and raw data
```

## Data flow

### Verification (`proof_verify`, CLI, slash)

1. `settings_from_mapping` validates the operator's settings once at plugin load.
   The model-facing tool accepts only `check_ids`; it cannot pass argv, cwd, env,
   paths or globs.
2. For each selected check, `ProofEngine.workspace_for`/`cwd_for` resolve the
   workspace and working directory and reject escapes.
3. `compute_fingerprint` expands the declared source/test globs, rejects symlinks
   that resolve outside the workspace, hashes each file with a before/after stat
   check, and builds a canonical fingerprint that includes the check digest.
4. `filtered_environment` + `subprocess.Popen(shell=False)` run the configured
   argv with a bounded timeout and a new process group. Output is streamed into
   SHA-256 digests while only the first 64 KiB per stream is retained.
5. A source/test file changing during the run, a timeout, a spawn error, an empty
   scope or a missing declared artifact yields `UNKNOWN`.
6. The post-run fingerprint (including artifacts) is stored with the receipt.
   Every execution appends a receipt; identical reruns inside the dedupe window
   are annotated with `duplicate_of`/dedupe metadata. They are never reused, so
   the most recent execution and status remain authoritative.
7. `verifications` records one row per invocation; `receipts` records immutable
   per-check evidence; `observations` records untrusted tool activity.

### Freshness (`proof_status`, gate, report)

The current fingerprint is recomputed and compared to the latest receipt's
stored fingerprint and check digest. Equal → the receipt's own status (`PASS`,
`FAIL`, `UNKNOWN`). Different → `STALE`. Old receipts are never mutated.

### `pre_verify`

Hermes calls `pre_verify` only when a coding turn edited files and at least one
hook is registered. The plugin:

- filters configured checks by matching changed paths against source/test globs;
- computes current freshness with a soft `gate_deadline_ms` budget;
- returns `None` when all relevant checks are fresh PASS, on non-coding turns,
  after the configured attempt bound, or when no check is relevant;
- otherwise returns one `{"action":"continue","message":...}` naming the checks
  and both invocation paths (`proof_verify` / `tool_call` / CLI).

Hermes independently caps continue directives. The plugin never edits the final
answer and never claims to gate every answer.

## Storage

`plugin_db("completion-proof")` opens
`$HERMES_HOME/plugin-data/completion-proof/data.db` through the public Hermes API.
Tables:

| Table | Purpose |
|---|---|
| `meta` | Schema version. |
| `verifications` | One row per invocation: source, timing, result payload, summary. |
| `receipts` | Immutable per-check evidence rows; indexed by check/time and `run_key`. |
| `observations` | Untrusted `post_tool_call` activity metadata; never evidence. |

The store sets `busy_timeout`, enables WAL where possible, retries lock errors,
and raises `StoreCorruptError` rather than silently recreating a damaged ledger.
Retention is applied only by an explicit `gc` command or the configured cap.

## Trust boundaries

| Boundary | Trusted side | Untrusted side |
|---|---|---|
| Check definition | `config.yaml` written by the operator | model tool arguments (`check_ids` only) |
| Command execution | argv from configuration, `shell=False` | command string returned by the model in a tool call (not used) |
| Evidence | plugin-run process + file hashes | model text, command stdout claims, `post_tool_call` result content |
| Executed code | whatever the configured check runs; the plugin does not sandbox it | n/a |

The plugin runs the operator's command with the operator's permissions. This is
disclosed and is why only the operator can configure checks.

## Public Hermes surfaces used

- `ctx.register_tool`, `ctx.register_hook`, `ctx.register_cli_command`,
  `ctx.register_command`
- `ctx.get_config`, `ctx.set_config`
- `plugins.plugin_storage.plugin_db`
- Hooks: `post_tool_call`, `pre_verify`

No core function, module attribute, private dict, or Desktop markup is replaced,
wrapped or rebound. No auto-updater, no network, no credential reads.

## Versioning and compatibility

- Plugin version `0.1.5`, manifest schema v2, API generation 1.
- `requires_hermes: ">=0.22.7"`.
- Current evidence validated with Hermes `0.23.0` release line `2026.9.24`;
  earlier 0.1.2–0.1.4 runs used `0.22.7`. The floor remains `>=0.22.7`, not a
  claim about every upstream build.
- SQLite schema version 2; older ledgers are opened and their receipts become
  STALE under the workspace-aware fingerprint. Newer schema versions are refused
  rather than downgraded.
- Configured checks receive a minimal environment allowlist; POSIX fingerprinting
  uses directory-FD/O_NOFOLLOW traversal; hook deadlines/hash budgets propagate
  into enumeration/hash; observations use a bounded queue.
