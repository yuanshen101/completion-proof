"""Validation of the operator-trusted Completion Proof configuration.

Security boundary: this module only ever sees the mapping returned by
``ctx.get_config()`` (the ``plugins.entries.completion-proof.settings`` subtree
of ``config.yaml``). Tool handlers receive a ``check_ids`` list and nothing else;
they cannot supply argv, cwd, environment, or glob patterns.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .models import CheckConfig, ConfigError, Settings

CHECK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DEFAULT_EXCLUDES = (
    ".git/**",
    ".venv/**",
    "venv/**",
    "node_modules/**",
    "__pycache__/**",
    ".pytest_cache/**",
    ".mypy_cache/**",
    ".ruff_cache/**",
    "dist/**",
    "build/**",
    ".tox/**",
)
SETTING_KEYS = {
    "workspace",
    "store_output",
    "max_output_bytes",
    "max_hash_bytes",
    "max_hash_files",
    "nudge_enabled",
    "nudge_max_attempts",
    "gate_deadline_ms",
    "dedupe_window_seconds",
    "retention_days",
    "max_receipts_per_check",
    "redact_output",
}


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _int(
    value: Any,
    name: str,
    errors: List[str],
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{name} must be an integer, got {type(value).__name__}")
        return default
    if value < minimum or value > maximum:
        errors.append(f"{name} must be between {minimum} and {maximum}, got {value}")
        return default
    return value


def _bool(value: Any, name: str, errors: List[str], *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        errors.append(f"{name} must be true or false, got {type(value).__name__}")
        return default
    return value


def _text(value: Any, name: str, errors: List[str], *, maximum: int = 500) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        errors.append(f"{name} must be a string, got {type(value).__name__}")
        return ""
    text = value.strip()
    if len(text) > maximum:
        errors.append(f"{name} is longer than {maximum} characters")
        return text[:maximum]
    return text


def _string_list(value: Any, name: str, errors: List[str], *, maximum_items: int = 64) -> Tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        errors.append(f"{name} must be a list of strings, got {type(value).__name__}")
        return ()
    if len(value) > maximum_items:
        errors.append(f"{name} must contain at most {maximum_items} items")
        value = value[:maximum_items]
    out: List[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            errors.append(f"{name}[{index}] must be a string, got {type(item).__name__}")
            continue
        text = item.strip()
        if not text:
            errors.append(f"{name}[{index}] must not be empty")
            continue
        out.append(text)
    return tuple(out)


def validate_glob(pattern: str, name: str, errors: List[str]) -> None:
    if "\x00" in pattern:
        errors.append(f"{name} contains a NUL byte")
        return
    if len(pattern) > 512:
        errors.append(f"{name} is longer than 512 characters")
        return
    if pattern.startswith(("/", "\\")):
        errors.append(f"{name} must be workspace-relative, got absolute path {pattern!r}")
        return
    # Both separators are rejected as traversal so a Windows config cannot escape
    # on a POSIX host and vice versa.
    parts = pattern.replace("\\", "/").split("/")
    if any(part == ".." for part in parts):
        errors.append(f"{name} must not contain '..': {pattern!r}")
    if pattern.strip() in ("", ".", ".."):
        errors.append(f"{name} must be a file glob, got {pattern!r}")


def validate_relative_cwd(value: str, name: str, errors: List[str]) -> None:
    if not value:
        errors.append(f"{name} must not be empty")
        return
    if "\x00" in value:
        errors.append(f"{name} contains a NUL byte")
        return
    if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", value):
        errors.append(f"{name} must be relative to the workspace, got {value!r}")
        return
    if any(part == ".." for part in value.replace("\\", "/").split("/")):
        errors.append(f"{name} must not contain '..': {value!r}")


def _parse_check(raw: Any, index: int, base_dir: Path, errors: List[str]) -> Optional[CheckConfig]:
    where = f"checks[{index}]"
    if not isinstance(raw, Mapping):
        errors.append(f"{where} must be a mapping, got {type(raw).__name__}")
        return None

    check_id = _text(raw.get("id"), f"{where}.id", errors, maximum=64)
    if not check_id:
        errors.append(f"{where}.id is required")
        return None
    if not CHECK_ID_RE.match(check_id):
        errors.append(
            f"{where}.id must match [A-Za-z0-9][A-Za-z0-9._-]{{0,63}}, got {check_id!r}"
        )
        return None

    argv_raw = raw.get("argv")
    argv: Tuple[str, ...] = ()
    if isinstance(argv_raw, str):
        errors.append(
            f"{where}.argv must be a list of strings; a single string would imply shell parsing"
        )
    elif not isinstance(argv_raw, (list, tuple)) or not argv_raw:
        errors.append(f"{where}.argv must be a non-empty list of strings")
    else:
        values: List[str] = []
        for arg_index, arg in enumerate(argv_raw):
            if not isinstance(arg, str):
                errors.append(f"{where}.argv[{arg_index}] must be a string")
                continue
            if not arg:
                errors.append(f"{where}.argv[{arg_index}] must not be empty")
                continue
            if "\x00" in arg:
                errors.append(f"{where}.argv[{arg_index}] contains a NUL byte")
                continue
            values.append(arg)
        if len(values) > 64:
            errors.append(f"{where}.argv must contain at most 64 arguments")
            values = values[:64]
        if values and len("\x00".join(values)) > 8192:
            errors.append(f"{where}.argv is longer than 8192 characters")
        argv = tuple(values)
    if not argv:
        return None

    timeout = _int(
        raw.get("timeout_seconds"),
        f"{where}.timeout_seconds",
        errors,
        default=120,
        minimum=1,
        maximum=600,
    )
    cwd = _text(raw.get("cwd"), f"{where}.cwd", errors, maximum=500) or "."
    validate_relative_cwd(cwd, f"{where}.cwd", errors)

    source_globs = _string_list(raw.get("source_globs"), f"{where}.source_globs", errors)
    test_globs = _string_list(raw.get("test_globs"), f"{where}.test_globs", errors)
    artifact_globs = _string_list(raw.get("artifact_globs"), f"{where}.artifact_globs", errors)
    for group_name, group in (
        ("source_globs", source_globs),
        ("test_globs", test_globs),
        ("artifact_globs", artifact_globs),
    ):
        for glob_index, pattern in enumerate(group):
            validate_glob(pattern, f"{where}.{group_name}[{glob_index}]", errors)

    exclude_globs = _string_list(
        raw.get("exclude_globs"), f"{where}.exclude_globs", errors, maximum_items=128
    )
    if not exclude_globs:
        exclude_globs = DEFAULT_EXCLUDES
    for glob_index, pattern in enumerate(exclude_globs):
        validate_glob(pattern, f"{where}.exclude_globs[{glob_index}]", errors)

    if not source_globs and not test_globs and not artifact_globs:
        errors.append(
            f"{where} must declare at least one of source_globs, test_globs or artifact_globs "
            "so a receipt has an explicit scope"
        )

    workspace = _text(raw.get("workspace"), f"{where}.workspace", errors, maximum=1000)
    description = _text(raw.get("description"), f"{where}.description", errors, maximum=500)
    proves = _text(raw.get("proves"), f"{where}.proves", errors, maximum=500)
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        errors.append(f"{where}.enabled must be true or false")
        enabled = True
    allow_missing = raw.get("allow_missing_patterns", False)
    if not isinstance(allow_missing, bool):
        errors.append(f"{where}.allow_missing_patterns must be true or false")
        allow_missing = False

    if raw.get("shell") not in (None, False):
        errors.append(f"{where}.shell is not supported; checks always run with shell=False")
    if raw.get("env") not in (None, {}):
        errors.append(
            f"{where}.env is not supported; checks inherit a filtered environment and must not "
            "embed secrets in configuration"
        )

    return CheckConfig(
        id=check_id,
        argv=argv,
        description=description,
        cwd=cwd,
        timeout_seconds=timeout,
        source_globs=source_globs,
        test_globs=test_globs,
        artifact_globs=artifact_globs,
        exclude_globs=exclude_globs,
        workspace=workspace,
        proves=proves,
        enabled=enabled,
        allow_missing_patterns=allow_missing,
    )


def settings_from_mapping(
    raw: Any,
    *,
    base_dir: Optional[Path] = None,
    warnings: Optional[List[str]] = None,
) -> Settings:
    """Validate a ``plugins.entries.completion-proof.settings`` mapping.

    Raises :class:`ConfigError` with every problem found instead of failing on
    the first one, so a user can fix their configuration in one pass.
    """
    errors: List[str] = []
    warnings = warnings if warnings is not None else []
    base = str((base_dir or Path.cwd()).resolve())
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ConfigError([f"settings must be a mapping, got {type(raw).__name__}"])

    for key in sorted(set(raw) - SETTING_KEYS - {"checks"}):
        warnings.append(f"unknown setting {key!r} is ignored")

    checks_raw = raw.get("checks", [])
    if checks_raw is None:
        checks_raw = []
    if not isinstance(checks_raw, (list, tuple)):
        errors.append(f"checks must be a list, got {type(checks_raw).__name__}")
        checks_raw = []
    if len(checks_raw) > 50:
        errors.append("checks must contain at most 50 entries")
        checks_raw = checks_raw[:50]

    checks: List[CheckConfig] = []
    seen: set[str] = set()
    for index, item in enumerate(checks_raw):
        parsed = _parse_check(item, index, Path(base), errors)
        if parsed is None:
            continue
        if parsed.id in seen:
            errors.append(f"duplicate check id {parsed.id!r}")
            continue
        seen.add(parsed.id)
        checks.append(parsed)

    workspace = _text(raw.get("workspace"), "workspace", errors, maximum=1000)
    if workspace:
        candidate = Path(workspace)
        if not candidate.is_absolute():
            candidate = Path(base) / candidate
        base = str(candidate.resolve())
    settings = Settings(
        checks=tuple(checks),
        base_dir=base,
        store_output=_bool(raw.get("store_output"), "store_output", errors, default=True),
        max_output_bytes=_int(
            raw.get("max_output_bytes"),
            "max_output_bytes",
            errors,
            default=4096,
            minimum=256,
            maximum=1_000_000,
        ),
        max_hash_bytes=_int(
            raw.get("max_hash_bytes"),
            "max_hash_bytes",
            errors,
            default=256 * 1024 * 1024,
            minimum=1024,
            maximum=2 * 1024 * 1024 * 1024,
        ),
        max_hash_files=_int(
            raw.get("max_hash_files"),
            "max_hash_files",
            errors,
            default=5000,
            minimum=1,
            maximum=100_000,
        ),
        nudge_enabled=_bool(raw.get("nudge_enabled"), "nudge_enabled", errors, default=True),
        nudge_max_attempts=_int(
            raw.get("nudge_max_attempts"),
            "nudge_max_attempts",
            errors,
            default=1,
            minimum=0,
            maximum=3,
        ),
        gate_deadline_ms=_int(
            raw.get("gate_deadline_ms"),
            "gate_deadline_ms",
            errors,
            default=250,
            minimum=25,
            maximum=5000,
        ),
        dedupe_window_seconds=_int(
            raw.get("dedupe_window_seconds"),
            "dedupe_window_seconds",
            errors,
            default=600,
            minimum=0,
            maximum=86400,
        ),
        retention_days=_int(
            raw.get("retention_days"),
            "retention_days",
            errors,
            default=30,
            minimum=0,
            maximum=3650,
        ),
        max_receipts_per_check=_int(
            raw.get("max_receipts_per_check"),
            "max_receipts_per_check",
            errors,
            default=200,
            minimum=1,
            maximum=100_000,
        ),
        redact_output=_bool(raw.get("redact_output"), "redact_output", errors, default=True),
    )
    if errors:
        raise ConfigError(errors)
    return settings


def template_checks(base_dir: Path) -> List[Dict[str, Any]]:
    """A conservative starting check for ``hermes completion-proof init``.

    The command is fixed by the plugin, not by the model or a free-form CLI
    argument. Users are expected to review and edit it in config.yaml.
    """
    base = Path(base_dir)
    if (base / "pyproject.toml").exists() or (base / "tests").is_dir():
        argv = ["python3", "-m", "pytest", "-q"]
    elif (base / "package.json").exists():
        argv = ["npm", "test", "--silent"]
    else:
        argv = ["python3", "-m", "unittest", "discover", "-v"]
    source_globs = [p for p in ("src/**/*.py", "lib/**/*.py", "app/**/*.py") if (base / p.split("/")[0]).is_dir()]
    if not source_globs:
        source_globs = ["*.py"]
    test_globs = ["tests/**/*.py"] if (base / "tests").is_dir() else []
    return [
        {
            "id": "tests",
            "description": "Project test suite (review this command before trusting it)",
            "argv": argv,
            "cwd": ".",
            "timeout_seconds": 120,
            "source_globs": source_globs,
            "test_globs": test_globs,
            "artifact_globs": [],
            "proves": "The configured test command exits 0 on the declared source/test scope.",
        }
    ]


def example_settings_yaml(base_dir: Path) -> str:
    """Render the init template as a copy-pasteable YAML snippet (no PyYAML needed)."""
    checks = template_checks(base_dir)
    lines = [
        "plugins:",
        "  enabled:",
        "    - completion-proof",
        "  entries:",
        "    completion-proof:",
        "      enabled: true",
        "      settings:",
        "        checks:",
    ]
    for check in checks:
        lines.append(f"          - id: {check['id']}")
        lines.append(f"            description: {check['description']}")
        lines.append("            argv:")
        for arg in check["argv"]:
            lines.append(f"              - {arg!r}")
        lines.append("            cwd: .")
        lines.append("            timeout_seconds: 120")
        lines.append("            source_globs:")
        for glob in check["source_globs"]:
            lines.append(f"              - {glob!r}")
        if check["test_globs"]:
            lines.append("            test_globs:")
            for glob in check["test_globs"]:
                lines.append(f"              - {glob!r}")
        lines.append("            artifact_globs: []")
    return "\n".join(lines) + "\n"
