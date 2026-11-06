# Limitations and unsupported cases

Completion Proof is intentionally narrow. Read this before relying on a receipt.

## What a PASS proves

Only this: the configured command exited 0 before its timeout, no declared
source/test file changed during execution, every declared artifact existed, and
the recorded SHA-256 set matches the declared scope. It is not a statement that
the software is correct or complete.

## Known limitations

1. **A green command can be incomplete.** A test suite that forgets a case, or a
   command that exits 0 without running the tests, still yields PASS. Use more
   than one check and review what each command actually runs.
2. **`pre_verify` is not a fail-closed lock.** It fires only on coding turns that
   edited code, only when a registered hook exists, and only once by default. A
   model can finish after the bounded nudge without proof. The plugin never
   rewrites the final answer.
3. **Only declared scope is hashed.** Files outside the globs can change without
   invalidating a receipt. Generated files inside the scope can cause STALE noise;
   narrow the globs or add `exclude_globs`. Artifact-only changes are included in
   the gate's relevance match when `artifact_globs` is non-empty. A PASS proves only
   existence/content after the check for artifacts; it does not prove the command
   generated that artifact.
4. **A command can exit 0 while collecting zero tests.** If the declared test
   files exist but the framework collects none, the plugin cannot know; it does
   not parse output as proof. Declared source/test globs that match no files are
   rejected as UNKNOWN (unless `allow_missing_patterns` is explicitly enabled).
5. **Artifact hashes can be non-deterministic.** A build that embeds timestamps
   or random data changes the artifact fingerprint every run, so a receipt never
   becomes fresh. Exclude volatile artifacts or make the build reproducible.
6. **Local tamper resistance is limited.** A process with the same OS permissions
   can edit the SQLite ledger, replace files after the receipt, or alter the
   plugin. This is not cryptographic attestation.
7. **Commands are trusted.** The operator's check runs with the operator's
   permissions. The plugin is not a sandbox and cannot stop a configured command
   from reading files or using the network.
8. **Output redaction is best-effort.** Unusual secret formats can survive the
   excerpt. Use `store_output: false` for strict output privacy.
9. **Plugin tools may be deferred.** Hermes can hide plugin tools behind
   `tool_search`/`tool_call`. The nudge and README show the fallback; the CLI and
   slash command are always available.
10. **Windows-native is UNTESTED.** The test host is Linux under WSL2. The code
   uses `Path`, `shell=False`, `CREATE_NO_WINDOW`, and a best-effort
   `taskkill /T` tree kill, but no native Windows run is claimed. WSL2 results
   must not be reported as Windows validation.
11. **No full agent benchmark.** The benchmark covers the deterministic engine
    only: direct command, verify, status and the stale gate. It does not measure
    full Hermes turns, token changes or user outcomes. `llm_calls_by_engine: 0`
    refers to the engine measurement, not an agent run.
12. **`pre_verify` timing depends on Hermes.** The coding posture and changed-path
    tracking are host behavior. The integration test forces `coding_context: on`
    in an isolated home; a real user's workspace may resolve differently.
13. **Not a submitted entry in this run.** Publication requires a public repository,
    a full 40-character catalog pin, a catalog PR, an X demo post, a Typeform
    submission and the operator's eligibility decision. Those are external, human-authorized
    steps. The technical kit is complete; the entry has not been submitted.

## Unsupported / not implemented

- Automatic discovery or generation of arbitrary checks from a conversation.
- Running a check the operator has not configured.
- Network or LLM calls inside the deterministic engine.
- Cryptographic signing, remote attestation, or tamper-proof storage.
- A Desktop UI, dashboard tab, or marketplace listing.
- Auto-update, background daemons, or scheduled execution.
- Windows-native validation or a macOS validation claim.
- Merge, publish, public repo creation, upstream PR, or hackathon submission.

## Security boundary notes for the current version

- POSIX fingerprinting traverses each path with directory file descriptors and
  `O_NOFOLLOW`, then hashes by descriptor. A symlink swapped into any parent is
  refused. Platforms without this guarantee return UNKNOWN rather than claiming
  a weaker containment promise.
- Credential-shaped paths (`.env`, `auth.json`, `.ssh`, key material, etc.) are
  refused by name even if an operator glob would match them.
- A deliberately escaped descendant (`start_new_session=True`) cannot be killed
  through the parent's process group; the engine returns bounded with incomplete
  output/UNKNOWN and discloses that residual risk.
- Configured commands run with operator OS privileges and are not sandboxed.
