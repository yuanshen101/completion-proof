#!/usr/bin/env bash
# Fast local checks: unit + adversarial tests, plus the opt-in real-Hermes suite
# when CP_HERMES_TEMPLATE_HOME is provided.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1
python3 -m unittest discover -s tests -v
if [ -n "${CP_HERMES_TEMPLATE_HOME:-}" ]; then
  python3 -m unittest tests.test_hermes_integration -v
else
  echo "Skipping tests.test_hermes_integration (set CP_HERMES_TEMPLATE_HOME)."
fi
