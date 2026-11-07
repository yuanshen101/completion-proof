# Changelog

All notable changes to Completion Proof are recorded here. The format follows
Keep a Changelog; versions follow Semantic Versioning for the plugin surface.

## [0.1.5] — 2026-11-07

### Single owner close

- Remove the redundant `root_handle.close()` in `run_check`'s inner
  pre-fingerprint exception. The existing outer `finally` is the single owner;
  a byte-budget/scope error can no longer free the root fd and then close a
  synthetic/real descriptor that reused that number.
- Add a real `engine.verify` byte-budget regression with scheduled FD reuse:
  old 0.1.4 closes twice and would close the victim; 0.1.5 closes once and the
  victim remains valid. Borrowed `compute_fingerprint` handles still survive
  errors; all test FDs are cleaned. POSIX `/proc/self/fd` only.
- Clarify threat-model dedupe terminology: retention bounds storage; dedupe is
  an annotation, never storage elision.
- Version 0.1.5; archive/evidence/export regenerated. No feature changes.

## [0.1.4] — 2026-11-06

### Resource lifetime and disclosure consistency

- Fix owned workspace-root FD leak: `compute_fingerprint` now protects the whole
  owned lifetime with `try/finally`. Exceptions from scope selection, deadlines,
  hash budgets, FIFO/identity checks and hashing close the descriptor; a
  caller-owned `run_check` handle remains open until its existing owner closes it.
  No double-close and no FD acquisition after failure.
- Add Linux `/proc/self/fd` regressions with warmed SQLite and 100 actual gate
  failures (byte budget, deadline, FIFO/hash) plus caller-owned handle validity.
  Old 0.1.3 leaks exactly 100 descriptors on the same fixture; 0.1.4 stays stable
  and recovers to PASS.
- Correct dedupe descriptions in `architecture.md`/`plugin.yaml`: every execution
  is stored, duplicates are annotations, latest result is authoritative.
- Replace the remaining draft disclosure with the complete Rule 13 text.
- Version 0.1.4; archive/evidence/export regenerated at final source.

## [0.1.3] — 2026-11-06

### Security boundary follow-up (issue #14)

- CP06-R: pin the canonical workspace root descriptor and device/inode before
  enumeration; carry it through selection, execution and hashing. POSIX hashing
  walks every ancestor/component with `O_NOFOLLOW`; a replaced root returns
  `workspace_replaced` UNKNOWN instead of hashing an outside tree.
- CP07-A: final file open uses `O_NONBLOCK` and immediate `fstat`; a selected
  regular file replaced by a FIFO is rejected before any read. Budget remains
  best-effort on arbitrary filesystems, documented, not hard realtime.
- CP07-B: budget-bounded reads use a separate read-only SQLite connection with
  `busy_timeout=0`; a lock/busy snapshot is UNKNOWN instead of waiting on the
  observation worker's write lock. Gate returns promptly with the existing
  deadline.
- Correct stale docs: actual 23 s replay video, minimal allowlist (not denylist),
  append-only receipts/latest authoritative, root-FD/FIFO/budget guarantees,
  exact Rule 13 disclosure, public root `SHA256SUMS`, kit as historical private
  archive, operator eligibility decision.
- Version 0.1.3; `requires_hermes >=0.22.7` unchanged; archive/evidence/export
  regenerated at final source.

## [0.1.2] — 2026-11-06

### Security / correctness remediation (judge BLOCK CP01-CP08)

- CP01: configured checks now receive a minimal allowlist environment, not a
  denylist; credentials, proxies, `PYTHONPATH`, profile switches and approval
  bypass variables are never forwarded.
- CP02: every execution appends a receipt; dedupe is an annotation and can no
  longer hide a newer failure behind an older event.
- CP03: POSIX process I/O uses a deadline-bounded selector loop; no synchronous
  close of a stream owned by a blocked reader. Escaped descendants are disclosed
  as unkillable and detection is bounded with UNKNOWN/incomplete output.
- CP04: disabled checks cannot be executed via tool, CLI or slash, even by id.
- CP05: workspace and effective cwd are part of the fingerprint/execution
  identity, preventing cross-workspace shared-ledger fresh PASS.
- CP06: POSIX fingerprinting traverses directory FDs with O_NOFOLLOW and hashes
  by fd/fstat; symlink-swapped parents and credential-shaped paths are refused.
  Platforms without the guarantee return UNKNOWN.
- CP07: hook deadline/hash budget propagate into enumeration and hashing; large
  files are rejected before reading; post_tool_call observations use a short
  bounded queue and never wait on a SQLite lock.
- CP08: declared source/test globs with no match are UNKNOWN unless the operator
  explicitly sets `allow_missing_patterns`; source-only scopes remain legitimate.
- Requires Hermes >=0.22.7 (actual tested runtime); version 0.1.2.

## [0.1.1] — 2026-11-05

### Changed

- Follow-up to issue #10: removed hard-coded temp paths and added an explicit
  environment allowlist plus a self-contained throwaway-Hermes bootstrap.
- Hardened `_run_process` drain semantics for a parent exit with a child holding
  stdout/stderr, with bounded cleanup and partial-output UNKNOWN status; added a
  large-output hash-completeness regression.
- Added actual process-tree RSS, parent CPU, child CPU and total CPU benchmark
  rows plus a generated resource report; full-agent token cost remains
  unmeasured and is not claimed to be zero.
- Added a real local replay MP4 (<90 s) with transcript, captions, narration,
  ffprobe metadata and reproducible capture script.
- Added a separate submission kit with hashes, link check and private-data scan;
  drafted publication/submission steps without executing them.
- Packaged-copy smoke now exercises native `pre_verify`/`post_tool_call` hooks,
  not only the source tree.

## [0.1.0] — 2026-11-05

### Added

- Native Hermes plugin (`plugin.yaml`, `__init__.py`) using only public plugin
  surfaces: `register_tool`, `register_hook`, `register_cli_command`,
  `register_command`, `ctx.get_config`, `ctx.set_config`, `plugin_db`.
- Deterministic proof engine: operator-configured argv checks, `shell=False`,
  bounded timeout with process-group kill, bounded/redacted output capture.
- SHA-256 scope fingerprints over declared source, test and artifact globs;
  PASS receipts become STALE when a relevant file or the check definition
  changes.
- PASS / FAIL / UNKNOWN / STALE ledger with provenance: command vector, cwd,
  file list, exit code, timeout flag, duration, output hashes, scope summary.
- One-shot, bounded `pre_verify` request on coding turns without fresh evidence.
- `post_tool_call` observation ledger, explicitly marked UNTRUSTED_CONTEXT and
  never used as proof evidence.
- Tools `proof_list_checks`, `proof_status`, `proof_verify`, `proof_report`;
  slash command `/proof`; CLI `hermes completion-proof ...`.
- Unit, adversarial and real-Hermes integration tests; reproducible benchmark
  with raw JSON/CSV; generated charts; local static marketing page; standalone
  deterministic archive with SHA-256 manifest; acceptance and submission docs.
- MIT license for newly authored original code.

### Known limitations

- `pre_verify` only fires on coding turns that edited code; its continue nudge
  is bounded (one request by default) and is not a fail-closed lock.
- Hermes may defer plugin tools behind `tool_search`/`tool_call`.
- Windows-native compatibility is UNTESTED; the test environment is Linux under
  WSL2 and must not be reported as native Windows validation.
- No public repository, demo video, catalog PR or submission form was created.
- Mauritius eligibility for the hackathon is unresolved.
