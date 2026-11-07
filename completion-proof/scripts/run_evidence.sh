#!/usr/bin/env bash
# Regenerate the local evidence bundle with real commands and real outputs.
#
#   CP_HERMES_TEMPLATE_HOME="${TMPDIR:?}/cp-hermes-template" \
#     bash scripts/run_evidence.sh
#
# The template home is only used to skip the one-time ~900 MB Hermes install
# bootstrap; it is never modified and never committed. Without it, unit,
# benchmark, package, site and demo-limited evidence still run; install evidence
# is explicitly marked BLOCKED.
set -uo pipefail
cd "$(dirname "$0")/.."
if [ -n "${TMPDIR_OVERRIDE:-}" ]; then
  export TMPDIR="$TMPDIR_OVERRIDE"
fi
if [ -z "${TMPDIR:-}" ]; then
  export TMPDIR="$PWD/../.scratch/tmp"
fi
mkdir -p "$TMPDIR"
export PYTHONDONTWRITEBYTECODE=1
OUT=../evidence
mkdir -p "$OUT"/{tests,install,hooks,benchmark,package,site,demo,judge-regressions,judge-regressions-013,judge-regressions-013/cp07a,resource-lifetime,owner-cleanup}
rm -rf "$OUT/tests"/* "$OUT/install"/* "$OUT/hooks"/* "$OUT/benchmark"/* "$OUT/package"/* "$OUT/site"/* "$OUT/demo"/* "$OUT/judge-regressions"/* "$OUT/judge-regressions-013"/* "$OUT/resource-lifetime"/* "$OUT/owner-cleanup"/*

FAILED=0
run_capture() {
  local target="$1"; shift
  echo "+ $*"
  if "$@" >"$target" 2>&1; then
    echo "  ok -> $target"
  else
    local code=$?
    echo "  exit $code -> $target"
    tail -20 "$target" || true
    FAILED=1
  fi
}

echo "== environment =="
python3 scripts/capture_env.py > "$OUT/environment.json" 2> "$OUT/environment.err" || FAILED=1

echo "== unit + adversarial tests =="
run_capture "$OUT/tests/unit.txt" env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_engine tests.test_runtime tests.test_judge_regressions tests.test_resource_lifetime -v

if [ -n "${CP_HERMES_TEMPLATE_HOME:-}" ] && [ -d "$CP_HERMES_TEMPLATE_HOME/installs" ]; then
  echo "== real Hermes integration =="
  run_capture "$OUT/install/integration.txt" env \
    CP_HERMES_TEMPLATE_HOME="$CP_HERMES_TEMPLATE_HOME" \
    CP_EVIDENCE_DIR="$PWD/../evidence" \
    python3 -m unittest tests.test_hermes_integration -v
else
  cat > "$OUT/install/BLOCKED.txt" <<'EOF'
BLOCKED: CP_HERMES_TEMPLATE_HOME was not provided, so the real Hermes install,
plugins doctor/validate, CLI and native pre_verify integration tests did not run.
Set CP_HERMES_TEMPLATE_HOME to a prepared throwaway HERMES_HOME with installs/.
EOF
  echo "integration evidence BLOCKED (no CP_HERMES_TEMPLATE_HOME)"
  FAILED=1
fi

echo "== deterministic benchmark =="
run_capture "$OUT/benchmark/benchmark-run.txt" \
  python3 scripts/benchmark.py --iterations 20 --seed 20261105 --files 40 --out-dir "$OUT/benchmark"
if [ -f "$OUT/benchmark/raw.json" ]; then
  run_capture "$OUT/benchmark/render.txt" python3 scripts/render_site.py \
    --raw "$OUT/benchmark/raw.json" --site site
fi

echo "== deterministic package =="
run_capture "$OUT/package/package.txt" python3 scripts/package.py --out-dir "$OUT/package"
ARCHIVE=$(ls "$OUT"/package/completion-proof-*.tar.gz 2>/dev/null | head -1 || true)
if [ -n "${ARCHIVE:-}" ]; then
  cp "$ARCHIVE" "dist/${ARCHIVE##*/}" 2>/dev/null || { mkdir -p dist; cp "$ARCHIVE" "dist/${ARCHIVE##*/}"; }
  if [ -n "${CP_HERMES_TEMPLATE_HOME:-}" ] && [ -d "$CP_HERMES_TEMPLATE_HOME/installs" ]; then
    run_capture "$OUT/package/fresh-install.txt" python3 scripts/verify_archive.py \
      --archive "$ARCHIVE" --template-home "$CP_HERMES_TEMPLATE_HOME" \
      --hook-test --out "$OUT/package/fresh-install.json"
  else
    echo "BLOCKED: archive fresh-install smoke skipped (no template home)" > "$OUT/package/fresh-install.BLOCKED.txt"
    FAILED=1
  fi
fi

echo "== local static site screenshots =="
if [ -n "${CP_CHROME_BIN:-}" ] || command -v chromium >/dev/null 2>&1 || command -v google-chrome >/dev/null 2>&1; then
  run_capture "$OUT/site/capture.txt" env CP_CHROME_BIN="${CP_CHROME_BIN:-}" python3 scripts/capture_site.py
else
  echo "BLOCKED: no Chromium/Chrome; set CP_CHROME_BIN" > "$OUT/site/BLOCKED.txt"
  FAILED=1
fi

echo "== actual CLI demo transcript and real MP4 replay =="
if [ -n "${CP_HERMES_TEMPLATE_HOME:-}" ] && [ -d "$CP_HERMES_TEMPLATE_HOME/installs" ]; then
  run_capture "$OUT/demo/session.txt" env CP_HERMES_TEMPLATE_HOME="$CP_HERMES_TEMPLATE_HOME" bash scripts/demo.sh
  run_capture "$OUT/demo/record.txt" env CP_HERMES_TEMPLATE_HOME="$CP_HERMES_TEMPLATE_HOME" \
    python3 scripts/record_demo.py --out-dir "$OUT/demo"
else
  echo "BLOCKED: demo skipped (no template home)" > "$OUT/demo/BLOCKED.txt"
  FAILED=1
fi

echo "== judge CP01-CP08 old-vs-new regressions =="
run_capture "$OUT/judge-regressions/run.txt" python3 scripts/run_judge_regressions.py \
  --old-ref 027c954 --out-dir "$OUT/judge-regressions"

echo "== CP06-R / CP07-A / CP07-B old 0.1.2 vs new =="
mkdir -p "$OUT/judge-regressions-013/cp07a"
TESTS=test_cp06r_actual_verify_does_not_pass_outside_root,test_cp06r_root_replacement_after_selection_is_unknown,test_cp06r_direct_hash_refuses_swapped_root,test_cp07a_fifo_after_selection_does_not_block,test_cp07a_pre_verify_with_injected_fifo_returns_promptly,test_cp07b_gate_does_not_wait_on_writer_plus_observation_lock
run_capture "$OUT/judge-regressions-013/run.txt" python3 scripts/run_judge_regressions.py \
  --old-ref a79577d --tests "$TESTS" --timeout 15 --out-dir "$OUT/judge-regressions-013"
run_capture "$OUT/judge-regressions-013/cp07a/run.txt" python3 scripts/run_judge_regressions.py \
  --old-ref a79577d --tests test_cp07a_fifo_after_selection_does_not_block,test_cp07a_pre_verify_with_injected_fifo_returns_promptly \
  --timeout 8 --out-dir "$OUT/judge-regressions-013/cp07a"

echo "== resource lifetime old 0.1.3 vs new =="
run_capture "$OUT/resource-lifetime/run.txt" python3 scripts/run_resource_lifetime.py \
  --old-ref 0c3c313 --out-dir "$OUT/resource-lifetime"

echo "== single-owner close old 0.1.4 vs new =="
run_capture "$OUT/owner-cleanup/run.txt" python3 scripts/run_owner_cleanup.py \
  --old-ref 155d582 --out-dir "$OUT/owner-cleanup"

echo "== sanitize local paths =="
python3 scripts/sanitize_evidence.py "$OUT" || FAILED=1

echo "== evidence manifest =="
python3 - "$OUT" <<'PY'
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
entries = []
for path in sorted(root.rglob("*")):
    if path.is_file() and path.name != "manifest.json":
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append({"path": path.relative_to(root).as_posix(), "sha256": digest, "bytes": path.stat().st_size})
manifest = {"schema": "completion-proof-evidence-v1", "files": entries}
(root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"manifest: {len(entries)} files")
PY

echo "== submission kit =="
run_capture "$PWD/../.scratch/submission-kit-build.txt" python3 scripts/build_submission_kit.py \
  --out "$PWD/../submission-kit"

echo "== sanitized public export =="
run_capture "$PWD/../.scratch/public-export-build.txt" python3 scripts/build_public_export.py

echo
echo "Evidence bundle complete under $OUT. failed=${FAILED}"
exit "$FAILED"
