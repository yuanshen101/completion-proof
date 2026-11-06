"""Best-effort redaction for captured command output.

This is defence in depth, not a guarantee. The plugin stores at most a bounded
excerpt (default 4 KiB per stream) and users can disable output storage entirely
with ``store_output: false``. A test command that prints a secret before the
plugin can redact it is still a user configuration problem; the plugin never
requests secrets and never reads credential stores.
"""

from __future__ import annotations

import re
from typing import Tuple

_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(\"(?:api[_-]?key|access[_-]?key|secret|token|password|passwd|authorization)\""
                r"\s*:\s*\")[^\"]+(\")"), r"\1[REDACTED]\2"),
    (re.compile(r"(?i)\b(api[_-]?key|access[_-]?key|secret|token|password|passwd|authorization)\b"
                r"(\s*[:=]\s*)([^\s,;\"']{4,})"), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)\b(api[_-]?key|access[_-]?key|secret|token|password|passwd|authorization)\b"
                r"(\s*[:=]\s*['\"])[^'\"]+(['\"])"), r"\1\2[REDACTED]\3"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                re.DOTALL), "[REDACTED_PRIVATE_KEY]"),
)


def redact_text(text: str) -> str:
    if not text:
        return ""
    out = text
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def cap_text(text: str, limit: int) -> Tuple[str, bool]:
    """Return (bounded text, truncated). Keeps the head and marks the cut."""
    if limit <= 0:
        return "", bool(text)
    encoded = text.encode("utf-8", "replace")
    if len(encoded) <= limit:
        return text, False
    head = encoded[:limit].decode("utf-8", "replace")
    return head + "\n…[output truncated by Completion Proof]…", True
