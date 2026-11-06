# Submission checklist (offline candidate)

This checklist separates **code readiness** from **submission readiness**. Nothing
below was submitted. Checked items are backed by committed evidence; unchecked
items are blockers, not failures to hide.

## Code and package

- [x] Native Hermes plugin with `plugin.yaml` and `__init__.py`.
- [x] Uses only public Hermes plugin surfaces.
- [x] `hermes plugins doctor` passes with real registration (`4 tool(s), 2 hook(s)`).
- [x] `hermes plugins validate --json` passes; security scan result `safe`.
- [x] Real install from the deterministic archive in an isolated `HERMES_HOME`.
- [x] Unit + adversarial tests (false PASS, timeout/child cleanup, stale source,
      stale test, missing artifact, corrupt DB, malformed config, duplicate calls,
      symlink escape, path escape, file changed during hash, bounded nudge,
      non-code turn, genuine green, workspace isolation, SQLite concurrency).
- [x] Real one-shot agent turn exercises `post_tool_call` and native `pre_verify`;
      `proof_verify` produced a PASS receipt.
- [x] Reproducible archive + SHA-256 manifest.
- [x] MIT license for newly authored code.
- [x] README in English with French summary; changelog; version manifest;
      troubleshooting; uninstall (uninstall instructions in README).
- [x] Local static site with generated charts from committed raw data.
- [x] Raw benchmark JSON/CSV with environment and generator command.
- [x] Actual CLI demo transcript (<90 s scenario), labelled transcript not video.
- [x] Acceptance mapping with FAIL/BLOCKED items stated.

## Submission blockers (not done)

- [ ] Public repository created (external, human-authorized step; not executed here).
- [ ] Full 40-character catalog SHA pin (needs the public commit).
- [ ] Catalog entry `repo`/`sha`/`docs_url` resolved.
- [ ] Catalog PR to `NousResearch/hermes-agent` (needs human submission).
- [x] Real local demo video under 90 seconds recorded (`evidence/demo/completion-proof-demo.mp4`), labelled replay.
- [ ] Demo posted on X tagging `@NousResearch` (external, human-authorized).
- [ ] Typeform submission completed with PR/X links.
- [ ] Operator decides whether to apply; Mauritius eligibility is not confirmed and is not a worker-side gate.
- [ ] Exact judging weights / detailed rules confirmed (not published).
- [ ] Native Windows validation (UNTESTED; WSL2 is not Windows).
- [x] Packaged-copy native hook smoke (`evidence/package/fresh-install.json`).
- [x] Separate submission kit with hashes and working site links (`submission-kit/`).
- [x] Sanitized public export prepared at `.scratch/public-export/` with root product README, link/leak checks and archive installation recheck.
- [ ] Human review of the private PR and any requested changes.

## Disclosure text for a future PR (draft)

> Completion Proof executes configured argv checks **automatically without a
> per-run model prompt**, with the user's OS privileges; it is **not a sandbox**.
> Checks may themselves use the network, read files, spawn descendants or consume
> CPU/memory/disk. The deterministic engine makes no network/model calls, but a
> nudge can add full-agent turns; zero engine calls is not zero token cost. It
> hashes declared source/test/artifact files under a configured workspace with a
> pinned root FD/`O_NOFOLLOW`/fd hashing, refuses credential-shaped paths, stores
> SQLite metadata/scope hashes/bounded redacted excerpts with retention/GC under
> `$HERMES_HOME/plugin-data/completion-proof/`, and has no telemetry, daemon,
> updater or self-update. `init --write` is the explicit profile write path. The
> model can select configured check ids but cannot supply a command, working
> directory, environment, path or glob. Checks have a 1–600 s timeout; a
> deliberately escaped descendant is not guaranteed to be killed. A PASS proves
> only that the configured command exited 0 on declared scope; evidence is local
> and not cryptographic or a correctness proof. `pre_verify` is a bounded,
> one-shot request, not a fail-closed gate. Credential protection is name/path and
> allowlist based, not a content guarantee. Requires Hermes >=0.22.7, the actual
> exercised runtime; Linux tested, Windows-native untested.

## Suggested one-line description

> Evidence receipts for coding completion: configured checks, SHA-256-scoped
> freshness, PASS/FAIL/UNKNOWN/STALE, and a bounded native verification request.

## Submission statement

**Not executed in this run.** This run's authorization excludes merge,
publication, public repository, catalog PR, form submission, X post and production
install. The technical kit is complete; an actual submitted entry requires the
external human-authorized steps in `docs/publication-and-submission.md`. These are
run boundaries, not permanent product limitations.
