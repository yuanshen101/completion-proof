# Demo narration (English)

Completion Proof is a native Hermes plugin. This is a replay of a real CLI run;
the commands and results were executed for this capture, and video timing is not live.

1. `hermes completion-proof checks` shows the operator-configured check. The model
   cannot supply argv, cwd, env or globs.
2. The configured test fails, so the receipt is FAIL. Passing text in output is not
   independent proof.
3. Fix the source and run the configured check again.
4. The receipt is PASS and the current status is fresh.
5. Change one covered source file; the status becomes STALE. Historical evidence is
   retained, but it is not proof for this revision.

## Résumé français

Completion Proof transforme une exécution de contrôles configurés en reçus
PASS/FAIL/UNKNOWN liés aux empreintes SHA-256 des fichiers déclarés. Un PASS
devient STALE dès qu'un fichier pertinent change. Le moteur est local et
déterministe, sans réseau ni LLM. Cette vidéo est un replay d'une exécution réelle ;
le temps d'exécution mesuré est indiqué séparément.

Actual command execution elapsed: 5.75s.
