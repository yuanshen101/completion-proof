# Differentiation from nearby plugins

This note uses the public catalog descriptions and repositories gathered in the
merged research docs (`docs/hackathon-plugin-shortlist.md`,
`docs/hackathon-plugin-scores.json` in the sandbox repository) as starting
input. It does not claim a code audit of competitors.

| Plugin | Public description (summary) | Overlap | Difference |
|---|---|---|---|
| **afterforge** | Local regression workbench turning agent failures/corrections into reviewed, evidence-backed checks; uses `post_tool_call`, `on_session_end`, `pre_verify`. | Both care about evidence and `pre_verify`. | afterforge builds regression checks from failures. Completion Proof is a general completion gate: any operator-configured check, revision-bound source/test/artifact fingerprints, explicit PASS/FAIL/UNKNOWN/STALE, and staleness after source *or* test edits. |
| **crew** | Work on a kanban board is done only when a proof command exits 0; `crew_proof_check`. | Proof command per work item. | crew is tied to kanban cards and task lifecycle. Completion Proof is session/turn-scoped, works outside kanban, and binds the receipt to a fingerprint rather than only a command exit. |
| **agent-ledger** | Write-ahead journal of side effects with digests and unconfirmed-call warnings. | Local ledger, digests. | A journal records what happened; Completion Proof decides whether the *current* revision has fresh evidence and invalidates old green runs. |
| **tracekit** | Signs each tool call/result for offline verification; blocking `pre_tool_call`. | Tool-call integrity. | Tracekit is a tool-call chain; Completion Proof is about completion claims and configured checks. It explicitly refuses to treat tool-call content as proof. |
| **command-tray** | Records terminal commands (exit code, workdir, preview). | Command history. | Command history is not evidence of the current artifact. Completion Proof hashes the declared scope and marks old receipts STALE. |
| **provenance-gate** | Checks that “the user decided” claims have verbatim support. | Claim/evidence gating. | Different claim class: user decisions vs code-completion claims. |
| **heartbeat2 / pocket-watch** | Deterministic watchers and cron health dashboards. | Deterministic local checks. | They watch external effects/jobs; Completion Proof binds a check result to a source/test/artifact revision for a coding completion. |

## The specific gap Completion Proof targets

The closest neighbors either (a) record commands/tool calls, (b) build checks
from prior failures, or (c) attach a proof command to a task card. None of the
public descriptions state that an old green result becomes **STALE** when the
source *or test* files change, while also separating operator-trusted check
configuration from model tool inputs and keeping a bounded, honest
`pre_verify` request.

That combination is the product: **a receipt, not a receipt-like log**.

## Honest weaknesses of this angle

- The concept is adjacent to several plugins; judges may see it as a refinement,
  not a new category.
- The built-in verify-on-stop guidance already nudges for verification; the
  plugin adds deterministic evidence and freshness, not a wholly new workflow.
- The gate is bounded and not fail-closed, so it cannot guarantee that a model
  reports the truth.
- The strongest differentiator (freshness) is only visible after the user edits
  a covered file; the demo must show that transition.
