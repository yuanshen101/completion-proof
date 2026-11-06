"""Data shapes used by the deterministic proof engine.

Everything in this module is standard-library only and free of Hermes imports so
the engine can be unit-tested outside a Hermes process.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .version import ENGINE_VERSION, SCHEMA_VERSION

STATUSES = ("PASS", "FAIL", "UNKNOWN", "STALE")
EVIDENCE_SOURCES = ("tool", "cli", "slash", "hook")

# Phrases used both in reports and in the pre_verify nudge. They are deliberately
# explicit about the limits of a PASS receipt.
WHAT_PASS_PROVES = (
    "The configured command exited 0 before its timeout, and every file in the "
    "check's declared source, test and artifact scope had the recorded SHA-256 "
    "when the check was run."
)
WHAT_PASS_DOES_NOT_PROVE = (
    "A PASS does not prove that all software is correct, that every test was run, "
    "or that untested code works. It proves only that this configured command "
    "exited 0 on this declared scope. Evidence is local and not cryptographically "
    "tamper-proof: a process with the same OS permissions can edit the ledger."
)


class ProofError(Exception):
    """Base class for expected, reportable failures."""


class ConfigError(ProofError):
    """The trusted user configuration is malformed."""

    def __init__(self, errors: Sequence[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors) or "invalid configuration")


class UnknownError(ProofError):
    """A check could not establish truth (timeout, escape, IO, ...)."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


class ScopeError(UnknownError):
    """A declared path or glob escapes the configured workspace."""


class StoreCorruptError(ProofError):
    """The evidence database exists but cannot be read."""


class StoreBusyError(ProofError):
    """A budget-bounded read could not get a consistent snapshot in time."""


@dataclass(frozen=True)
class CheckConfig:
    """One user-trusted check definition.

    The model never supplies argv, cwd or environment values: these fields can
    only come from the operator's ``config.yaml`` (or the plugin CLI's fixed
    template). Tool calls may only name a configured check id.
    """

    id: str
    argv: Tuple[str, ...]
    description: str = ""
    cwd: str = "."
    timeout_seconds: int = 120
    source_globs: Tuple[str, ...] = ()
    test_globs: Tuple[str, ...] = ()
    artifact_globs: Tuple[str, ...] = ()
    exclude_globs: Tuple[str, ...] = ()
    workspace: str = ""
    proves: str = ""
    enabled: bool = True
    allow_missing_patterns: bool = False

    @property
    def all_globs(self) -> Tuple[str, ...]:
        return tuple(self.source_globs) + tuple(self.test_globs) + tuple(self.artifact_globs)

    def digest(self) -> str:
        """Stable digest of every field that changes what a receipt means.

        ``description`` and ``proves`` are documentation: changing them must not
        invalidate previously valid evidence.
        """
        payload = {
            "id": self.id,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "timeout_seconds": self.timeout_seconds,
            "source_globs": list(self.source_globs),
            "test_globs": list(self.test_globs),
            "artifact_globs": list(self.artifact_globs),
            "exclude_globs": list(self.exclude_globs),
            "workspace": self.workspace,
            "enabled": self.enabled,
            "allow_missing_patterns": self.allow_missing_patterns,
        }
        return sha256_json(payload)

    def display_command(self) -> str:
        import shlex

        return shlex.join(self.argv)

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "timeout_seconds": self.timeout_seconds,
            "source_globs": list(self.source_globs),
            "test_globs": list(self.test_globs),
            "artifact_globs": list(self.artifact_globs),
            "exclude_globs": list(self.exclude_globs),
            "workspace": self.workspace,
            "proves": self.proves,
            "enabled": self.enabled,
            "allow_missing_patterns": self.allow_missing_patterns,
            "check_digest": self.digest(),
        }


@dataclass(frozen=True)
class Settings:
    """Validated plugin settings, resolved once from trusted user config."""

    checks: Tuple[CheckConfig, ...] = ()
    base_dir: str = "."
    store_output: bool = True
    max_output_bytes: int = 4096
    max_hash_bytes: int = 256 * 1024 * 1024
    max_hash_files: int = 5000
    nudge_enabled: bool = True
    nudge_max_attempts: int = 1
    gate_deadline_ms: int = 250
    dedupe_window_seconds: int = 600
    retention_days: int = 30
    max_receipts_per_check: int = 200
    redact_output: bool = True

    def enabled_checks(self) -> Tuple[CheckConfig, ...]:
        return tuple(c for c in self.checks if c.enabled)

    def check_by_id(self, check_id: str) -> Optional[CheckConfig]:
        for check in self.checks:
            if check.id == check_id:
                return check
        return None

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "checks": [c.to_public_dict() for c in self.checks],
            "base_dir": self.base_dir,
            "store_output": self.store_output,
            "max_output_bytes": self.max_output_bytes,
            "max_hash_bytes": self.max_hash_bytes,
            "max_hash_files": self.max_hash_files,
            "nudge_enabled": self.nudge_enabled,
            "nudge_max_attempts": self.nudge_max_attempts,
            "gate_deadline_ms": self.gate_deadline_ms,
            "dedupe_window_seconds": self.dedupe_window_seconds,
            "retention_days": self.retention_days,
            "max_receipts_per_check": self.max_receipts_per_check,
            "redact_output": self.redact_output,
        }


@dataclass(frozen=True)
class FileRecord:
    path: str
    sha256: str
    size: int
    group: str

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "size": self.size, "group": self.group}


@dataclass
class Fingerprint:
    digest: str
    files: List[FileRecord] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    file_count: int = 0
    total_bytes: int = 0
    elapsed_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "digest": self.digest,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "elapsed_ms": self.elapsed_ms,
            "missing": list(self.missing),
            "files": [f.to_dict() for f in self.files],
        }


@dataclass
class Receipt:
    receipt_id: str
    verification_id: str
    check_id: str
    created_at: str
    status: str
    check_digest: str
    input_fingerprint: str
    input_file_count: int
    duration_ms: int
    exit_code: Optional[int] = None
    timed_out: bool = False
    reason: str = ""
    proves: str = ""
    scope_summary: str = ""
    stdout_sha256: str = ""
    stderr_sha256: str = ""
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""
    output_truncated: bool = False
    files: List[Dict[str, Any]] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    source_globs: List[str] = field(default_factory=list)
    test_globs: List[str] = field(default_factory=list)
    artifact_globs: List[str] = field(default_factory=list)
    exclude_globs: List[str] = field(default_factory=list)
    run_key: str = ""
    duplicate_of: str = ""
    engine_version: str = ENGINE_VERSION
    schema_version: int = SCHEMA_VERSION
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "verification_id": self.verification_id,
            "check_id": self.check_id,
            "created_at": self.created_at,
            "status": self.status,
            "check_digest": self.check_digest,
            "input_fingerprint": self.input_fingerprint,
            "input_file_count": self.input_file_count,
            "duration_ms": self.duration_ms,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "reason": self.reason,
            "proves": self.proves,
            "scope_summary": self.scope_summary,
            "stdout_sha256": self.stdout_sha256,
            "stderr_sha256": self.stderr_sha256,
            "stdout_excerpt": self.stdout_excerpt,
            "stderr_excerpt": self.stderr_excerpt,
            "output_truncated": self.output_truncated,
            "files": list(self.files),
            "missing": list(self.missing),
            "source_globs": list(self.source_globs),
            "test_globs": list(self.test_globs),
            "artifact_globs": list(self.artifact_globs),
            "exclude_globs": list(self.exclude_globs),
            "duplicate_of": self.duplicate_of,
            "engine_version": self.engine_version,
            "schema_version": self.schema_version,
            "metadata": dict(self.metadata),
        }


def sha256_json(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def normalize_check_ids(check_ids: Optional[Iterable[str]]) -> Optional[List[str]]:
    if check_ids is None:
        return None
    if isinstance(check_ids, str):
        check_ids = [check_ids]
    result: List[str] = []
    for item in check_ids:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result
