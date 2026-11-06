# Contributing

This is an offline candidate build in a private sandbox. Contributions should keep
the plugin independently packageable and should not introduce private paths,
credentials or network requirements.

## Development setup

```bash
cd completion-proof
python3 -m unittest discover -s tests -v
```

The unit suite needs only Python and the standard library. `scripts/run_tests.sh`
also runs the real-Hermes suite when `CP_HERMES_TEMPLATE_HOME` points to a
prepared throwaway Hermes home (the suite skips otherwise).

## Rules

1. Use only public Hermes plugin surfaces (`register_*`, hooks, middleware,
   `plugin_db`, `ctx.get_config`). Do not monkeypatch core, private attributes or
   Desktop markup.
2. Keep the trust boundary: tool schemas must not accept argv, cwd, env, paths or
   globs. Check definitions come only from operator configuration.
3. Never treat model/tool output as evidence. Only plugin-run configured checks
   create receipts.
4. Keep `shell=False`, bounded timeouts, bounded output and path containment.
5. Add a test for every behavior change, especially freshness and failure cases.
6. Update `CHANGELOG.md`, `VERSION` and `plugin.yaml` together.
7. Regenerate evidence after behavior changes; do not edit evidence by hand.
8. No secrets, absolute local paths or large third-party artifacts in commits.
9. Do not add an auto-updater, telemetry, background daemon or default network
   call.
10. MIT for newly authored code; retain third-party attribution and licenses.

## Evidence regeneration

```bash
CP_HERMES_TEMPLATE_HOME="$(bash scripts/prepare_hermes_template.sh)" \
CP_CHROME_BIN=/path/to/chrome \
bash scripts/run_evidence.sh
```

This rebuilds `evidence/`, charts, screenshots, package and demo transcript.
Review the generated diff, then commit it with the code change.

## Commit / PR expectations

- Small, reviewable commits with a clear message.
- State user-visible outcome and actual checks run in the PR.
- Disclose any new execution, filesystem, network or storage behavior.
- Do not merge or enable auto-merge from the agent account.
- Windows-native claims require an actual Windows run; WSL2 is not Windows.
