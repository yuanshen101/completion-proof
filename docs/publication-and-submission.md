# Publication and submission steps (not executed)

This document is the exact human-authorized handoff for turning the finished
offline kit into a submitted hackathon entry. **Nothing in this document was
executed by the agent run.** The technical kit is complete; the submitted entry
is not.

## Boundary of this run

- Authorized: implementation, review fixes, local tests, demo recording, measured
  evidence, packaging, and the offline submission kit in this sandbox.
- Not authorized/executed: merge, publication, public repository creation,
  catalog PR, X post, Typeform submission, paid services, production install,
  shared-runtime changes, secrets, or reading operator accounts/mail/memory.

These are run-authorization boundaries, not permanent product limitations.

## External prerequisites (each is human-controlled)

1. **Public repository** — create an https cloneable repo (the plugin root is
   independently packageable). Keep the MIT license and attribution.
2. **Full SHA pin** — push a reviewed commit and record the exact 40-character
   SHA; branches, tags and short SHAs are rejected by the catalog loader.
3. **Catalog validation** — run
   `hermes plugins validate /path/to/completion-proof --install-deps` at that
   commit; fix warnings a reviewer will read.
4. **Catalog PR** — add `plugin-catalog/completion-proof.yaml` to
   `NousResearch/hermes-agent` with the schema below, disclose execution and
   local storage, and wait for human review and CI.
5. **Demo video** — record a real run under 90 seconds. The offline kit includes a
   replay MP4 (`evidence/demo/completion-proof-demo.mp4`) with transcript,
   captions, narration and ffprobe metadata. A submission video must be recorded
   live or clearly labelled as a replay; do not add unsupported speed claims.
6. **X post** — post the demo video tagging `@NousResearch`.
7. **Typeform** — provide first name, plugin title, description, email, Discord,
   X demo link and GitHub PR link.
8. **Eligibility** — the operator decides whether to apply; Mauritius is not a worker-side gate. Do not claim eligibility is confirmed, and
   confirm age/tax/travel terms. This remains unresolved; do not infer it.

## Candidate catalog entry

Use `completion-proof/docs/catalog-entry.template.yaml`, replacing the visibly
unresolved placeholders:

```yaml
name: completion-proof
repo: <PUBLIC_REPOSITORY_URL>
sha: <FULL_40_CHARACTER_COMMIT_SHA>
subdir: completion-proof
description: Evidence receipts for coding completion, bound to the current source/test/artifact fingerprints and invalidated when they change.
maintainer: yuanshen101
tier: community
category: tools
requires_hermes: ">=0.22.7"
docs_url: <PUBLIC_README_URL_AT_SHA>
version: "0.1.5"
platforms: [linux]
capabilities:
  provides_tools: [proof_list_checks, proof_status, proof_verify, proof_report]
  provides_hooks: [post_tool_call, pre_verify]
  provides_middleware: []
  requires_env: []
```

## Publication disclosure (draft)

> Completion Proof executes configured argv checks **automatically without a
> per-run model prompt**, with the user's OS privileges; it is **not a sandbox**.
> Checks may themselves use the network, read files, spawn descendants or consume
> CPU/memory/disk. The deterministic engine makes no network/model calls, but a
> nudge can add full-agent turns; zero engine calls is not zero token cost. It
> hashes declared source/test/artifact files under a configured workspace with a
> pinned root FD/`O_NOFOLLOW`/fd hashing, refuses credential-shaped paths, stores
> SQLite receipt fields and bounded redacted excerpts with retention/GC under
> `$HERMES_HOME/plugin-data/completion-proof/`, and has no telemetry, daemon,
> updater or self-update. `init --write` is the explicit profile write path. The
> model can select configured check ids but cannot supply a command, working
> directory, environment, path or glob. Checks have a 1–600 s timeout; a
> deliberately escaped descendant is not guaranteed to be killed. A PASS proves
> only that the configured command exited 0 on declared scope; evidence is local
> and not cryptographic or a correctness proof. `pre_verify` is a bounded,
> one-shot request, not a fail-closed gate. Requires Hermes >=0.22.7; current
> 0.1.5 evidence was exercised with 0.23.0 (earlier runs 0.22.7). Linux tested,
> Windows-native untested.

## Submission text (draft)

**Short:** “Completion Proof turns configured test runs into revision-bound
receipts. Change a covered file and the old PASS becomes STALE — evidence, not a
memory of a command.”

**Long:** “Coding agents say ‘tests pass’ without independent evidence. Completion
Proof is a native Hermes plugin: operators declare checks and scopes; the plugin
runs them locally, hashes source/test/artifacts, records PASS/FAIL/UNKNOWN
receipts, and invalidates old passes when relevant files change. A native
`pre_verify` hook asks once for verification on code-edited turns. Local-first,
deterministic, no network/LLM in the check engine.”

The private `submission-kit/` is a historical offline archive for earlier review;
the clean public export intentionally excludes it and carries its own root
`SHA256SUMS`, `link-check.json` and `scan.json`.

## Verification checklist for the human publisher

- [ ] `sha256sum -c SHA256SUMS` passes in the clean public export.
- [ ] Plugin archive installs into a fresh `HERMES_HOME`; doctor/validate pass.
- [ ] Catalog validation at the public SHA passes; security scan is not `dangerous`.
- [ ] Video is real, under 90 seconds, and labelled replay if it is a replay.
- [ ] No operator paths, contacts, credentials or private Feishu content in the
      public repo/video/post.
- [ ] Operator decides whether to apply; Mauritius eligibility is not a worker-side gate and is not claimed confirmed.
- [ ] The PR is a deliberate public publication; the agent did not merge or submit.
