"""Deterministic file fingerprinting for evidence receipts.

The scope is always explicit: the operator lists source/test/artifact globs in
trusted configuration. Nothing here accepts a path from a model tool call.
Symlinks that resolve outside the workspace are rejected rather than followed.
A file that changes while it is being hashed is reported as UNKNOWN, never
silently hashed from a mix of two revisions.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .models import CheckConfig, FileRecord, Fingerprint, ScopeError, UnknownError, sha256_json

_READ_CHUNK = 1024 * 1024
_HASH_RETRIES = 3

# Credential-shaped names are never read by the fingerprint, even if an operator
# glob accidentally includes them. The plugin must not become a credential
# reader; this is a name/path refusal, not a scan of the file contents.
_SENSITIVE_PARTS = {
    ".ssh", ".aws", ".config/gcloud", ".config/gh", ".docker", "keyrings",
    "credentials", "credential", "cookies", "keyring", "secrets",
}
_SENSITIVE_NAMES = {
    ".env", ".netrc", ".git-credentials", "auth.json", "credentials.json",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "known_hosts",
}


def sensitive_path(relative: str) -> bool:
    parts = [part.lower() for part in Path(relative).parts]
    joined = "/".join(parts)
    if any(part in _SENSITIVE_PARTS for part in parts):
        return True
    if any(joined.endswith(item) for item in _SENSITIVE_PARTS if "/" in item):
        return True
    name = parts[-1] if parts else ""
    if name in _SENSITIVE_NAMES or name.startswith(".env."):
        return True
    if name.endswith((".pem", ".key", ".p12", ".pfx")):
        return True
    return False


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a workspace-relative glob into a full-match regex.

    Supports ``**/`` (zero or more directories), ``**`` (anything, including
    separators), ``*`` (within one segment), ``?`` and character classes.
    """
    pattern = pattern.replace("\\", "/")
    out: List[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if index + 1 < length and pattern[index + 1] == "*":
                if index + 2 < length and pattern[index + 2] == "/":
                    out.append("(?:.*/)?")
                    index += 3
                    continue
                out.append(".*")
                index += 2
                continue
            out.append("[^/]*")
            index += 1
            continue
        if char == "?":
            out.append("[^/]")
            index += 1
            continue
        if char == "[":
            close = pattern.find("]", index + 1)
            if close == -1:
                out.append(re.escape(char))
                index += 1
                continue
            body = pattern[index + 1 : close]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            index = close + 1
            continue
        out.append(re.escape(char))
        index += 1
    return re.compile("^" + "".join(out) + "$")


def glob_match(relative_path: str, pattern: str) -> bool:
    return bool(_glob_to_regex(pattern).match(relative_path.replace("\\", "/")))


def is_excluded(relative_path: str, exclude_globs: Sequence[str]) -> bool:
    rel = relative_path.replace("\\", "/")
    return any(glob_match(rel, pattern) for pattern in exclude_globs)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass
class _Selection:
    files: List[Tuple[str, Path, str]]
    missing: List[str]



@dataclass
class RootHandle:
    """Pinned workspace root: fd plus device/inode identity."""

    path: Path
    fd: int
    device: int
    inode: int

    def verify(self) -> None:
        try:
            current = self.path.stat()
        except OSError as exc:
            raise UnknownError("workspace_replaced", f"{self.path}: {exc}") from exc
        if current.st_dev != self.device or current.st_ino != self.inode:
            raise UnknownError(
                "workspace_replaced",
                f"{self.path} was replaced after the root descriptor was pinned",
            )

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


def open_root_handle(root: Path) -> RootHandle:
    """Open every root ancestor with O_NOFOLLOW and pin device/inode."""
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise UnknownError(
            "secure_open_unsupported",
            "this platform cannot guarantee no-follow root traversal; refusing to hash",
        )
    # Do not resolve the path: resolving would follow a replaced symlink
    # before the no-follow walk. The engine passes an already canonical root,
    # and the caller's pinned handle protects the verify pipeline.
    absolute = Path(os.path.abspath(root))
    flags_dir = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open("/", flags_dir)
    try:
        for part in absolute.parts[1:]:
            next_fd = os.open(part, flags_dir, dir_fd=fd)
            os.close(fd)
            fd = next_fd
    except (OSError, ValueError) as exc:
        os.close(fd)
        raise UnknownError("secure_open_failed", f"{absolute}: {exc}") from exc
    stat_result = os.fstat(fd)
    return RootHandle(path=absolute, fd=fd, device=stat_result.st_dev, inode=stat_result.st_ino)


def _open_secure_at(root_fd: int, relative: str) -> int:
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise UnknownError(
            "secure_open_unsupported",
            "this platform cannot guarantee no-follow directory traversal; refusing to hash",
        )
    parts = Path(relative).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise UnknownError("path_escape", f"invalid relative path {relative!r}")
    flags_dir = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    # O_NONBLOCK prevents a selected regular file replaced by a FIFO from
    # blocking before fstat rejects the non-regular type.
    flags_file = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | os.O_NONBLOCK
        | getattr(os, "O_CLOEXEC", 0)
    )
    dir_fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, flags_dir, dir_fd=dir_fd)
            os.close(dir_fd)
            dir_fd = next_fd
        file_fd = os.open(parts[-1], flags_file, dir_fd=dir_fd)
    except (OSError, ValueError) as exc:
        raise UnknownError("secure_open_failed", f"{relative}: {exc}") from exc
    finally:
        try:
            os.close(dir_fd)
        except OSError:
            pass
    stat_result = os.fstat(file_fd)
    if not stat.S_ISREG(stat_result.st_mode):
        os.close(file_fd)
        raise UnknownError("not_a_regular_file", relative)
    return file_fd


def secure_hash_file(
    root: Path,
    relative: str,
    *,
    root_fd: Optional[int] = None,
    retries: int = _HASH_RETRIES,
    max_bytes: Optional[int] = None,
    deadline: Optional[float] = None,
) -> Tuple[str, int]:
    """Hash via a pinned root FD with no-follow traversal for every component."""
    last_error = ""
    handle: Optional[RootHandle] = None
    pinned_fd = root_fd
    try:
        if pinned_fd is None:
            handle = open_root_handle(root)
            pinned_fd = handle.fd
        for _attempt in range(max(1, retries)):
            fd = _open_secure_at(pinned_fd, relative)
            try:
                before = os.fstat(fd)
                if max_bytes is not None and before.st_size > max_bytes:
                    raise UnknownError(
                        "hash_budget_exceeded",
                        f"{relative} is {before.st_size} bytes, above the remaining "
                        f"{max_bytes}-byte hash budget",
                    )
                digest = hashlib.sha256()
                total = 0
                while True:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise UnknownError("deadline_exceeded", f"while hashing {relative}")
                    chunk = os.read(fd, _READ_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if max_bytes is not None and total > max_bytes:
                        raise UnknownError(
                            "hash_budget_exceeded",
                            f"{relative} exceeded the remaining {max_bytes}-byte hash budget",
                        )
                    digest.update(chunk)
                after = os.fstat(fd)
                stable = (
                    before.st_size == after.st_size
                    and before.st_mtime_ns == after.st_mtime_ns
                    and before.st_ino == after.st_ino
                )
                if stable:
                    return digest.hexdigest(), after.st_size
                last_error = "size or mtime changed while hashing"
            finally:
                os.close(fd)
            time.sleep(0.005)
        raise UnknownError("file_changed_during_hash", last_error or relative)
    finally:
        if handle is not None:
            handle.close()


def select_files(
    root: Path,
    groups: Sequence[Tuple[str, Sequence[str]]],
    exclude_globs: Sequence[str],
    *,
    max_files: int,
    deadline: Optional[float] = None,
) -> _Selection:
    """Expand declared globs with one pruned walk per group.

    Excluded directories are pruned during the walk; deadline checks occur before
    sorting and during enumeration; sensitive names are refused before hashing.
    """
    root = root.resolve()
    seen: dict[str, Tuple[str, Path, str]] = {}
    missing: List[str] = []
    for group_name, globs in groups:
        if not globs:
            continue
        group_excludes = () if group_name == "artifact" else exclude_globs
        matched_patterns = {pattern: False for pattern in globs}
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            if deadline is not None and time.monotonic() >= deadline:
                raise UnknownError("deadline_exceeded", "while enumerating scope files")
            directory = Path(dirpath)
            relative_dir = directory.relative_to(root).as_posix()
            if relative_dir == ".":
                relative_dir = ""
            kept = []
            for name in dirnames:
                child_rel = f"{relative_dir}/{name}" if relative_dir else name
                # Prune excluded directories and never follow symlinked dirs.
                if is_excluded(child_rel + "/x", group_excludes):
                    continue
                if (directory / name).is_symlink():
                    continue
                kept.append(name)
            dirnames[:] = kept
            for filename in filenames:
                if deadline is not None and time.monotonic() >= deadline:
                    raise UnknownError("deadline_exceeded", "while enumerating scope files")
                relative = f"{relative_dir}/{filename}" if relative_dir else filename
                if sensitive_path(relative):
                    raise ScopeError(
                        "sensitive_path_in_scope",
                        f"credential-sensitive path refused by fingerprinting: {relative}",
                    )
                if is_excluded(relative, group_excludes):
                    continue
                matched_here = [pattern for pattern in globs if glob_match(relative, pattern)]
                if not matched_here:
                    continue
                candidate = root / relative
                if not candidate.is_file():
                    continue
                try:
                    real = candidate.resolve(strict=True)
                except OSError as exc:
                    raise UnknownError("file_missing", f"{candidate}: {exc}") from exc
                if not _is_within(real, root):
                    raise ScopeError(
                        "path_escape",
                        f"{candidate} resolves outside the configured workspace",
                    )
                for pattern in matched_here:
                    matched_patterns[pattern] = True
                if relative not in seen:
                    seen[relative] = (relative, real, group_name)
                if len(seen) > max_files:
                    raise UnknownError(
                        "scope_too_large",
                        f"more than {max_files} files matched; narrow the configured globs",
                    )
        for pattern, matched in matched_patterns.items():
            if not matched:
                missing.append(f"{group_name}:{pattern}")
    if deadline is not None and time.monotonic() >= deadline:
        raise UnknownError("deadline_exceeded", "before sorting the scope file list")
    return _Selection(files=sorted(seen.values(), key=lambda item: item[0]), missing=sorted(missing))


def hash_file(path: Path, *, retries: int = _HASH_RETRIES) -> Tuple[str, int]:
    """Hash a file, detecting replacement or in-place edits during the read."""
    last_error = ""
    for _attempt in range(max(1, retries)):
        try:
            before = path.stat()
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                while True:
                    chunk = handle.read(_READ_CHUNK)
                    if not chunk:
                        break
                    digest.update(chunk)
            after = path.stat()
        except FileNotFoundError as exc:
            raise UnknownError("file_missing", str(path)) from exc
        except PermissionError as exc:
            raise UnknownError("permission_denied", str(path)) from exc
        except OSError as exc:
            last_error = str(exc)
            continue
        stable = (
            before.st_size == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns
            and getattr(before, "st_ino", 0) == getattr(after, "st_ino", 0)
        )
        if stable:
            return digest.hexdigest(), after.st_size
        last_error = "size or mtime changed while hashing"
        time.sleep(0.005)
    raise UnknownError("file_changed_during_hash", last_error or str(path))


def compute_fingerprint(
    root: Path,
    check: CheckConfig,
    *,
    include_artifacts: bool = True,
    max_files: int = 5000,
    max_bytes: int = 256 * 1024 * 1024,
    deadline: Optional[float] = None,
    root_handle: Optional[RootHandle] = None,
) -> Fingerprint:
    started = time.monotonic()
    root = Path(root)
    handle = root_handle or open_root_handle(root)
    owns_handle = root_handle is None
    try:
        if not root.is_dir():
            raise UnknownError("workspace_missing", str(root))
        handle.verify()
        groups: List[Tuple[str, Sequence[str]]] = [
            ("source", check.source_globs),
            ("test", check.test_globs),
        ]
        if include_artifacts:
            groups.append(("artifact", check.artifact_globs))
        selection = select_files(
            root, groups, check.exclude_globs, max_files=max_files, deadline=deadline
        )
        handle.verify()
        records: List[FileRecord] = []
        total_bytes = 0
        for relative, _real, group_name in selection.files:
            if deadline is not None and time.monotonic() >= deadline:
                raise UnknownError("deadline_exceeded", "before hashing the next scope file")
            handle.verify()
            digest, size = secure_hash_file(
                root,
                relative,
                root_fd=handle.fd,
                max_bytes=max(0, max_bytes - total_bytes),
                deadline=deadline,
            )
            total_bytes += size
            if total_bytes > max_bytes:
                raise UnknownError(
                    "scope_too_large",
                    f"declared scope exceeds {max_bytes} bytes; narrow the configured globs",
                )
            records.append(FileRecord(path=relative, sha256=digest, size=size, group=group_name))
        payload = {
            "check_digest": check.digest(),
            "include_artifacts": include_artifacts,
            "workspace": str(Path(root).resolve()),
            "cwd": check.cwd,
            "effective_cwd": str((Path(root) / check.cwd).resolve()),
            "files": [record.to_dict() for record in records],
            "missing": selection.missing,
        }
        handle.verify()
        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        return Fingerprint(
            digest=sha256_json(payload),
            files=records,
            missing=selection.missing,
            file_count=len(records),
            total_bytes=total_bytes,
            elapsed_ms=elapsed_ms,
        )
    finally:
        if owns_handle:
            handle.close()


def scope_summary(check: CheckConfig, fingerprint: Fingerprint) -> str:
    parts = []
    if check.source_globs:
        parts.append(f"source={list(check.source_globs)}")
    if check.test_globs:
        parts.append(f"tests={list(check.test_globs)}")
    if check.artifact_globs:
        parts.append(f"artifacts={list(check.artifact_globs)}")
    return (
        f"{fingerprint.file_count} files ({fingerprint.total_bytes} bytes); "
        + "; ".join(parts)
    )
