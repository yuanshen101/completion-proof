"""Deterministic proof engine: run trusted checks, fingerprint scope, record receipts.

Safety invariants enforced here (and covered by adversarial tests):

* argv/cwd/env come only from validated operator configuration;
* ``shell=False`` and a fixed argument vector: no interpolation;
* every path is resolved and checked to stay inside the configured workspace;
* timeouts kill the whole child process group where the platform allows it;
* output storage is bounded and redacted; hashing only covers declared globs;
* a PASS is stale as soon as any declared source/test/artifact file changes;
* model tool handlers can only select configured check ids, never a command.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import selectors
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .config import DEFAULT_EXCLUDES
from .fingerprint import compute_fingerprint, glob_match, scope_summary
from .sandbox_env import sandbox_env
from .models import (
    CheckConfig,
    ConfigError,
    Fingerprint,
    Receipt,
    Settings,
    StoreBusyError,
    UnknownError,
    WHAT_PASS_DOES_NOT_PROVE,
    WHAT_PASS_PROVES,
    normalize_check_ids,
    sha256_bytes,
    sha256_json,
    utc_now_iso,
)
from .redact import cap_text, redact_text
from .store import Store
from .version import ENGINE_VERSION, SCHEMA_VERSION

OUTPUT_CAPTURE_BYTES = 64 * 1024
_SECRET_ENV_RE = None


def _secret_env_regex():
    global _SECRET_ENV_RE
    if _SECRET_ENV_RE is None:
        import re

        _SECRET_ENV_RE = re.compile(
            r"(?i)(^|_)(API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|AUTH|CREDENTIAL|COOKIE|"
            r"PRIVATE_?KEY|ACCESS_?KEY|SESSION_?KEY)(_|$)"
        )
    return _SECRET_ENV_RE


def filtered_environment() -> Dict[str, str]:
    """Build the minimal environment inherited by a configured check.

    This is an allowlist, not a denylist: credentials, proxy settings,
    runtime-injection variables (PYTHONPATH), profile switches and approval
    bypass switches are never forwarded. Only OS/runtime essentials from
    ``sandbox_env`` are included; configured commands that need another value
    must be adapted by the operator, not by adding a secret to config.
    """
    return sandbox_env()


def _relative_within(root: Path, path: Path) -> Optional[str]:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


class ProofEngine:
    def __init__(self, settings: Settings, store: Store, *, engine_version: str = ENGINE_VERSION):
        self.settings = settings
        self.store = store
        self.engine_version = engine_version

    # -- path resolution --------------------------------------------------

    def workspace_for(self, check: CheckConfig) -> Path:
        raw = check.workspace or self.settings.base_dir
        path = Path(raw)
        if not path.is_absolute():
            path = Path(self.settings.base_dir) / path
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise UnknownError("workspace_missing", f"{raw}: {exc}") from exc
        if not resolved.is_dir():
            raise UnknownError("workspace_missing", f"{raw} is not a directory")
        return resolved

    def cwd_for(self, check: CheckConfig, root: Path) -> Path:
        candidate = (root / check.cwd).resolve(strict=False)
        if _relative_within(root, candidate) is None:
            raise UnknownError("cwd_escape", f"cwd {check.cwd!r} resolves outside {root}")
        if not candidate.is_dir():
            raise UnknownError("cwd_missing", f"cwd {check.cwd!r} is not a directory")
        return candidate

    def _fingerprint(
        self,
        root: Path,
        check: CheckConfig,
        *,
        include_artifacts: bool,
        deadline: Optional[float] = None,
        root_handle=None,
    ) -> Fingerprint:
        return compute_fingerprint(
            root,
            check,
            include_artifacts=include_artifacts,
            max_files=self.settings.max_hash_files,
            max_bytes=self.settings.max_hash_bytes,
            deadline=deadline,
            root_handle=root_handle,
        )

    # -- process execution ------------------------------------------------

    def _run_process(self, argv: Sequence[str], cwd: Path, timeout_seconds: int) -> Dict[str, Any]:
        started = time.monotonic()
        started_at = utc_now_iso()
        creationflags = 0
        start_new_session = os.name == "posix"
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            )
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                env=filtered_environment(),
                start_new_session=start_new_session,
                creationflags=creationflags,
            )
        except (OSError, ValueError) as exc:
            return {
                "spawn_error": f"{type(exc).__name__}: {exc}",
                "timed_out": False,
                "exit_code": None,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "started_at": started_at,
                "stdout_sha256": "",
                "stderr_sha256": "",
                "stdout_bytes": b"",
                "stderr_bytes": b"",
                "stdout_total": 0,
                "stderr_total": 0,
                "pipe_incomplete": False,
                "pipe_drain_timed_out": False,
                "output_complete": True,
            }

        def result(
            progress: Dict[str, Dict[str, Any]],
            *,
            timed_out: bool,
            pipe_incomplete: bool,
            pipe_drain_timed_out: bool,
            exit_code: Optional[int],
        ) -> Dict[str, Any]:
            duration_ms = int((time.monotonic() - started) * 1000)
            stdout = progress["stdout"]
            stderr = progress["stderr"]
            return {
                "spawn_error": "",
                "timed_out": timed_out,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "started_at": started_at,
                "stdout_sha256": stdout["digest"].hexdigest(),
                "stderr_sha256": stderr["digest"].hexdigest(),
                "stdout_bytes": bytes(stdout["kept"]),
                "stderr_bytes": bytes(stderr["kept"]),
                "stdout_total": stdout["total"],
                "stderr_total": stderr["total"],
                "pipe_incomplete": pipe_incomplete,
                "pipe_drain_timed_out": pipe_drain_timed_out,
                "output_complete": not pipe_incomplete,
            }

        progress: Dict[str, Dict[str, Any]] = {
            "stdout": {"digest": hashlib.sha256(), "kept": bytearray(), "total": 0},
            "stderr": {"digest": hashlib.sha256(), "kept": bytearray(), "total": 0},
        }
        if os.name != "posix":
            return self._run_process_threads(
                process, progress, started, started_at, timeout_seconds
            )

        selector = selectors.DefaultSelector()
        streams = {"stdout": process.stdout, "stderr": process.stderr}
        try:
            for key, stream in streams.items():
                assert stream is not None
                fd = stream.fileno()
                os.set_blocking(fd, False)
                selector.register(fd, selectors.EVENT_READ, key)
            deadline = started + timeout_seconds
            timed_out = False
            pipe_incomplete = False
            pipe_drain_timed_out = False
            drain_deadline: Optional[float] = None
            while selector.get_map():
                now = time.monotonic()
                if not timed_out and now >= deadline:
                    timed_out = True
                    self._kill_process_group(process, pgid=process.pid)
                    drain_deadline = now + 1.0
                if drain_deadline is not None and now >= drain_deadline:
                    pipe_incomplete = True
                    pipe_drain_timed_out = True
                    break
                events = selector.select(0.05)
                if not events:
                    # The direct child may have exited while a descendant still
                    # holds the inherited pipes. Give them a short drain grace,
                    # then return bounded with incomplete output.
                    if process.poll() is not None and drain_deadline is None:
                        drain_deadline = now + 0.5
                    continue
                for key, _mask in events:
                    key_name = str(key.data)
                    state = progress[key_name]
                    fd = int(key.fd)
                    while True:
                        try:
                            chunk = os.read(fd, 65536)
                        except BlockingIOError:
                            break
                        except OSError:
                            chunk = b""
                        if not chunk:
                            try:
                                selector.unregister(fd)
                            except KeyError:
                                pass
                            break
                        state["total"] += len(chunk)
                        state["digest"].update(chunk)
                        if len(state["kept"]) < OUTPUT_CAPTURE_BYTES:
                            state["kept"].extend(
                                chunk[: OUTPUT_CAPTURE_BYTES - len(state["kept"])]
                            )
            exit_code = process.poll()
            if exit_code is None:
                self._kill_process_group(process, pgid=process.pid)
                try:
                    exit_code = process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    exit_code = None
        finally:
            try:
                selector.close()
            except Exception:
                pass
            for stream in streams.values():
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        return result(
            progress,
            timed_out=timed_out,
            pipe_incomplete=pipe_incomplete,
            pipe_drain_timed_out=pipe_drain_timed_out,
            exit_code=exit_code,
        )

    def _run_process_threads(
        self,
        process: "subprocess.Popen[bytes]",
        progress: Dict[str, Dict[str, Any]],
        started: float,
        started_at: str,
        timeout_seconds: int,
    ) -> Dict[str, Any]:
        """Fallback for platforms without pipe selectors (Windows; untested)."""
        captured: Dict[str, Tuple[str, bytes, int]] = {}

        def drain(stream, key: str) -> None:
            state = progress[key]
            try:
                while True:
                    chunk = stream.read(65536)
                    if not chunk:
                        break
                    state["total"] += len(chunk)
                    state["digest"].update(chunk)
                    if len(state["kept"]) < OUTPUT_CAPTURE_BYTES:
                        state["kept"].extend(
                            chunk[: OUTPUT_CAPTURE_BYTES - len(state["kept"])]
                        )
            except (OSError, ValueError):
                pass
            finally:
                captured[key] = (
                    state["digest"].hexdigest(),
                    bytes(state["kept"]),
                    state["total"],
                )

        threads = [
            threading.Thread(target=drain, args=(process.stdout, "stdout"), daemon=True),
            threading.Thread(target=drain, args=(process.stderr, "stderr"), daemon=True),
        ]
        for thread in threads:
            thread.start()
        timed_out = False
        pipe_incomplete = False
        pipe_drain_timed_out = False
        try:
            exit_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = None
            self._kill_process_group(process)
            try:
                exit_code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                exit_code = None
        for thread in threads:
            thread.join(timeout=2.0)
        if any(thread.is_alive() for thread in threads):
            pipe_incomplete = True
            pipe_drain_timed_out = True
            self._kill_process_group(process, pgid=process.pid)
            for thread in threads:
                thread.join(timeout=2.0)
        # Do not close a stream while one of its reader threads is still blocked.
        if not any(thread.is_alive() for thread in threads):
            for stream in (process.stdout, process.stderr):
                try:
                    stream.close()
                except OSError:
                    pass
        for key, state in progress.items():
            captured.setdefault(
                key, (state["digest"].hexdigest(), bytes(state["kept"]), state["total"])
            )
        return {
            "spawn_error": "",
            "timed_out": timed_out,
            "exit_code": exit_code,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "started_at": started_at,
            "stdout_sha256": captured["stdout"][0],
            "stderr_sha256": captured["stderr"][0],
            "stdout_bytes": captured["stdout"][1],
            "stderr_bytes": captured["stderr"][1],
            "stdout_total": captured["stdout"][2],
            "stderr_total": captured["stderr"][2],
            "pipe_incomplete": pipe_incomplete,
            "pipe_drain_timed_out": pipe_drain_timed_out,
            "output_complete": not pipe_incomplete,
        }

    @staticmethod
    def _kill_process_group(process: "subprocess.Popen[bytes]", pgid: int = 0) -> None:
        if os.name == "posix":
            # start_new_session makes the child a process-group leader. After the
            # parent exits, getpgid() can fail but the group id is still its pid.
            target = pgid or process.pid
            try:
                os.killpg(target, signal.SIGKILL)
                return
            except (ProcessLookupError, PermissionError, OSError):
                pass
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
                return
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass

    # -- check execution --------------------------------------------------

    def run_check(self, check: CheckConfig, verification_id: str, source: str) -> Dict[str, Any]:
        root: Optional[Path] = None
        cwd: Optional[Path] = None
        pre: Optional[Fingerprint] = None
        process: Optional[Dict[str, Any]] = None
        post: Optional[Fingerprint] = None
        root_handle = None
        reason = ""
        status = "UNKNOWN"

        try:
            root = self.workspace_for(check)
            cwd = self.cwd_for(check, root)
            from .fingerprint import open_root_handle

            root_handle = open_root_handle(root)
            try:
                pre = self._fingerprint(
                    root, check, include_artifacts=False, root_handle=root_handle
                )
            except Exception:
                root_handle.close()
                raise
            if not check.allow_missing_patterns:
                missing_required = [
                    marker
                    for marker in pre.missing
                    if marker.startswith("source:") or marker.startswith("test:")
                ]
                if missing_required:
                    raise UnknownError(
                        "scope_pattern_missing",
                        "declared source/test glob(s) matched no files: "
                        + ", ".join(missing_required),
                    )
            if (
                (check.source_globs or check.test_globs)
                and not pre.files
                and not check.allow_missing_patterns
            ):
                raise UnknownError(
                    "scope_empty",
                    "declared source/test globs matched no files; refusing to record a PASS",
                )
            process = self._run_process(check.argv, cwd, check.timeout_seconds)
            post = self._fingerprint(
                root, check, include_artifacts=True, root_handle=root_handle
            )
            if process["spawn_error"]:
                reason = "spawn_error: " + process["spawn_error"]
                status = "UNKNOWN"
            elif process["timed_out"]:
                reason = f"timeout after {check.timeout_seconds}s; child process group killed"
                status = "UNKNOWN"
            elif process["pipe_incomplete"]:
                reason = (
                    "output_incomplete: a child process held stdout/stderr after the "
                    "parent exited; the process group was killed and the output hash "
                    "is partial"
                )
                status = "UNKNOWN"
            elif self._scope_changed(pre, post):
                reason = "source or test file changed while the check was running"
                status = "UNKNOWN"
            else:
                missing_artifacts = [m for m in post.missing if m.startswith("artifact:")]
                if missing_artifacts:
                    reason = "declared artifact missing after check: " + ", ".join(missing_artifacts)
                    status = "UNKNOWN"
                elif process["exit_code"] == 0:
                    status = "PASS"
                    reason = ""
                else:
                    status = "FAIL"
                    reason = f"command exited {process['exit_code']}"
        except UnknownError as exc:
            reason = f"{exc.reason}: {exc.detail}" if exc.detail else exc.reason
            status = "UNKNOWN"
        except Exception as exc:  # noqa: BLE001 - evidence failure must not crash the agent
            reason = f"internal_error: {type(exc).__name__}: {exc}"
            status = "UNKNOWN"
        finally:
            if root_handle is not None:
                root_handle.close()
                root_handle = None

        duration_ms = int(process["duration_ms"]) if process else 0
        stdout_sha = process["stdout_sha256"] if process else ""
        stderr_sha = process["stderr_sha256"] if process else ""
        stdout_excerpt = ""
        stderr_excerpt = ""
        truncated = False
        if process and self.settings.store_output:
            stdout_text = process["stdout_bytes"].decode("utf-8", "replace")
            stderr_text = process["stderr_bytes"].decode("utf-8", "replace")
            if self.settings.redact_output:
                stdout_text = redact_text(stdout_text)
                stderr_text = redact_text(stderr_text)
            stdout_excerpt, cut_out = cap_text(stdout_text, self.settings.max_output_bytes)
            stderr_excerpt, cut_err = cap_text(stderr_text, self.settings.max_output_bytes)
            truncated = bool(
                cut_out
                or cut_err
                or process.get("stdout_total", 0) > len(process["stdout_bytes"])
                or process.get("stderr_total", 0) > len(process["stderr_bytes"])
            )

        fingerprint = post or pre
        fingerprint_digest = fingerprint.digest if fingerprint else ""
        files = [record.to_dict() for record in fingerprint.files] if fingerprint else []
        missing = list(fingerprint.missing) if fingerprint else []
        source_globs = list(check.source_globs)
        test_globs = list(check.test_globs)
        artifact_globs = list(check.artifact_globs)
        check_digest = check.digest()
        created_at = utc_now_iso()
        run_key = sha256_json(
            {
                "check_id": check.id,
                "check_digest": check_digest,
                "input_fingerprint": fingerprint_digest,
                "status": status,
                "exit_code": process["exit_code"] if process else None,
                "timed_out": bool(process and process["timed_out"]),
                "stdout_sha256": stdout_sha,
                "stderr_sha256": stderr_sha,
                "reason": reason,
            }
        )
        receipt = Receipt(
            receipt_id="r-" + uuid.uuid4().hex,
            verification_id=verification_id,
            check_id=check.id,
            created_at=created_at,
            status=status,
            check_digest=check_digest,
            input_fingerprint=fingerprint_digest,
            input_file_count=len(files),
            duration_ms=duration_ms,
            exit_code=process["exit_code"] if process else None,
            timed_out=bool(process and process["timed_out"]),
            reason=reason,
            proves=check.proves or WHAT_PASS_PROVES,
            scope_summary=(
                scope_summary(check, fingerprint)
                if fingerprint
                else "; ".join(
                    filter(
                        None,
                        [
                            f"source={source_globs}" if source_globs else "",
                            f"tests={test_globs}" if test_globs else "",
                            f"artifacts={artifact_globs}" if artifact_globs else "",
                        ],
                    )
                )
            ),
            stdout_sha256=stdout_sha,
            stderr_sha256=stderr_sha,
            stdout_excerpt=stdout_excerpt,
            stderr_excerpt=stderr_excerpt,
            output_truncated=truncated,
            files=files,
            missing=missing,
            source_globs=source_globs,
            test_globs=test_globs,
            artifact_globs=artifact_globs,
            exclude_globs=list(check.exclude_globs),
            run_key=run_key,
            engine_version=self.engine_version,
            schema_version=SCHEMA_VERSION,
            metadata={
                "workspace": str(root) if root else "",
                "cwd": check.cwd,
                "argv": list(check.argv),
                "source": source,
                "platform": sys.platform,
                "python": sys.version.split()[0],
                "stdout_total_bytes": process["stdout_total"] if process else 0,
                "stderr_total_bytes": process["stderr_total"] if process else 0,
                "output_complete": process["output_complete"] if process else False,
                "pipe_incomplete": process["pipe_incomplete"] if process else False,
                "pipe_drain_timed_out": process["pipe_drain_timed_out"] if process else False,
            },
        )

        duplicate_of = ""
        if self.settings.dedupe_window_seconds > 0:
            from datetime import datetime, timedelta, timezone

            since = (
                datetime.now(timezone.utc)
                - timedelta(seconds=self.settings.dedupe_window_seconds)
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            existing = self.store.find_recent_run_key(run_key, since)
            if existing:
                duplicate_of = str(existing.get("receipt_id", ""))
        receipt.duplicate_of = duplicate_of
        # Always append the execution event. Dedupe only annotates a receipt; it
        # must never hide a newer failure behind an older identical-looking run.
        self.store.insert_receipt(receipt)
        result = receipt.to_public_dict()
        result["duplicate_of"] = duplicate_of
        result["stored"] = True
        result["deduplicated"] = bool(duplicate_of)
        result["what_pass_proves"] = WHAT_PASS_PROVES
        result["what_pass_does_not_prove"] = WHAT_PASS_DOES_NOT_PROVE
        return result

    @staticmethod
    def _scope_changed(pre: Fingerprint, post: Fingerprint) -> bool:
        def source_test(fp: Fingerprint) -> List[Tuple[str, str]]:
            return sorted(
                (record.path, record.sha256)
                for record in fp.files
                if record.group in ("source", "test")
            )

        return source_test(pre) != source_test(post)

    # -- public operations ------------------------------------------------

    def select_checks(self, check_ids: Optional[Iterable[str]] = None) -> Tuple[CheckConfig, ...]:
        ids = normalize_check_ids(check_ids)
        if ids is None:
            return self.settings.enabled_checks()
        unknown = [check_id for check_id in ids if self.settings.check_by_id(check_id) is None]
        if unknown:
            raise ConfigError([f"unknown check id(s): {', '.join(unknown)}"])
        disabled = [
            check_id
            for check_id in ids
            if self.settings.check_by_id(check_id) is not None
            and not self.settings.check_by_id(check_id).enabled  # type: ignore[union-attr]
        ]
        if disabled:
            raise ConfigError(
                [f"check id(s) are disabled and may not be executed: {', '.join(disabled)}"]
            )
        selected = tuple(
            self.settings.check_by_id(check_id) for check_id in ids  # type: ignore[misc]
        )
        return tuple(check for check in selected if check is not None)

    def verify(
        self, check_ids: Optional[Iterable[str]] = None, *, source: str = "tool"
    ) -> Dict[str, Any]:
        checks = self.select_checks(check_ids)
        verification_id = "v-" + uuid.uuid4().hex
        payload: Dict[str, Any] = {
            "verification_id": verification_id,
            "started_at": utc_now_iso(),
            "finished_at": "",
            "source": source,
            "summary_status": "",
            "engine_version": self.engine_version,
            "schema_version": SCHEMA_VERSION,
            "results": [],
            "what_pass_proves": WHAT_PASS_PROVES,
            "what_pass_does_not_prove": WHAT_PASS_DOES_NOT_PROVE,
        }
        self.store.insert_verification(payload)
        statuses: List[str] = []
        for check in checks:
            result = self.run_check(check, verification_id, source)
            payload["results"].append(result)
            statuses.append(result["status"])
        if not statuses:
            summary = "UNKNOWN"
        elif "FAIL" in statuses:
            summary = "FAIL"
        elif "UNKNOWN" in statuses:
            summary = "UNKNOWN"
        else:
            summary = "PASS"
        payload["summary_status"] = summary
        payload["finished_at"] = utc_now_iso()
        payload["status_counts"] = {
            status: statuses.count(status) for status in ("PASS", "FAIL", "UNKNOWN")
        }
        payload["duplicate_count"] = sum(1 for r in payload["results"] if r.get("duplicate_of"))
        self.store.update_verification(payload)
        return payload

    def status(
        self,
        check_ids: Optional[Iterable[str]] = None,
        *,
        deadline_monotonic: Optional[float] = None,
        include_observations: bool = False,
    ) -> Dict[str, Any]:
        checks = self.select_checks(check_ids)
        rows: List[Dict[str, Any]] = []
        for check in checks:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                rows.append(
                    {
                        "check_id": check.id,
                        "status": "UNKNOWN",
                        "fresh": False,
                        "reason": "gate deadline exceeded before fingerprinting this check",
                        "receipt": None,
                    }
                )
                continue
            try:
                latest = self.store.latest_receipt(check.id)
            except StoreBusyError as exc:
                rows.append(
                    {
                        "check_id": check.id,
                        "status": "UNKNOWN",
                        "fresh": False,
                        "reason": str(exc),
                        "receipt": None,
                    }
                )
                continue
            row: Dict[str, Any] = {
                "check_id": check.id,
                "description": check.description,
                "command": check.display_command(),
                "status": "UNKNOWN",
                "fresh": False,
                "reason": "no receipt has been recorded for this check",
                "receipt": latest,
                "workspace": "",
            }
            try:
                row["workspace"] = str(self.workspace_for(check))
                row["effective_cwd"] = str(self.cwd_for(check, self.workspace_for(check)))
            except UnknownError:
                pass
            if latest:
                row["reason"] = ""
                row["verified_at"] = latest.get("created_at")
                row["exit_code"] = latest.get("exit_code")
                row["duration_ms"] = latest.get("duration_ms")
                row["input_fingerprint"] = latest.get("input_fingerprint")
                row["scope_summary"] = latest.get("scope_summary", "")
                try:
                    root = self.workspace_for(check)
                    current = self._fingerprint(
                        root,
                        check,
                        include_artifacts=True,
                        deadline=deadline_monotonic,
                    )
                    row["current_fingerprint"] = current.digest
                    row["current_file_count"] = current.file_count
                    if (
                        latest.get("input_fingerprint") != current.digest
                        or latest.get("check_digest") != check.digest()
                    ):
                        row["status"] = "STALE"
                        row["reason"] = (
                            "relevant source, test, artifact or check configuration changed "
                            "since this receipt"
                        )
                    else:
                        row["status"] = str(latest.get("status", "UNKNOWN"))
                        row["fresh"] = row["status"] == "PASS"
                        row["reason"] = str(latest.get("reason", ""))
                except UnknownError as exc:
                    row["status"] = "UNKNOWN"
                    row["reason"] = f"{exc.reason}: {exc.detail}" if exc.detail else exc.reason
            rows.append(row)
        result: Dict[str, Any] = {
            "engine_version": self.engine_version,
            "schema_version": SCHEMA_VERSION,
            "checks": rows,
            "what_pass_proves": WHAT_PASS_PROVES,
            "what_pass_does_not_prove": WHAT_PASS_DOES_NOT_PROVE,
        }
        if include_observations:
            result["observations_untrusted"] = self.store.observations()
        return result

    def relevant_checks(self, changed_paths: Sequence[str]) -> List[CheckConfig]:
        relevant: List[CheckConfig] = []
        for check in self.settings.enabled_checks():
            try:
                root = self.workspace_for(check)
            except UnknownError:
                continue
            patterns = (
                tuple(check.source_globs)
                + tuple(check.test_globs)
                + tuple(check.artifact_globs)
            )
            if not patterns:
                continue
            for raw in changed_paths:
                relative = self._changed_relative(root, raw)
                if relative is None:
                    continue
                if any(glob_match(relative, pattern) for pattern in patterns):
                    relevant.append(check)
                    break
        return relevant

    @staticmethod
    def _changed_relative(root: Path, raw: str) -> Optional[str]:
        if not raw:
            return None
        path = Path(raw)
        if path.is_absolute():
            try:
                return path.resolve(strict=False).relative_to(root).as_posix()
            except (OSError, ValueError):
                return None
        normalized = os.path.normpath(str(path)).replace("\\", "/")
        if normalized.startswith("../") or normalized == "..":
            return None
        return normalized

    def pre_verify(
        self,
        *,
        coding: bool,
        attempt: int,
        changed_paths: Sequence[str],
        final_response: str = "",
    ) -> Optional[Dict[str, str]]:
        if not coding or not self.settings.nudge_enabled:
            return None
        if self.settings.nudge_max_attempts <= 0:
            return None
        if attempt >= self.settings.nudge_max_attempts:
            return None
        relevant = self.relevant_checks(changed_paths)
        if not relevant:
            return None
        deadline = time.monotonic() + (self.settings.gate_deadline_ms / 1000.0)
        try:
            status = self.status(
                [check.id for check in relevant], deadline_monotonic=deadline
            )
        except Exception:
            return None
        stale = [row for row in status["checks"] if row.get("status") != "PASS"]
        if not stale:
            return None
        labels = []
        for row in stale:
            label = row["check_id"]
            if row.get("status") == "STALE":
                label += " (STALE)"
            elif row.get("status") == "FAIL":
                label += " (FAIL)"
            elif row.get("status") == "UNKNOWN":
                label += " (UNKNOWN)"
            labels.append(label)
        ids = [row["check_id"] for row in stale]
        json_hint = json.dumps({"check_ids": ids}, separators=(",", ":"))
        tool_call_hint = json.dumps(
            {"calls": [{"name": "proof_verify", "arguments": {"check_ids": ids}}]},
            separators=(",", ":"),
        )
        message = (
            "[Completion Proof] This coding turn edited files covered by configured check(s): "
            + ", ".join(labels)
            + ". No fresh PASS receipt matches the current source/test/artifact fingerprints, "
            "so completion is not evidence-backed yet. Run the proof_verify tool with "
            f"{json_hint}. If Hermes defers plugin tools, use tool_call with {tool_call_hint}; "
            f"otherwise run `hermes completion-proof verify --check {ids[0]}`. Then address any "
            "failure. If a check cannot run, say so explicitly; do not claim tests passed or "
            "invent output. This is a one-shot request, not a lock."
        )
        return {"action": "continue", "message": message}

    def record_observation(
        self,
        *,
        tool_name: str,
        args: Mapping[str, Any],
        result: str,
        duration_ms: int = 0,
        status: str = "",
    ) -> None:
        try:
            command = ""
            if isinstance(args, Mapping):
                raw_command = args.get("command")
                if isinstance(raw_command, str):
                    command = raw_command
                elif isinstance(raw_command, list):
                    command = shlex.join(str(part) for part in raw_command)
            result_text = result if isinstance(result, str) else json.dumps(result, sort_keys=True)
            exit_code = None
            try:
                parsed = json.loads(result_text)
                if isinstance(parsed, dict):
                    for key in ("exit_code", "returncode", "code"):
                        value = parsed.get(key)
                        if isinstance(value, int) and not isinstance(value, bool):
                            exit_code = value
                            break
            except (TypeError, ValueError):
                pass
            self.store.add_observation(
                tool_name=tool_name,
                duration_ms=duration_ms,
                status=status,
                command_sha256=sha256_bytes(command.encode("utf-8")) if command else "",
                result_sha256=sha256_bytes(result_text.encode("utf-8", "replace")),
                exit_code=exit_code,
                metadata={"args_keys": sorted(str(key) for key in args) if isinstance(args, Mapping) else []},
            )
        except Exception:
            # Observers must never disturb a tool call.
            return None

    def report(self, check_ids: Optional[Iterable[str]] = None, *, format: str = "markdown") -> str:
        state = self.status(check_ids)
        if format == "json":
            return json.dumps(state, indent=2, sort_keys=True)
        lines = [
            "# Completion Proof report",
            "",
            f"Engine {state['engine_version']} · schema {state['schema_version']}",
            "",
            "| Check | Status | Exit | Duration | Verified (UTC) | Command | Scope |",
            "|---|---|---|---|---|---|---|",
        ]
        for row in state["checks"]:
            lines.append(
                "| {id} | {status} | {exit} | {duration} | {verified} | `{command}` | {scope} |".format(
                    id=row["check_id"],
                    status=row.get("status", "UNKNOWN"),
                    exit=row.get("exit_code", ""),
                    duration=(f"{row.get('duration_ms')} ms" if row.get("duration_ms") is not None else ""),
                    verified=row.get("verified_at", "never"),
                    command=str(row.get("command", "")).replace("|", "\\|"),
                    scope=str(row.get("scope_summary", "")).replace("|", "\\|"),
                )
            )
            if row.get("reason"):
                lines.append(f"|  | ↳ {str(row['reason']).replace('|', '\\|')} |  |  |  |  |  |")
        lines += [
            "",
            "## What PASS proves",
            "",
            WHAT_PASS_PROVES,
            "",
            "## What PASS does not prove",
            "",
            WHAT_PASS_DOES_NOT_PROVE,
            "",
            "## Provenance",
            "",
            "Every receipt stores the command vector, working directory (relative), declared "
            "globs, full file list with SHA-256, exit code, timeout flag, duration, and output "
            "hashes. Old receipts are immutable; they become STALE when scoped files change.",
        ]
        return "\n".join(lines) + "\n"

    def raw_state(self, check_ids: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        state = self.status(check_ids, include_observations=True)
        state["settings"] = {
            "store_output": self.settings.store_output,
            "max_output_bytes": self.settings.max_output_bytes,
            "max_hash_bytes": self.settings.max_hash_bytes,
            "max_hash_files": self.settings.max_hash_files,
            "nudge_enabled": self.settings.nudge_enabled,
            "nudge_max_attempts": self.settings.nudge_max_attempts,
            "gate_deadline_ms": self.settings.gate_deadline_ms,
            "retention_days": self.settings.retention_days,
            "max_receipts_per_check": self.settings.max_receipts_per_check,
        }
        state["store"] = self.store.stats()
        return state

    def gc(self) -> Dict[str, int]:
        removed = self.store.gc(
            retention_days=self.settings.retention_days,
            max_per_check=self.settings.max_receipts_per_check,
        )
        removed["store"] = self.store.stats()  # type: ignore[assignment]
        return removed
