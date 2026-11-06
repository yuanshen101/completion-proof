# Completion Proof

**Evidence receipts for coding completion — tied to the actual source revision, not to a
model's memory of a command.**

Completion Proof is a native [Hermes](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins)
plugin. You declare checks you trust (an argv vector, a timeout, and source/test/artifact
globs). The plugin runs them locally, fingerprints the declared files with SHA-256, and
records **PASS / FAIL / UNKNOWN** receipts. A successful old receipt becomes **STALE** as
soon as any relevant file or the check definition changes.

This repository contains the **finished offline technical kit** for the Nous Research
Plugin Hackathon. It is **not a submitted entry**: publication, the public repository,
catalog PR, demo-post and form are separate human-authorized external steps and were not
performed in this run. See [Submission status](#submission-status) and
[`docs/acceptance.md`](../docs/acceptance.md).

---

## What a PASS actually proves

> The configured command exited `0` before its timeout, and every file in the check's
> declared source, test and artifact scope had the recorded SHA-256 when the check was run.

That is all. A PASS does **not** prove that all software is correct, that every relevant
test was run, or that untested code works. Evidence is local and **not cryptographically
tamper-proof**: a process with the same OS permissions can edit the SQLite ledger. The
plugin says this in every report and in the `pre_verify` nudge.

A check exiting `0` can still be incomplete. A framework can collect zero tests while the
command exits 0; Completion Proof does not parse output as proof. Declared source/test
globs that match no files are UNKNOWN unless the operator explicitly enables
`allow_missing_patterns`. The plugin records *what was configured and observed*; it does
not infer what the check forgot to test. Artifact-only changes are included in the gate's
relevance match when `artifact_globs` is non-empty, but an artifact hash proves only its
content/existence after the check, not that the command generated it.

---

## Why this is a native plugin, not a prompt or a script wrapper

- A prompt can ask “run the tests”; it cannot hash the source revision, persist an immutable
  receipt, invalidate an old pass, or return a `pre_verify` continue directive.
- A standalone script can run a command; it cannot hook the Hermes turn boundary, observe the
  coding posture and changed paths, or use the profile-scoped plugin ledger.

The plugin uses only public Hermes surfaces: `ctx.register_tool`, `ctx.register_hook`,
`ctx.register_cli_command`, `ctx.register_command`, `ctx.get_config`, `ctx.set_config`, and
`plugins.plugin_storage.plugin_db`. It does not monkeypatch core, read credentials, or
auto-update itself.

---

## Install

Requirements: Hermes with native plugin support (`requires_hermes >= 0.22.7`). The build was
validated with Hermes `0.22.7` (release line `2026.9.24`); see
[`evidence/environment.json`](../evidence/environment.json).

### From the release archive

```bash
tar -xzf completion-proof-0.1.4.tar.gz -C "${HERMES_HOME:-$HOME/.hermes}/plugins"
hermes plugins enable completion-proof
hermes plugins doctor completion-proof --ci
```

### From a directory

```bash
cp -r completion-proof "${HERMES_HOME:-$HOME/.hermes}/plugins/"
hermes plugins enable completion-proof
```

The archive and its SHA-256 manifest are produced by `python3 scripts/package.py`; the
manifest is committed under `evidence/package/`.

---

## Quickstart (runs for real)

```bash
cd /path/to/your/project

# 1. Print a starter configuration (detects pytest/unittest/npm heuristically).
hermes completion-proof init

# 2. Review the command and globs, then add them to config.yaml or write the
#    fixed template with:
hermes completion-proof init --write

# 3. Run the configured check and store a receipt.
hermes completion-proof verify

# 4. Inspect the latest receipt and its freshness.
hermes completion-proof status
# or: hermes completion-proof report --json

# 5. Edit a covered source or test file, then:
hermes completion-proof status
# the previous PASS is now STALE, not PASS
```

`init --write` only writes if no checks exist yet. It uses a fixed template; it never accepts
a command from an argument. Review it before trusting it.

### In a Hermes session

- Tools: `proof_list_checks`, `proof_status`, `proof_verify`, `proof_report`.
- Slash command: `/proof [status|checks|verify [id...]|report]`.
- CLI: `hermes completion-proof status|verify|checks|report|gc|init`.

Hermes may defer plugin tools behind `tool_search`. If `proof_verify` is not in the immediate
tool list, call it through `tool_call`:

```json
{"calls": [{"name": "proof_verify", "arguments": {"check_ids": ["tests"]}}]}
```

The slash command and CLI are always available. The `pre_verify` nudge tells the agent both
paths.

---

## Configuration

Settings live under `plugins.entries.completion-proof.settings` in Hermes `config.yaml`. This
is the **trusted operator boundary**: the model cannot add or change checks, argv, cwd, env or
globs through a tool.

```yaml
plugins:
  enabled:
    - completion-proof
  entries:
    completion-proof:
      enabled: true
      settings:
        workspace: /absolute/path/to/project   # or relative to the Hermes process cwd
        store_output: true
        max_output_bytes: 4096
        nudge_enabled: true
        nudge_max_attempts: 1
        gate_deadline_ms: 250
        retention_days: 30
        max_receipts_per_check: 200
        checks:
          - id: tests
            description: Project test suite
            argv: ["python3", "-m", "pytest", "-q"]
            cwd: "."
            timeout_seconds: 120
            source_globs: ["src/**/*.py", "app/**/*.py"]
            test_globs: ["tests/**/*.py"]
            artifact_globs: []          # optional, hashed after the check runs
            exclude_globs: [".venv/**", "node_modules/**", "dist/**"]
            proves: "The configured test command exits 0 on the declared scope."
```

### Check fields

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Unique `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`; the only thing a model tool may name. |
| `argv` | yes | Non-empty list of strings. `shell=False`; a single string is rejected. |
| `timeout_seconds` | no | 1–600, default 120. On timeout the child process group is killed and the result is UNKNOWN. |
| `cwd` | no | Workspace-relative directory, default `.`. Absolute paths and `..` are rejected. |
| `source_globs` / `test_globs` | one of | Workspace-relative globs hashed before and after the check. |
| `artifact_globs` | one of | Workspace-relative globs hashed after the check; a missing declared artifact yields UNKNOWN. |
| `exclude_globs` | no | Excludes source/test matches only (never artifacts). Defaults exclude `.git`, virtualenvs, caches, `dist`, `build`. |
| `workspace` | no | Per-check workspace override; otherwise the settings `workspace` or the Hermes process cwd. |
| `proves` | no | Your own sentence describing what this check proves. |
| `allow_missing_patterns` | no | Default false. Declared source/test globs that match no files make the result UNKNOWN; set true only when an operator intentionally marks an absent scope optional. |

At least one of `source_globs`, `test_globs`, `artifact_globs` is required. If the declared
source/test scope matches no files, the result is UNKNOWN rather than PASS.

Scopes must stay inside the workspace. Symlinks that resolve outside are rejected with an
UNKNOWN `path_escape` receipt; they are never followed for hashing.

---

## Statuses and freshness

| Status | Meaning |
|---|---|
| `PASS` | The configured command exited 0, no source/test file changed during the check, and all declared artifacts (if any) existed. Fresh only while the fingerprint matches. |
| `FAIL` | The configured command returned non-zero. |
| `UNKNOWN` | Timeout, spawn error, path escape, empty scope, file changed during hashing, corrupt/unreadable ledger, or no receipt yet. Never treated as PASS. |
| `STALE` | A receipt exists, but the current fingerprint or check definition differs. The historical receipt is retained; the current status is STALE. |

Receipts are immutable. The ledger stores:

- check id and a digest of the check definition;
- command vector, relative cwd, timeout;
- exit code, timeout flag, wall duration;
- SHA-256 of stdout and stderr, plus bounded redacted excerpts;
- every scoped file's path, SHA-256, size and group;
- the input fingerprint, scope summary and the `proves` sentence;
- engine/schema version, platform and Python version.

Every execution appends a receipt. Reruns inside `dedupe_window_seconds` are
annotated with `duplicate_of` and a dedupe count, but they never reuse or hide the
new event: the latest execution and its status remain authoritative.

---

## The native hooks

### `pre_verify`

Fires only when Hermes edited code this turn and a `pre_verify` hook is registered. The plugin:

1. selects configured checks whose source/test globs match the changed paths;
2. computes the current fingerprint (bounded by `gate_deadline_ms`);
3. if every relevant check has a fresh PASS, returns nothing;
4. otherwise returns one `{"action": "continue", "message": ...}` asking the agent to run
   `proof_verify` (or the CLI), and never overwrites the final answer.

It is one-shot by default (`nudge_max_attempts: 1`) and Hermes independently caps consecutive
continue directives. It is **not** a fail-closed lock on every answer, it does not gate
non-coding conversations, and it cannot make a model tell the truth. It makes a missing
verification explicit and gives the agent a deterministic path to a receipt.

### `post_tool_call`

Records tool *activity* (tool name, command hash, result hash, optional exit code, duration)
in the local ledger. These rows are labelled `UNTRUSTED_CONTEXT` and are never used as PASS
evidence: the model chose the command and could have displayed anything. Only a
plugin-executed, configured check creates evidence.

---

## Security and privacy model

### Rule 13 disclosure

- Configured checks execute **automatically without a per-run model prompt**, with
  your OS privileges. They are **not a sandbox** and may use the network, read
  files, spawn descendants, or consume CPU/memory/disk; the plugin does not
  restrict what an operator-configured command does.
- The deterministic plugin engine makes **no network calls and no LLM calls** of
  its own. A nudge can still add full-agent turns; zero engine model calls is not
  zero token cost.
- Workspace reads are limited to declared source/test/artifact globs. The
  fingerprint refuses credential-shaped paths (for example `.env`, `auth.json`,
  `.ssh`, key material) even if a glob would match them.
- Plugin state is SQLite metadata/receipt fields and bounded redacted excerpts
  under the profile's `plugin-data/completion-proof/`, with retention/GC; no
  daemon, telemetry or updater. `init --write` is the explicit profile write path.
- `pre_verify` is a bounded one-shot request, **not a fail-closed gate**.
- Checks have a 1-600 second timeout; deliberately escaped descendants are not
  guaranteed to be killed.
- The plugin never reads raw credential stores; secrets belong to the operator's
  environment/Hermes routes, not to plugin config.
- Configuring a check is the operator's authorization for that check and scope.
- Local evidence is not cryptographic attestation and a PASS is not a proof of
  correctness.

- **Trusted configuration vs model input.** Tools accept only configured `check_ids` (and a
  report format). They cannot pass `argv`, `cwd`, `env`, paths or globs. Extra keys are ignored
  and the configured argv is what runs.
- **No shell.** `shell=False`, fixed argument vector, no interpolation. The model cannot turn
  the plugin into arbitrary command execution.
- **Bounded execution.** Timeout (1–600 s), process-group kill on POSIX, best-effort
  `taskkill /T` on Windows, bounded output capture (64 KiB in memory, default 4 KiB stored per
  stream), and a global scope cap (`max_hash_files`, `max_hash_bytes`).
- **Secret handling.** The plugin never reads `.env`, `auth.json`, keychains or other credential
  stores. Configured checks receive a minimal allowlist environment (PATH/HOME/locale/temp and
  Windows loader essentials); credentials, proxies, `PYTHONPATH`, `HERMES_*` profile/approval
  switches and unknown variables are not forwarded. Output excerpts are redacted with best-effort
  token patterns. This is defence in depth, not a guarantee: a test command that prints a secret
  can still leak it before redaction. Set `store_output: false` to store hashes only.
- **Path safety.** Workspace-relative globs only; `..` and absolute globs are rejected at
  configuration time. POSIX hashing pins the canonical workspace root descriptor and device/inode,
  walks every component with `O_NOFOLLOW`, and hashes by fd; a replaced root or swapped parent is
  UNKNOWN, not an outside PASS. A selected regular file replaced by a FIFO is rejected with
  `O_NONBLOCK` + fstat before any read. Unsupported platforms are UNKNOWN.
- **Minimal child environment.** Configured checks receive an allowlist: PATH, HOME,
  locale/temp and Windows loader essentials. Credentials, proxy settings,
  `PYTHONPATH`, `HERMES_*` profile/approval switches and other unknown variables are
  not forwarded.
- **No network by default.** The deterministic engine contains no network code. A benchmark
  socket guard observes zero attempts. The plugin does not call an LLM to decide evidence.
- **Profile-safe state.** SQLite lives under `$HERMES_HOME/plugin-data/completion-proof/`
  through the public `plugin_db` API, so it follows the active profile. Keep secrets out of it.
- **Approvals.** The plugin never approves anything and never spawns Hermes or shell children
  to bypass approval. It runs only the commands the operator configured.
- **Tamper limits.** Evidence is local. A user or process with the same OS permissions can edit
  the database or replace files between hashing and reporting. Do not describe this as
  cryptographic attestation.

See [`docs/threat-model.md`](docs/threat-model.md) for the threat model and residual risks.

---

## Uninstall

```bash
hermes plugins disable completion-proof
hermes plugins remove completion-proof
# Optional: delete the local receipts for this profile. Use your file manager or
# a path you have verified; the plugin never deletes data on its own.
#   <HERMES_HOME>/plugin-data/completion-proof/
```

The plugin does not modify other profiles or shared runtime files.

## Troubleshooting

| Symptom | Check |
|---|---|
| Plugin not listed | Directory must be `$HERMES_HOME/plugins/completion-proof/` with `plugin.yaml` and `__init__.py`; run `hermes plugins list`, `hermes plugins enable completion-proof`, then `hermes plugins doctor completion-proof --ci`. |
| `proof_verify` “does not exist” in a session | Hermes deferred plugin tools. Use `tool_search`/`tool_describe`/`tool_call`, or the CLI. The pre_verify message includes a `tool_call` example. |
| No `pre_verify` nudge | It only fires when Hermes is in its coding posture, edited code this turn, and a configured glob matches the changed path. Check `coding_context` and the globs. |
| Status is STALE after no visible edit | A declared artifact or test file changed, or a glob matches generated files. Narrow the scope or exclude caches/build output. |
| `UNKNOWN scope_empty` | A configured source/test glob matched no files. Fix the globs; the plugin refuses to record a PASS on an empty scope. |
| `UNKNOWN path_escape` | A symlink resolves outside the workspace. Move the target inside the declared workspace or remove the symlink from scope. |
| Config error in the tool output | Run `hermes completion-proof checks`; fix the reported `checks[n]` field in `config.yaml`. The plugin degrades to an error tool result rather than crashing Hermes. |
| Corrupt ledger | The plugin reports `StoreCorruptError` and refuses to fabricate evidence. Back up `plugin-data/completion-proof/data.db`, then remove it to start a new ledger. |
| Windows | Native Windows is **UNTESTED**. The test environment is Linux under WSL2; WSL results are not Windows validation. The code uses `Path`, `subprocess` with `shell=False`, `CREATE_NO_WINDOW`, and a best-effort `taskkill` tree kill, but no Windows run is claimed. |

---

## Development

```bash
# Unit + adversarial tests (no Hermes, no network)
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v

# One-time self-contained integration bootstrap (never touches a real profile)
CP_HERMES_TEMPLATE_HOME="$(bash scripts/prepare_hermes_template.sh)"

# Real Hermes integration (uses the prepared throwaway home's installs/)
CP_HERMES_TEMPLATE_HOME="$CP_HERMES_TEMPLATE_HOME" \
  python3 -m unittest tests.test_hermes_integration -v

# Reproducible deterministic benchmark (raw rows only; charts derive from them)
python3 scripts/benchmark.py --iterations 20 --seed 20261105 --files 40
python3 scripts/render_site.py

# Standalone deterministic archive + SHA-256 manifest
python3 scripts/package.py --out-dir ../evidence/package

# Full local evidence run (doctor, validate, integration, benchmark, package, screenshots)
CP_HERMES_TEMPLATE_HOME="${TMPDIR:?}/cp-hermes-template" bash scripts/run_evidence.sh
```

Test layout: `tests/test_engine.py` (engine, freshness, adversarial cases),
`tests/test_runtime.py` (tools/CLI/hooks with a fake context),
`tests/test_hermes_integration.py` (actual installed Hermes, `plugins doctor`/`validate`,
real CLI receipts, and a real one-shot agent turn against a local mock OpenAI-compatible
server to fire the native `pre_verify` hook). Windows-native is not tested.

The benchmark measures the deterministic engine only: direct command baseline, an
observe-only run with ledger writes disabled, full engine verify, status read and the stale
gate. It records wall time, parent CPU, reaped child CPU, total CPU, process-tree RSS
(psutil sampling), a separate tracemalloc Python-allocation figure, and DB growth. The
engine itself makes zero network and zero model calls (`llm_calls_by_engine: 0`), but a
`pre_verify` nudge can add agent turns: **full-agent token cost is not measured and is not
claimed to be zero**. The test host is Linux under WSL2; native Windows is untested and is
not claimed. See `evidence/benchmark/resource-report.md`.

---

## Evidence index

| Path | Content |
|---|---|
| [`evidence/environment.json`](../evidence/environment.json) | Host, Python, Hermes version, git commit, generated timestamp. |
| [`evidence/tests/`](../evidence/tests/) | Actual unit and integration test output. |
| [`evidence/install/`](../evidence/install/) | `plugins doctor`, `plugins validate --json`, security-scan outcome, CLI receipts. |
| [`evidence/hooks/`](../evidence/hooks/) | Real agent-turn transcript/log proving `pre_verify` fired and `proof_verify` produced a receipt. |
| [`evidence/benchmark/`](../evidence/benchmark/) | Raw JSON/CSV, derived `summary.json`, generator command and environment. |
| [`evidence/package/`](../evidence/package/) | Deterministic archive, SHA-256 manifest, fresh-install smoke output. |
| [`evidence/site/`](../evidence/site/) | Page captures; the neutral/before state is not presented as product-improvement evidence. |
| [`evidence/demo/`](../evidence/demo/) | Real local demo MP4 (replay of a captured real run, labelled replay), transcript, captions, narration and ffprobe metadata. |
| [`docs/acceptance.md`](../docs/acceptance.md) | Requirement-by-requirement mapping to evidence or an honest FAIL/BLOCKED. |
| [`docs/submission-checklist.md`](../docs/submission-checklist.md) | What would be needed before any public submission. |
| [`docs/catalog-entry.template.yaml`](docs/catalog-entry.template.yaml) | Candidate catalog entry with visibly unresolved URL/SHA placeholders. |
| [`docs/demo-storyboard.md`](docs/demo-storyboard.md) | Actual replay-video description and optional future live-recording plan. |
| [`site/index.html`](site/index.html) | Local static marketing page, no trackers, no paid services. |

---

## Submission status

**Technical kit: complete for this offline run. Submitted entry: no.**

- Implemented, tested, benchmarked and packaged: plugin, engine, hooks, CLI/tools/slash,
  unit/adversarial/regression tests, real Hermes install/doctor/validate/security scan,
  native `pre_verify` integration, fresh archive install, demo video, site, docs and the
  standalone submission kit.
- External prerequisites for an actual entry: public repository, full 40-character catalog
  SHA pin, catalog PR to `NousResearch/hermes-agent`, X demo post, Typeform submission,
  and the operator's own eligibility decision. Mauritius is not a worker-side blocker; the
  operator decides whether to apply. These are human-authorized steps, not product
  limitations; see [`docs/publication-and-submission.md`](../docs/publication-and-submission.md).
- This run's authorization boundary: no merge, publish, public repository, catalog PR, X
  post, form submission, paid service or production install was performed. Native Windows
  remains untested; no Windows claim is made.

---

## En bref (français)

Completion Proof est un plugin natif Hermes qui transforme une exécution de contrôles
**configurés par l’opérateur** en reçus PASS/FAIL/UNKNOWN liés aux empreintes SHA-256 des
fichiers réellement déclarés (source, tests, artefacts). Toute modification pertinente rend
l’ancien PASS **STALE**. Le moteur est local et déterministe : pas de réseau, pas de LLM pour
décider de la preuve. Le modèle ne peut fournir ni commande, ni répertoire, ni variable
d’environnement : il ne peut que nommer un contrôle déjà configuré. Un PASS ne prouve que la
sortie 0 de la commande configurée sur le périmètre déclaré, pas l’exactitude de tout le
logiciel. Les preuves sont locales et non infalsifiables par un processus disposant des mêmes
permissions. Le hook `pre_verify` ne se déclenche que sur les tours de codage ayant modifié des
fichiers et sa relance est bornée : ce n’est pas un verrou absolu. Une vidéo de rejeu réelle
de 23 s est fournie ; aucune soumission publique et aucun dépôt public n’ont été créés.

---

## License

MIT for newly authored original code. See [`LICENSE`](LICENSE). Third-party names and links
belong to their owners; no third-party code was copied into this plugin.

Hermes is a Nous Research project; this plugin is an independent candidate, not an official
Nous product.
