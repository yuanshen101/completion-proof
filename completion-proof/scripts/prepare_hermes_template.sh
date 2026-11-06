#!/usr/bin/env bash
# Prepare a throwaway Hermes home whose installs/ can be symlinked by tests.
#
#   bash scripts/prepare_hermes_template.sh [scratch-root]
#
# The first bootstrap may take a few minutes and downloads nothing beyond what
# the installed Hermes launcher normally prepares. It never writes to a real
# profile: HERMES_HOME and HOME point into the new scratch directory.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="${1:-${CP_SCRATCH:-${TMPDIR:-}}}"
if [ -z "$ROOT" ]; then
  ROOT="$PLUGIN_ROOT/../.scratch"
fi
mkdir -p "$ROOT"
ROOT="$(cd "$ROOT" && pwd)"
TEMPLATE=$(python3 -c 'import sys, tempfile; print(tempfile.mkdtemp(prefix="cp-hermes-template-", dir=sys.argv[1]))' "$ROOT")
TEMPLATE="$(cd "$TEMPLATE" && pwd)"
mkdir -p "$TEMPLATE/userhome" "$TEMPLATE/tmp"
cat > "$TEMPLATE/config.yaml" <<'YAML'
updates:
  check: false
YAML
echo "Preparing a throwaway Hermes home at $TEMPLATE (one-time bootstrap)..." >&2
python3 "$SCRIPT_DIR/clean_exec.py" \
  --hermes-home "$TEMPLATE" --home "$TEMPLATE/userhome" --tmp "$TEMPLATE/tmp" \
  -- hermes plugins list >/dev/null
# Commit the dependency environment explicitly; a passive startup may leave it
# staged but uncommitted on a fresh home.
python3 "$SCRIPT_DIR/clean_exec.py" \
  --hermes-home "$TEMPLATE" --home "$TEMPLATE/userhome" --tmp "$TEMPLATE/tmp" \
  -- hermes pm repair >/dev/null
if [ ! -d "$TEMPLATE/installs" ]; then
  echo "bootstrap did not create installs/ under $TEMPLATE" >&2
  exit 2
fi
echo "$TEMPLATE"
