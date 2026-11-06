#!/usr/bin/env python3
"""Remove local absolute paths from committed text evidence.

The evidence bundle must not contain operator home paths, ticket checkout paths
or throwaway temp paths. Binary screenshots are untouched, and the deterministic
engine never stores absolute workspace paths in receipts (only the local plugin
DB may contain them; it is not committed).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parent
HOME = Path.home()

TEXT_SUFFIXES = {".txt", ".json", ".jsonl", ".csv", ".md", ".yaml", ".yml", ".svg", ".log", ".sh"}
TMP_RE = re.compile(r"/tmp/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")
HOME_RE = re.compile(r"/home/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SENSITIVE_KEYS = (
    "PASS", "PASSWORD", "SECRET", "TOKEN", "API_KEY", "CREDENTIAL", "AUTH",
    "DATABASE_URL", "GITHUB_PAT", "GOOGLE_APPLICATION_CREDENTIALS",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "TELEGRAM_CHAT_ID", "ALERT_EMAIL",
    "YU_GMAIL_PASS", "YU_GMAIL", "MEM0_USER_ID", "DSH_SESSION_ID",
    "HERMES_YOLO_MODE", "HERMES_NONINTERACTIVE", "PYTHONPATH",
)
_SECRET_QUOTED_RE = re.compile(
    r"(?i)(['\"])([A-Za-z0-9_]*(?:_(?:PASS|PASSWORD|SECRET|TOKEN|KEY|CREDENTIAL|AUTH))|"
    r"password|passwd|secret|token|api[_-]?key|authorization)(['\"]\s*:\s*['\"])([^'\"]+)(['\"])"
)
_SECRET_ENV_RE = re.compile(
    r"\b(" + "|".join(sorted(set(SENSITIVE_KEYS), key=len, reverse=True)) + r")\b\s*=\s*[^\s,;'\"]+"
)


def sanitize(text: str) -> str:
    text = text.replace(str(PLUGIN_ROOT), "<PLUGIN_ROOT>")
    text = text.replace(str(REPO_ROOT), "<REPO_ROOT>")
    text = text.replace(str(HOME), "<HOME>")
    text = TMP_RE.sub("<TMP>", text)
    text = HOME_RE.sub("<HOME_PATH>", text)
    text = EMAIL_RE.sub("<EMAIL>", text)
    text = _SECRET_QUOTED_RE.sub(r"\1\2\3<REDACTED>\5", text)
    text = _SECRET_ENV_RE.sub(lambda match: match.group(1) + "=<REDACTED>", text)
    return text


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else REPO_ROOT / "evidence"
    changed = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        updated = sanitize(original)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed += 1
    print(f"sanitized {changed} text evidence file(s) under {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
