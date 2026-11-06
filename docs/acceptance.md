# Acceptance checklist — Completion Proof 0.1.4

**Verdict: finished technical kit for this offline run; not a submitted entry.**
This document maps the original assignment and issue #10 follow-up to committed
evidence. Items marked BLOCKED, UNTESTED or EXTERNAL are not claimed as done.
Publication/submission are separate human-authorized steps.

Evidence paths are relative to the repository root. The plugin root is
`completion-proof/`; evidence and the acceptance/submission docs are at repository
root so the plugin directory remains independently packageable and scannable.

---

## 1. Contract verification, differentiation, architecture, spike

| # | Requirement | Status | Evidence / note |
|---|---|---|---|
| 1.1 | Verify the current plugin/hooks contract from current docs/source, not guesses | PASS | `plugin.yaml`, `completion-proof/__init__.py`, `completion-proof/completion_proof/runtime.py`; `evidence/install/doctor.txt` (real registration), `evidence/install/validate.json`; built against the current local Hermes 0.22.7 / requires >=0.22.7 |
| 1.2 | Competitor differentiation (especially afterforge/crew) | PASS | `completion-proof/docs/differentiation.md`; based on the merged research docs and public catalog descriptions, not a code audit |
| 1.3 | Architecture and threat model | PASS | `completion-proof/docs/architecture.md`, `completion-proof/docs/threat-model.md` |
| 1.4 | Explicit product acceptance checklist | PASS | this file |
| 1.5 | Spike real `pre_verify` / `post_tool_call` and storage in an isolated runtime, not fake hook tests | PASS | `evidence/hooks/mock_requests.jsonl` (real nudge appears in a real one-shot agent turn), `evidence/hooks/receipts.json` (real `unit PASS` receipt, `tool` verification), `evidence/hooks/agent-turn.txt`; the integration test drives actual Hermes with a local mock OpenAI-compatible provider |
| 1.6 | Honest fallback if a surface is impossible; no simulated hook test | PASS | The agent-turn test is real; the only substitution is the local mock model (no paid provider/network), disclosed in `evidence/hooks/agent-turn.txt` and the test source |

---

## 2. Engine, storage, checks, hooks, install

| # | Requirement | Status | Evidence / note |
|---|---|---|---|
| 2.1 | Proof engine and storage | PASS | `completion_proof/engine.py`, `store.py`; SQLite ledger; `evidence/install/cli-verify.json` |
| 2.2 | Explicit trusted-check configuration, separate from model tool inputs | PASS | `config.py`; tool schemas accept only `check_ids`; `tests/test_runtime.py::test_tool_rejects_unknown_check_and_ignores_command_inputs` |
| 2.3 | Freshness against source + tests + artifacts | PASS | `engine.status`, `fingerprint.py`; `tests/test_engine.py::test_stale_after_source_edit`, `test_stale_after_test_edit`, `test_stale_after_check_config_change`, `test_artifact_produced_by_check_is_hashed`; `evidence/install/cli-status-stale.json` |
| 2.4 | Ledger PASS/FAIL/UNKNOWN with provenance, execution time, exit/timeout, fingerprints | PASS | receipt fields in `models.py`; `evidence/install/cli-verify.json`; `tests/test_engine.py` |
| 2.5 | A successful old run becomes STALE after relevant source/test change | PASS | `test_stale_after_source_edit`, `test_stale_after_test_edit`; real CLI stale evidence |
| 2.6 | Never accept model-supplied stdout/test counts/exit codes as independent evidence | PASS | `post_tool_call` rows are `UNTRUSTED_CONTEXT` only; `tests/test_runtime.py::test_observation_cannot_create_evidence`; receipt creation only in `run_check` |
| 2.7 | Evidence source and scope explicit; PASS scope documented | PASS | `scope_summary`, `proves`, `WHAT_PASS_PROVES` / `WHAT_PASS_DOES_NOT_PROVE` in reports and README |
| 2.8 | No cryptographic tamper-proof claim | PASS | README, report text, `docs/threat-model.md`, `site/index.html` all state local/not tamper-proof |
| 2.9 | CLI/tools/slash as supported | PASS | `hermes completion-proof ...`; tools registered and validated; `/proof`; `evidence/install/doctor.txt`, `cli-verify.json` |
| 2.10 | Install/uninstall/config examples | PASS | README install/uninstall/config; `scripts/package.py`; `evidence/package/` |
| 2.11 | Zero network and zero LLM in the deterministic engine | PASS | `engine.py` has no network/LLM code; benchmark socket guard records `network_attempts_observed: 0` and `llm_calls_by_engine: 0` in `evidence/benchmark/raw.json` |
| 2.12 | Profile-safe plugin storage | PASS | `plugins.plugin_storage.plugin_db`; `$HERMES_HOME/plugin-data/completion-proof/`; integration DB at that path |

---

## 3. Unit, integration, adversarial, real install and packaging

| # | Requirement | Status | Evidence / note |
|---|---|---|---|
| 3.1 | Unit + integration + adversarial tests | PASS | `evidence/tests/unit.txt` (engine + runtime modules) and `evidence/install/integration.txt` (real Hermes) |
| 3.2 | Real isolated Hermes load/doctor/validation/security scan using supported commands | PASS | `evidence/install/doctor.txt` (`4 tool(s), 2 hook(s)`), `evidence/install/validate.json` (`security scan: safe`), `evidence/install/integration.txt` |
| 3.3 | Test the actual packaged installed copy, not only imports | PASS | `scripts/verify_archive.py` extracts the built archive into a fresh `HERMES_HOME`; `evidence/package/fresh-install.json` / `.txt` |
| 3.4 | Minimal fixture project end-to-end with actual native hooks and real receipts | PASS | `tests/test_hermes_integration.py::test_real_agent_turn_fires_pre_verify_and_records_receipt`; `evidence/hooks/*` |
| 3.5 | Windows-native compatibility if available, else explicit UNTESTED | UNTESTED | Host is Linux under WSL2; `evidence/environment.json` records `wsl: true`, `windows_native: false`; README/limits say Windows-native UNTESTED. No Windows claim. |

---

## 4. Controlled failure injections

| Injection | Status | Test / evidence |
|---|---|---|
| Exit 1 while printing “passed” | PASS | `test_false_pass_from_text_is_fail` |
| Timeout and child cleanup | PASS | `test_timeout_is_unknown_and_child_killed` (process group killed; child marker absent) |
| Missing artifact | PASS | `test_missing_artifact_is_unknown` |
| Stale previous green after source edit | PASS | `test_stale_after_source_edit`; real CLI stale evidence |
| Stale previous green after test edit | PASS | `test_stale_after_test_edit` |
| Corrupt/malformed DB | PASS | `test_store_corrupt_database_is_reported` |
| Corrupt/malformed config | PASS | `test_config_rejects_unsafe_shapes`, `test_config_error_degrades_without_crashing` |
| Altered/false output | PASS | `test_output_redaction_and_cap`; `test_false_pass_from_text_is_fail`; observations cannot become evidence |
| Duplicate calls | PASS | `test_duplicate_receipt_is_reused` |
| Path/symlink escape | PASS | `test_symlink_escape_is_unknown`, `test_cannot_escape_with_dotdot_glob` |
| Non-code turn | PASS | `test_pre_verify_bounded_and_relevant` |
| Bounded nudge attempts | PASS | `test_pre_verify_bounded_and_relevant`; real agent turn shows one nudge |
| Genuine green | PASS | `test_pass_records_scope_and_receipt`, `test_pre_verify_silent_after_fresh_pass`; real CLI/integration |
| Workspace/profile isolation | PASS | `test_workspace_isolation`; integration uses fresh `HERMES_HOME` |
| File changes during hash/check | PASS | `test_file_changed_during_hash_is_unknown`, `test_source_change_during_check_is_unknown` |
| SQLite locking/concurrency | PASS | `test_concurrent_store_writes` (4 threads) |
| Approval semantics | PASS by design | Plugin never approves/bypasses; only runs configured checks. No test can prove host behavior for all surfaces; disclosed. |

No claim is made that unit mocks replace the real integration; both exist and the real
Hermes turn is exercised.

---

## 5. Benchmark

| # | Requirement | Status | Evidence / note |
|---|---|---|---|
| 5.1 | Reproducible benchmark tool with raw JSON/CSV | PASS | `scripts/benchmark.py`; `evidence/benchmark/raw.json`, `raw.csv` |
| 5.2 | Baseline engine off / observe vs enabled | PASS | Conditions: `direct_command_baseline` (engine off), `engine_verify_observe_no_store` (engine with no ledger writes), `engine_verify_enabled` (full), plus `engine_status_fresh`, `engine_pre_verify_stale_gate` |
| 5.3 | Repeatable seeds/fixtures | PASS | `--seed 20261105 --files 40 --iterations 20`; same generator; environment in raw JSON |
| 5.4 | CPU time / wall p50/p95 / process RSS / disk growth | PASS | raw rows have `wall_ms`, `parent_cpu_ms`, `child_cpu_ms`, `total_cpu_ms`, `rss_peak_kb` (psutil process-tree sampling), `python_peak_kb` (separate tracemalloc), `db_bytes_delta`; `summary.json` derives p50/p95. See `evidence/benchmark/resource-report.md`. |
| 5.5 | Network and added LLM turns/tokens if actually observed | PASS | `network_attempts_observed: 0` via socket guard; `llm_calls_by_engine: 0`; full-agent token cost **not measured** and `full_agent_nudges_can_add_turns: true`; no zero-total-tokens claim. |
| 5.6 | Include environment/version/cmd/commit | PASS | `evidence/environment.json`, `raw.json.environment` |
| 5.7 | Separate deterministic engine from full agent runs and fixture success from real-user outcomes | PASS | `measurement_scope` / `agent_run_measurements: not_measured` in raw JSON; README and site state it |
| 5.8 | Charts derive only from committed raw data; no hardcoded marketing numbers | PASS | `scripts/render_site.py` reads raw JSON; `site/assets/charts/*.svg`, `evidence/benchmark/charts/*.svg`, `site/assets/data.js`; no manual figures |

**Caveat:** full-agent measurements (turns, tokens, latency) were not made. The raw data
is deterministic-engine evidence, not an agent benchmark. The site says so.

---

## 6. Product UX, docs, site

| # | Requirement | Status | Evidence / note |
|---|---|---|---|
| 6.1 | README English + French summary | PASS | `completion-proof/README.md` (sections and “En bref”) |
| 6.2 | Install/config/use/troubleshooting/uninstall | PASS | README sections; quickstart genuinely runs (`evidence/demo/session.txt`, integration) |
| 6.3 | Quickstart truly runs | PASS | `scripts/demo.sh` output; `evidence/install/cli-verify.json`; integration |
| 6.4 | Changelog/version manifest/license | PASS | `CHANGELOG.md`, `VERSION`, `plugin.yaml`, `LICENSE` (MIT) |
| 6.5 | Limitations/security/privacy | PASS | README, `docs/limitations.md`, `docs/threat-model.md` |
| 6.6 | Contribution guide | PASS | `CONTRIBUTING.md` |
| 6.7 | Polished local static marketing page with real-data charts, evidence links, resource impact, limitations | PASS | `site/index.html`, `style.css`, `app.js`; wall/RSS/Python-allocation charts; resource report; demo MP4 link. The existing page captures are **not** presented as product-improvement evidence. |
| 6.8 | No external trackers or mandatory paid services | PASS | No external scripts/fonts/trackers; all local; charts generated from committed data |

---

## 7. Submission-ready package (offline scope)

| # | Requirement | Status | Evidence / note |
|---|---|---|---|
| 7.1 | Standalone plugin archive + SHA-256 manifest produced by reproducible script | PASS | `scripts/package.py`; `evidence/package/completion-proof-0.1.4.tar.gz`, `SHA256SUMS`; deterministic tar/gzip |
| 7.2 | Fresh install smoke from archive | PASS | `evidence/package/fresh-install.json` / `.txt` |
| 7.3 | Candidate catalog YAML template meeting current schema; URL/SHA placeholders visibly unresolved | PASS | `completion-proof/docs/catalog-entry.template.yaml` with `<PUBLIC_REPOSITORY_URL_UNRESOLVED>` and `<FULL_40_CHARACTER_COMMIT_SHA_UNRESOLVED>` |
| 7.4 | Submission checklist and text | PASS | `docs/submission-checklist.md` |
| 7.5 | Real local demo video under 90 s, actual exercised FAIL -> fix -> PASS -> STALE | PASS | `evidence/demo/completion-proof-demo.mp4` (H.264 MP4, 23 s, 1280×720), `demo_capture.json`, `transcript.txt`, `video_captions.srt`, `narration.md`, `ffprobe.json`; labelled replay, not live, with actual execution time separately recorded |
| 7.6 | Recording script, narration/captions English + French summary, reproducible render command | PASS | `scripts/record_demo.py`, `evidence/demo/render-command.txt`, `narration.md`, `video_captions.srt` |
| 7.7 | No public submission/merge in this run; explain authorization boundary, not permanent “do not submit” | PASS | `docs/publication-and-submission.md`; README/site say technical kit complete, submitted entry no |
| 7.8 | External prerequisites not treated as reasons to stop offline work | PASS | `docs/publication-and-submission.md`; public repo, full SHA, catalog PR, X, form and the operator eligibility decision are human steps |
| 7.9 | Separate complete submission kit with hashes and working site links | PASS | `submission-kit/` (source, site, evidence, docs, demo video, README, SHA256SUMS, manifest.json, link-check.json, scan.json) |

---

## Issue #10 follow-up gates

| Gate | Status | Evidence / note |
|---|---|---|
| 1. Remove hard-coded temp paths; honor TMPDIR; deliberate env allowlist; no real-profile writes; self-contained bootstrap | PASS | `completion_proof/sandbox_env.py`, `scripts/clean_exec.py`, `scripts/prepare_hermes_template.sh`; integration `_env` uses the allowlist; template built under `.scratch/`; no `/tmp` literals in scripts/tests |
| 2. Pipe draining: parent exits + child holds stdout/stderr; timeout; large-output completeness; no hang/leak | PASS | `engine.py` drain semantics; `tests/test_engine.py::test_parent_exit_with_child_holding_pipe_is_bounded_and_unknown`, `test_large_output_hash_covers_the_whole_stream`, timeout/child-cleanup test; evidence in `evidence/tests/unit.txt` |
| 3. Real local demo video <90 s, actual FAIL→fix→PASS→STALE, captions/narration English + French, ffprobe, no live claim | PASS | `evidence/demo/completion-proof-demo.mp4` (23 s H.264), `demo_capture.json`, `transcript.txt`, `video_captions.srt`, `narration.md`, `ffprobe.json`, `scripts/record_demo.py` |
| 4. Fresh-install current archive; doctor/validate/CLI/hooks against package; artifacts rebuilt to final source | PASS | `scripts/verify_archive.py --hook-test`; `evidence/package/fresh-install.json` includes packaged-copy native `pre_verify`; archive/SHA and evidence manifest rebuilt after code changes |
| 5. Resource report: actual process RSS/CPU/disk, method/platform/units/baseline, child CPU disclosure, full-agent token caveat, real-data charts | PASS | `scripts/benchmark.py`, `evidence/benchmark/raw.json/csv`, `summary.json`, `resource-report.md`, wall/RSS/alloc charts; `llm_calls_by_engine: 0`, `full_agent_tokens: not_measured`, `full_agent_nudges_can_add_turns: true` |
| 6. Submission kit with site/evidence/docs/video/README and hashes; working site links; private-data scan | PASS | `submission-kit/` is the historical private offline bundle; the clean public export carries root `SHA256SUMS`, `link-check.json`, `scan.json` and excludes the duplicated kit |
| 7. Draft exact publication/submission steps; do not execute; explain run authorization boundary; distinguish technical kit from entry | PASS | `docs/publication-and-submission.md`; README/site/checklist wording updated; no publication action taken |

## Judge remediation CP01-CP08 (0.1.2)

Independent judges blocked 0.1.1 despite green tests. The judge-derived suite is
`tests/test_judge_regressions.py`; `scripts/run_judge_regressions.py` exports the old
commit (`027c954`) into scratch, copies the same tests, and records old-fail/new-pass
evidence under `evidence/judge-regressions/`. No old tree is mutated and no secrets
are used.

| Finding | Status | Fix / regression |
|---|---|---|
| CP01 environment denylist leaked credentials, `PYTHONPATH`, profile/approval switches | PASS | Production child env is a minimal allowlist (`sandbox_env`); function + actual child tests with synthetic names; no unknown forwarding. |
| CP02 dedupe hid a newer failure (FAIL-PASS-FAIL showed fresh PASS) | PASS | Every execution appends a receipt; dedupe is annotation only. Tests cover FAIL-PASS-FAIL, PASS-FAIL-PASS and latest-status ordering. |
| CP03 timeout pipe deadlock with escaped child | PASS | POSIX selector/nonblocking drain with deadline; incomplete output -> UNKNOWN; no close of a stream owned by a blocked thread. Escaped-child regression cleans the PID and keeps engine return bounded. |
| CP04 `enabled:false` executed by explicit id | PASS | `select_checks` rejects disabled ids via tool/CLI/slash; markers prove no execution. |
| CP05 shared ledger showed fresh PASS across workspaces | PASS | Canonical workspace/effective cwd are in the fingerprint and status validation; cross-workspace shared-DB regression. |
| CP06 symlink TOCTOU / credential path reads | PASS | POSIX directory-FD + `O_NOFOLLOW` traversal, hash by fd/fstat; symlink-swapped parent refused; credential-shaped paths refused; unsupported platform -> UNKNOWN. |
| CP07 hook budgets ignored / SQLite hook stall | PASS | Deadline/hash budget propagate into enumeration and hashing; sparse 512 MiB rejected before read; post_tool_call uses a bounded queue and returns without waiting on a lock. |
| CP08 missing required test/source scope could PASS | PASS | Declared source/test globs with no match -> UNKNOWN unless `allow_missing_patterns` is explicitly enabled; source-only scopes remain valid; zero-collected-tests caveat documented. |

Additional: JSON/quoted secret redaction tests; artifact-only gate relevance documented;
Rule 13 disclosure; version 0.1.2 and `requires_hermes >=0.22.7` (the exercised runtime).
Old-fail/new-pass evidence: `evidence/judge-regressions/old.txt` (12 failures + 5 errors
on `027c954`) and `new.txt` (22/22 OK); `summary.json` records the source refs. The
0.1.2→0.1.3 variants are separately recorded below.

### Public export

`scripts/build_public_export.py` prepares a sanitized export under
`.scratch/public-export/` with the nested plugin layout plus root `evidence/` and
`docs/`, a product root README, MIT license, `SHA256SUMS`, `link-check.json`,
`scan.json` and `PUBLIC-EXPORT.json` (actual source commit/dirty flag, tested
content hash). It excludes the submission-kit, `.git`, `.scratch`, homes/databases,
research scoring docs, `hello-migration.sh` and the smoke-comment workflow. The
export is not committed; the parent copies/publishes it after review. Site links
and archive installation are rechecked after export.

## Security boundary follow-up CP06-R / CP07-A / CP07-B (0.1.3)

Old 0.1.2: `a79577d`; new: this branch. The same tests are exported against 0.1.2
by `scripts/run_judge_regressions.py --old-ref a79577d --tests ...`; raw results
are under `evidence/judge-regressions-013/`.

| Finding | Status | Fix / regression |
|---|---|---|
| CP06-R replaced workspace root after selection hashed outside and PASSed | PASS | The verification opens and pins the canonical root FD + device/inode before enumeration, carries it through selection/execution/post-hash, walks every ancestor/component with `O_NOFOLLOW`, and hashes via the pinned FD. A replaced root is `workspace_replaced` UNKNOWN. True `engine.verify` root-race and direct-hash regressions fail on 0.1.2 and pass here; stable-root parent swap still refused. |
| CP07-A selected file replaced by FIFO blocked before fstat | PASS | Final open adds `O_NONBLOCK`, immediately `fstat`s and rejects non-regular files before any read. Direct-hash and actual `pre_verify` injected-FIFO tests return promptly; old 0.1.2 run is killed by the external supervisor at 8 s. |
| CP07-B observation worker + real writer made 25 ms gate wait ~452 ms | PASS | Budget-bounded reads use a separate read-only SQLite connection with `busy_timeout=0`; a busy snapshot becomes UNKNOWN rather than waiting on the observation write lock. Real cross-thread writer + enqueued production observation + 25 ms pre_verify test passes; 0.1.2 failed at ~0.56 s. |

The deadline remains bounded best-effort, not hard realtime on arbitrary filesystems.
The 65-test unit/runtime/judge suite, real-Hermes integration, archive/native hook
smoke and clean export checks were rerun at the final 0.1.3 source. Variant evidence:
6/6 new pass; old 0.1.2 fails CP06-R, fails CP07-B, and the CP07-A pair is killed
by the external supervisor at 8 s (`evidence/judge-regressions-013/{summary.json,cp07a/summary.json}`).

## Resource lifetime and disclosure follow-up (0.1.4)

| Finding | Status | Fix / regression |
|---|---|---|
| Owned root FD leaked when `compute_fingerprint` raised after opening a handle | PASS | The entire owned lifetime is now `try/finally`; close only when `owns_handle` and never for a caller-owned `run_check` handle. Regression uses warmed SQLite and 100 actual gate failures (byte budget, deadline, FIFO/hash) with `/proc/self/fd`: old 0.1.3 leaks 100 descriptors, 0.1.4 stays stable and restores a normal 2-byte source to PASS. A caller-owned handle remains usable until its owner closes it. |
| Dedupe docs said receipts are reused | PASS | `architecture.md` and `plugin.yaml` now state every execution is stored; duplicates are annotations and the latest result is authoritative. |
| Submission checklist disclosure was incomplete | PASS | Replaced with the complete Rule 13 text: automatic configured checks without per-run prompt, OS privilege/no sandbox, commands may network/read/spawn/consume, SQLite metadata/scope hashes/excerpts/retention/GC, explicit `init --write`, 1-600 s timeout/no escaped-descendant guarantee, no engine network/LLM/daemon/telemetry, bounded nudge, honest credential scope. |

Evidence: `evidence/resource-lifetime/` (old 0.1.3 two tests fail with 100 leaked FDs; new two tests pass; full new suite includes caller-owned and FIFO cases). No host FD limit change; tests skip outside POSIX `/proc/self/fd`.

## Required PR content paths

| Required path | Status |
|---|---|
| `completion-proof/plugin.yaml` | present |
| `completion-proof/__init__.py` | present |
| `completion-proof/README.md` | present |
| `completion-proof/LICENSE` | present |
| `completion-proof/tests/` | present |
| `completion-proof/scripts/` | present |
| `completion-proof/site/` | present |
| `completion-proof/docs/` | present |
| `evidence/` with commands/stdout/structured results/environment | present at repository root |
| `docs/acceptance.md` | this file |
| `docs/submission-checklist.md` | present |
| `docs/publication-and-submission.md` | present |
| `submission-kit/` | historical private offline bundle; the clean public export excludes it and carries root `SHA256SUMS` |
| `evidence/demo/completion-proof-demo.mp4` | real 23 s replay MP4 |

---

## Quality gates

| Gate | Status | Note |
|---|---|---|
| No stubs/TODO masquerading as done | PASS | No `TODO`/stub implementation; unsupported items are marked BLOCKED/UNTESTED |
| Real install + native hook integration + passing tests | PASS | doctor/validate/archive fresh install/integration/agent turn; packaged copy also exercised with native hooks |
| Hard-coded temp paths removed; explicit env allowlist | PASS | `scripts/prepare_hermes_template.sh`, `completion_proof/sandbox_env.py`, `scripts/clean_exec.py`; no test writes to a real profile |
| Pipe-drain regression | PASS | bounded parent-exits-child-holds-pipe test, large-output hash completeness test |
| Safe trusted config | PASS | operator-only argv; tool surface limited; path containment |
| Freshness invalidation | PASS | source/test/artifact/check-digest staleness tests |
| Baseline-backed measurements; RSS/CPU/disk | PASS | direct/observe/enabled raw rows; psutil RSS + parent/child CPU; resource report |
| Scanner outcome honestly included | PASS | `security scan: safe` at final validation; evidence outside plugin root; committed in `evidence/install/validate.json` |
| No secrets/private paths | PASS | filtered env; redaction; kit `scan.json`; no operator secrets read |
| Reproducible package/docs/kit | PASS | deterministic archive, manifest, submission kit hashes |
| Windows-native | UNTESTED | not claimed |

## Parent checks vs new worker checks

The parent independently verified the original PR #9 commit `cdeca76`: 38 unit/runtime
tests, 3 real-Hermes integration tests, doctor/security validation, CLI PASS → STALE,
and native `pre_verify` with a disclosed local mock provider. This follow-up re-ran
those suites on its own branch (the expanded unit/runtime/judge suite, see `evidence/tests/unit.txt`)
and added: explicit env allowlist/temp bootstrap,
pipe-drain and large-output regressions, real MP4 capture, RSS/CPU resource rows,
packaged-copy native hook smoke, and the submission-kit link/leak checks. Parent
observations were not reused as fresh results.

## Final honesty statement

The technical kit is implemented, tested, recorded, measured, packaged and evidenced.
It is **not a submitted entry**: the public repository, full SHA pin, catalog PR, X
post, Typeform submission and the operator eligibility decision are externally
human-authorized steps and were not executed. No merge, publish, public repo, upstream
PR, form submission, paid service or production install was performed.
