#!/usr/bin/env bash
# Actual CLI demo transcript. Runs against a throwaway HERMES_HOME.
# The MP4 replay is produced by scripts/record_demo.py.
#
#   CP_HERMES_TEMPLATE_HOME="${TMPDIR:?}/cp-hermes-template" \
#     bash scripts/demo.sh | tee evidence/demo/session.txt
#
# The prepared home only supplies the Hermes install tree; the plugin, project,
# ledger and config are created fresh under a temporary directory.
set -u

if [ -z "${CP_HERMES_TEMPLATE_HOME:-}" ] || [ ! -d "$CP_HERMES_TEMPLATE_HOME/installs" ]; then
  echo "BLOCKED: CP_HERMES_TEMPLATE_HOME with installs/ is required for the isolated demo." >&2
  exit 2
fi
if ! command -v hermes >/dev/null 2>&1; then
  echo "BLOCKED: hermes is not on PATH." >&2
  exit 2
fi

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T=$(python3 -c 'import tempfile; print(tempfile.mkdtemp(prefix="cp-demo-"))') || exit 1
cleanup() {
  if [ -f "$T/home/gateway.pid" ]; then
    pid=$(python3 -c "import json;print(json.load(open('$T/home/gateway.pid'))['pid'])" 2>/dev/null || true)
    [ -n "${pid:-}" ] && kill -9 "$pid" 2>/dev/null || true
  fi
  rm -rf "$T"
}
trap cleanup EXIT

mkdir -p "$T/home/plugins" "$T/home-user" "$T/tmp" "$T/proj/src" "$T/proj/tests"
ln -s "$CP_HERMES_TEMPLATE_HOME/installs" "$T/home/installs"
cp -r "$PLUGIN_ROOT" "$T/home/plugins/completion-proof"
find "$T/home/plugins/completion-proof" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

cat > "$T/proj/src/app.py" <<'PY'
VALUE = 1
PY
cat > "$T/proj/tests/test_app.py" <<'PY'
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from app import VALUE

class T(unittest.TestCase):
    def test_value(self):
        self.assertEqual(VALUE, 2)

if __name__ == "__main__":
    unittest.main()
PY

python3 - "$T" <<'PY'
import sys, yaml
from pathlib import Path
T = Path(sys.argv[1])
config = {
    "plugins": {
        "enabled": ["completion-proof"],
        "entries": {
            "completion-proof": {
                "enabled": True,
                "settings": {
                    "workspace": str(T / "proj"),
                    "checks": [{
                        "id": "unit",
                        "description": "fixture unittest (fails until the bug is fixed)",
                        "argv": [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                        "source_globs": ["src/**/*.py"],
                        "test_globs": ["tests/**/*.py"],
                        "timeout_seconds": 30,
                    }],
                },
            }
        },
    }
}
(T / "home" / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
PY

cd "$T/proj"
START=$(date +%s)
echo "Completion Proof — real CLI demo (isolated HERMES_HOME)"
echo "Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Fixture: src/app.py VALUE=1, test expects 2"
echo

hermes_run() {
  python3 "$PLUGIN_ROOT/scripts/clean_exec.py" \
    --hermes-home "$T/home" --home "$T/home-user" --tmp "$T/tmp" -- "$@"
}

run() {
  echo "\$ $*"
  hermes_run "$@"
  echo "[exit $?]"
  echo
}

echo "== 1. The plugin is installed and enabled =="
env HERMES_HOME="$T/home" HOME="$T/home-user" hermes plugins list --json \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print([ (p["name"], p["status"]) for p in d if p["name"]=="completion-proof"])'
echo

echo "== 2. Run the configured check: it fails, and the exit code is FAIL =="
run hermes completion-proof verify
echo "The model's claim 'tests passed' would not match this receipt."

echo "== 3. Fix the source (VALUE=2) and re-run =="
cat > src/app.py <<'PY'
VALUE = 2
PY
run hermes completion-proof verify

echo "== 4. The receipt is fresh =="
run hermes completion-proof status

echo "== 5. Change a covered source file: the old PASS becomes STALE =="
cat > src/app.py <<'PY'
VALUE = 3
PY
run hermes completion-proof status
echo "Historical PASS is retained; current status is STALE. This is the core freshness property."

echo "== 6. Report shows what PASS does and does not prove =="
hermes_run hermes completion-proof report | head -30
echo

END=$(date +%s)
echo "Demo transcript complete in $((END - START))s. This is a terminal transcript; see record_demo.py for the MP4 replay."
echo "Native pre_verify hook evidence (real agent turn) lives in evidence/hooks/."
