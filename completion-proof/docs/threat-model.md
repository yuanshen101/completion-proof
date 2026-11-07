# Threat model and security notes

## Assets

1. The integrity of evidence shown to the user (PASS/FAIL/UNKNOWN/STALE and the
   receipt fields).
2. The user's files and machine, which the configured check can touch.
3. Secrets in the environment or in command output.
4. The local ledger and the plugin's own code.
5. The trust model of the Hermes plugin catalog (no self-update, pinned SHA,
   public surfaces only).

## Actors

- **Operator**: writes trusted configuration and runs checks.
- **Agent/model**: may call plugin tools, read receipts, and claim completion.
  It is not trusted to supply commands or evidence.
- **Hermes host**: dispatches hooks and tools, enforces approvals and budgets.
- **Local attacker/process**: has the same OS permissions as the plugin and can
  edit the ledger or workspace files.
- **Third-party check**: the command the operator configures; its correctness is
  outside the plugin's control.

## Trust boundaries

- Trusted: `$HERMES_HOME/config.yaml` settings, the plugin code at the installed
  revision, and the deterministic engine.
- Untrusted: tool-call arguments, model text, tool results, `post_tool_call`
  content, environment values not explicitly configured, filesystem changes that
  race with hashing.
- The operator's check command is trusted to the same degree as the operator's
  shell; the plugin does not sandbox it.

## Threats and mitigations

| Threat | Mitigation | Residual risk |
|---|---|---|
| Model passes `argv`/cwd/env to execute arbitrary commands | Tool schemas expose only configured `check_ids`; extra keys are ignored; argv/cwd/env come only from validated settings | A user who configures a malicious check has authorized it |
| Shell injection | `shell=False`; argument list only; a single-string argv is rejected | The configured program itself may interpret its arguments unsafely |
| `..`/absolute glob escapes | Rejected during config validation; every resolved file must stay under the workspace | Workspace itself may contain sensitive files the operator chose to hash |
| Symlink / root replacement | Pinned workspace root FD + device/inode; directory-FD `O_NOFOLLOW` walk for every component; hash by fd; swapped root/parent → `workspace_replaced`/`secure_open_failed` UNKNOWN | Same-permission attacker can change content after the receipt; no cryptographic attestation |
| TOCTOU between hash and use | Before/after `stat` (size, mtime_ns, inode) while reading; source/test changed during check → UNKNOWN | Same-permission attacker can still swap content after the receipt |
| Runaway/hung check | 1–600 s timeout; POSIX process-group `SIGKILL`; Windows best-effort `taskkill /T`; bounded output buffers; readers joined with a bound | A child that escapes the process group can survive; Windows tree kill is best-effort |
| Huge output/disk growth | 64 KiB retained per stream, default 4 KiB stored per stream, truncated marker, hashes of full streams | A huge output still consumes pipe/disk time before hashing |
| Huge hash scope | `max_hash_files`, `max_hash_bytes` caps → UNKNOWN; explicit globs | Large repositories can hit the cap; user must narrow scope |
| Secrets in stored output | `store_output` toggle; default 4 KiB excerpts; best-effort regex redaction; minimal allowlist child env (no credentials, proxies, `PYTHONPATH`, `HERMES_*` switches) | A secret printed in an unexpected format can survive redaction; disable output storage for strict privacy |
| Secret env inheritance | Child receives only PATH/HOME/locale/temp and Windows loader essentials; unknown names are never forwarded | Test commands may need credentials; the plugin does not provide them |
| Ledger tampering | Local SQLite with WAL; receipts immutable in normal operation; verification log | Same-permission user can edit or delete it; **not cryptographic attestation** |
| Corrupt/malformed DB | `StoreCorruptError`, no silent recreation, explicit CLI error | User must back up/remove the DB to recover |
| Concurrent writers / lock | Writes use bounded retries; reads use a separate read-only connection with `busy_timeout=0`; a busy snapshot becomes UNKNOWN within the gate budget | A filesystem that blocks a syscall is still bounded best-effort, not hard realtime |
| Receipt storage growth | Retention bounds storage; the dedupe window only annotates duplicate runs and never elides a stored receipt | Every execution, including a changed timestamp/output, is a real row; compact/GC is an explicit retention operation |
| Model claims tests passed without evidence | `pre_verify` nudge; receipts only from plugin-run configured checks; `post_tool_call` observations labelled UNTRUSTED_CONTEXT | The nudge is bounded and not fail-closed; the model can still finish without proof |
| Infinite nudge loop | Plugin returns continue only while `attempt < nudge_max_attempts` (default 1); Hermes caps consecutive nudges; no hook on non-code turns | A misconfigured Hermes with a very high cap could still spend extra turns |
| Plugin tool deferred by `tool_search` | Nudge includes `tool_call` syntax; CLI/slash always available; README documents it | The model may still fail to invoke it; the receipt is then absent, not faked |
| Network exfiltration by check | Engine has no network code; checks themselves can use network if configured | The plugin cannot prevent a configured test command from networking |
| Credential-store reads | Plugin never reads `.env`, `auth.json`, keychains | A configured command can read whatever the operator's permissions allow |
| Self-update / mutable code | No updater; standalone archive with SHA-256 manifest; catalog pin remains the trust model | The user must verify the archive/pin |

## Non-goals / honest limitations

- Not a sandbox: running a check executes operator-authorized code with operator
  permissions.
- Not a proof of correctness: a green command can miss most of the software.
- Not tamper-proof: local evidence can be edited by the same user.
- Not a complete gate: `pre_verify` only fires on code-edited coding turns and is
  bounded by design.
- Not validated on native Windows in this environment; WSL2 is not Windows.
- Not a public submission: no catalog pin, X post or form. A real 23 s replay
  video is supplied; it is not a live recording.

## Security scan outcome

`hermes plugins validate` ran the install security scan at the plugin directory.
The recorded result is `safe`; the exact JSON is in
`evidence/install/validate.json`. The scan is one input, not a proof of safety.
The plugin deliberately executes configured commands (`subprocess`), which the
scan considers; the design restricts the command source to operator config and
runs it with `shell=False`.
