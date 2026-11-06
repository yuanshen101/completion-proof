"""Hermes registration: tools, hooks, CLI and slash commands.

Only public plugin surfaces are used: ``ctx.register_tool``,
``ctx.register_hook``, ``ctx.register_cli_command``, ``ctx.register_command``,
``ctx.get_config``, ``ctx.set_config`` and the profile-scoped
``plugins.plugin_storage`` helpers.
"""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import schemas
from .config import example_settings_yaml, settings_from_mapping, template_checks
from .engine import ProofEngine
from .models import CheckConfig, ConfigError, ProofError, Settings
from .store import Store
from .version import __version__

logger = logging.getLogger(__name__)

PLUGIN_ID = "completion-proof"
TOOLSET = "completion-proof"


class Runtime:
    """Per-process plugin runtime built once at ``register(ctx)``."""

    def __init__(self, ctx: Any):
        self.ctx = ctx
        self.settings: Optional[Settings] = None
        self.engine: Optional[ProofEngine] = None
        self.store: Optional[Store] = None
        self.config_error: str = ""
        self.config_warnings: List[str] = []
        self._obs_queue: "queue.Queue[dict]" = queue.Queue(maxsize=64)
        self._obs_thread = threading.Thread(target=self._obs_worker, daemon=True)
        self._obs_thread.start()
        self._build()

    def _build(self) -> None:
        try:
            raw = {
                "workspace": self.ctx.get_config("workspace", default=""),
                "checks": self.ctx.get_config("checks", default=[]),
                "store_output": self.ctx.get_config("store_output", default=True),
                "max_output_bytes": self.ctx.get_config("max_output_bytes", default=4096),
                "max_hash_bytes": self.ctx.get_config("max_hash_bytes", default=256 * 1024 * 1024),
                "max_hash_files": self.ctx.get_config("max_hash_files", default=5000),
                "nudge_enabled": self.ctx.get_config("nudge_enabled", default=True),
                "nudge_max_attempts": self.ctx.get_config("nudge_max_attempts", default=1),
                "gate_deadline_ms": self.ctx.get_config("gate_deadline_ms", default=250),
                "dedupe_window_seconds": self.ctx.get_config("dedupe_window_seconds", default=600),
                "retention_days": self.ctx.get_config("retention_days", default=30),
                "max_receipts_per_check": self.ctx.get_config("max_receipts_per_check", default=200),
                "redact_output": self.ctx.get_config("redact_output", default=True),
            }
            self.settings = settings_from_mapping(
                raw, base_dir=Path.cwd(), warnings=self.config_warnings
            )
            from plugins.plugin_storage import plugin_db  # public Hermes API

            connection = plugin_db(PLUGIN_ID)
            self.store = Store(connection)
            self.engine = ProofEngine(self.settings, self.store)
        except ConfigError as exc:
            self.config_error = "invalid configuration: " + "; ".join(exc.errors)
            logger.warning("Completion Proof disabled: %s", self.config_error)
        except Exception as exc:  # noqa: BLE001 - a broken plugin must not break Hermes
            self.config_error = f"plugin storage/configuration unavailable: {type(exc).__name__}: {exc}"
            logger.warning("Completion Proof disabled: %s", self.config_error)

    # -- helper results ---------------------------------------------------

    def error_result(self, message: str = "") -> Dict[str, Any]:
        return {
            "ok": False,
            "error": message or self.config_error or "engine unavailable",
            "engine_version": __version__,
        }

    def tool_list_checks(self, args: Mapping[str, Any]) -> str:
        if not self.engine:
            return json.dumps(self.error_result())
        return json.dumps(
            {
                "ok": True,
                "checks": [check.to_public_dict() for check in self.engine.settings.enabled_checks()],
                "config_warnings": list(self.config_warnings),
            },
            sort_keys=True,
        )

    def tool_status(self, args: Mapping[str, Any]) -> str:
        if not self.engine:
            return json.dumps(self.error_result())
        try:
            state = self.engine.status(self._check_ids(args))
            state["ok"] = True
            return json.dumps(state, sort_keys=True)
        except ProofError as exc:
            return json.dumps(self.error_result(str(exc)))
        except Exception as exc:  # noqa: BLE001
            return json.dumps(self.error_result(f"{type(exc).__name__}: {exc}"))

    def tool_verify(self, args: Mapping[str, Any]) -> str:
        if not self.engine:
            return json.dumps(self.error_result())
        try:
            payload = self.engine.verify(self._check_ids(args), source="tool")
            payload["ok"] = payload.get("summary_status") == "PASS"
            return json.dumps(payload, sort_keys=True)
        except ProofError as exc:
            return json.dumps(self.error_result(str(exc)))
        except Exception as exc:  # noqa: BLE001
            return json.dumps(self.error_result(f"{type(exc).__name__}: {exc}"))

    def tool_report(self, args: Mapping[str, Any]) -> str:
        if not self.engine:
            return json.dumps(self.error_result())
        try:
            fmt = str(args.get("format") or "markdown").strip().lower()
            if fmt == "json":
                state = self.engine.status(self._check_ids(args))
                state["ok"] = True
                return json.dumps(state, sort_keys=True)
            return json.dumps(
                {
                    "ok": True,
                    "format": "markdown",
                    "report": self.engine.report(self._check_ids(args), format="markdown"),
                },
                sort_keys=True,
            )
        except ProofError as exc:
            return json.dumps(self.error_result(str(exc)))
        except Exception as exc:  # noqa: BLE001
            return json.dumps(self.error_result(f"{type(exc).__name__}: {exc}"))

    @staticmethod
    def _check_ids(args: Mapping[str, Any]) -> Optional[List[str]]:
        value = args.get("check_ids") if isinstance(args, Mapping) else None
        if value is None:
            return None
        if not isinstance(value, (list, tuple)):
            raise ConfigError(["check_ids must be an array of configured check ids"])
        if len(value) > 50:
            raise ConfigError(["check_ids must contain at most 50 entries"])
        return [str(item) for item in value]

    # -- hooks ------------------------------------------------------------

    def _obs_worker(self) -> None:
        while True:
            item = self._obs_queue.get()
            if item is None:
                self._obs_queue.task_done()
                return
            try:
                if self.engine:
                    self.engine.record_observation(**item)
            except Exception:  # noqa: BLE001 - observations are best effort
                pass
            finally:
                self._obs_queue.task_done()

    def close(self) -> None:
        """Stop the observation worker and close the ledger (tests/shutdown)."""
        try:
            self._obs_queue.put_nowait(None)
        except queue.Full:
            pass
        self._obs_thread.join(timeout=1.0)
        if self.store:
            self.store.close()

    def flush_observations(self, timeout: float = 2.0) -> bool:
        """Test/support helper: wait briefly for the observation queue to drain."""
        deadline = time.monotonic() + timeout
        while self._obs_queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return not self._obs_queue.unfinished_tasks

    def on_post_tool_call(
        self,
        tool_name: str = "",
        args: Optional[Mapping[str, Any]] = None,
        result: str = "",
        duration_ms: int = 0,
        status: str = "",
        **kwargs: Any,
    ) -> None:
        if not self.engine:
            return None
        item = {
            "tool_name": str(tool_name),
            "args": args or {},
            "result": result if isinstance(result, str) else str(result),
            "duration_ms": int(duration_ms or 0),
            "status": str(status or ""),
        }
        try:
            self._obs_queue.put_nowait(item)
        except queue.Full:
            # The hook must never block the agent loop; drop the oldest pending
            # observation rather than waiting on SQLite.
            try:
                self._obs_queue.get_nowait()
                self._obs_queue.task_done()
                self._obs_queue.put_nowait(item)
            except queue.Empty:
                pass
        return None

    def on_pre_verify(
        self,
        session_id: str = "",
        platform: str = "",
        model: str = "",
        coding: bool = False,
        attempt: int = 0,
        final_response: str = "",
        changed_paths: Optional[Sequence[str]] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, str]]:
        if not self.engine:
            return None
        try:
            return self.engine.pre_verify(
                coding=bool(coding),
                attempt=int(attempt or 0),
                changed_paths=list(changed_paths or []),
                final_response=str(final_response or ""),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Completion Proof pre_verify ignored an internal error: %s", exc)
            return None

    # -- slash command ----------------------------------------------------

    def slash(self, raw_args: str = "", **kwargs: Any) -> str:
        if not self.engine:
            return self.config_error or "Completion Proof is unavailable."
        parts = (raw_args or "").split()
        command = parts[0].lower() if parts else "status"
        check_ids = parts[1:] or None
        if command in ("status", "s"):
            try:
                return self.engine.report(check_ids, format="markdown")
            except ProofError as exc:
                return f"Completion Proof: {exc}"
        if command in ("checks", "list"):
            lines = ["| Check | Enabled | Command | Scope |", "|---|---|---|---|"]
            for check in self.engine.settings.checks:
                lines.append(
                    f"| {check.id} | {check.enabled} | `{check.display_command()}` | "
                    f"{check.source_globs or check.test_globs or check.artifact_globs} |"
                )
            return "\n".join(lines)
        if command in ("verify", "run"):
            try:
                payload = self.engine.verify(check_ids, source="slash")
            except ProofError as exc:
                return f"Completion Proof: {exc}"
            lines = [f"Summary: {payload['summary_status']}", ""]
            for result in payload["results"]:
                lines.append(
                    f"- {result['check_id']}: {result['status']} "
                    f"(exit={result['exit_code']}, {result['duration_ms']} ms)"
                    + (f" — {result['reason']}" if result.get("reason") else "")
                )
            lines.append("")
            lines.append("A PASS proves only the configured command exited 0 on the declared scope.")
            return "\n".join(lines)
        if command in ("report", "r"):
            return self.engine.report(check_ids, format="markdown")
        return "Usage: /proof [status|checks|verify [check-id...]|report [check-id...]]"

    # -- CLI --------------------------------------------------------------

    def cli(self, argv: Optional[Sequence[str]] = None) -> int:
        args = list(sys.argv[1:] if argv is None else argv)
        # ``hermes completion-proof ...`` argparse already handled by Hermes; this
        # path is used by tests that call the runtime directly.
        if not args or args[0] in ("status", "s"):
            return self._cli_status(args[1:])
        command = args[0]
        if command in ("verify", "run"):
            return self._cli_verify(args[1:])
        if command in ("checks", "list"):
            return self._cli_checks(args[1:])
        if command in ("report", "r"):
            return self._cli_report(args[1:])
        if command == "gc":
            return self._cli_gc(args[1:])
        if command == "init":
            return self._cli_init(args[1:])
        print(f"Unknown completion-proof command: {command}", file=sys.stderr)
        return 2

    def _cli_common(self, argv: List[str]) -> tuple[bool, List[str]]:
        as_json = False
        checks: List[str] = []
        index = 0
        while index < len(argv):
            item = argv[index]
            if item in ("--json", "-j"):
                as_json = True
            elif item in ("--check", "-c") and index + 1 < len(argv):
                checks.append(argv[index + 1])
                index += 1
            else:
                checks.append(item)
            index += 1
        return as_json, checks

    def _cli_status(self, argv: List[str]) -> int:
        as_json, checks = self._cli_common(argv)
        if not self.engine:
            print(self.config_error or "Completion Proof unavailable", file=sys.stderr)
            return 2
        try:
            state = self.engine.status(checks or None)
        except ProofError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if as_json:
            print(json.dumps(state, indent=2, sort_keys=True))
        else:
            print(self.engine.report(checks or None, format="markdown"))
        # status is diagnostic: it reports STALE/FAIL/UNKNOWN without failing the
        # shell. Use `verify` when a non-zero exit code is wanted for CI.
        return 0

    def _cli_verify(self, argv: List[str]) -> int:
        as_json, checks = self._cli_common(argv)
        if not self.engine:
            print(self.config_error or "Completion Proof unavailable", file=sys.stderr)
            return 2
        try:
            payload = self.engine.verify(checks or None, source="cli")
        except ProofError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if as_json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"Summary: {payload['summary_status']}")
            for result in payload["results"]:
                print(
                    f"  {result['check_id']}: {result['status']} "
                    f"(exit={result['exit_code']}, {result['duration_ms']} ms)"
                    + (f" — {result['reason']}" if result.get("reason") else "")
                )
            print("")
            print("PASS proves only that the configured command exited 0 on the declared scope.")
        return 0 if payload["summary_status"] == "PASS" else 1

    def _cli_checks(self, argv: List[str]) -> int:
        as_json = "--json" in argv or "-j" in argv
        if not self.engine:
            print(self.config_error or "Completion Proof unavailable", file=sys.stderr)
            return 2
        checks = [check.to_public_dict() for check in self.engine.settings.checks]
        if as_json:
            print(json.dumps({"checks": checks}, indent=2, sort_keys=True))
        else:
            for check in self.engine.settings.checks:
                print(f"{check.id}  {'enabled' if check.enabled else 'disabled'}")
                print(f"  command: {check.display_command()}")
                print(f"  cwd: {check.cwd}  timeout: {check.timeout_seconds}s")
                print(f"  source: {list(check.source_globs)}")
                print(f"  tests:  {list(check.test_globs)}")
                print(f"  artifacts: {list(check.artifact_globs)}")
        return 0

    def _cli_report(self, argv: List[str]) -> int:
        as_json, checks = self._cli_common(argv)
        if not self.engine:
            print(self.config_error or "Completion Proof unavailable", file=sys.stderr)
            return 2
        try:
            if as_json:
                print(self.engine.report(checks or None, format="json"))
            else:
                print(self.engine.report(checks or None, format="markdown"))
        except ProofError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        return 0

    def _cli_gc(self, argv: List[str]) -> int:
        as_json = "--json" in argv or "-j" in argv
        if not self.engine:
            print(self.config_error or "Completion Proof unavailable", file=sys.stderr)
            return 2
        try:
            result = self.engine.gc()
        except ProofError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True) if as_json else str(result))
        return 0

    def _cli_init(self, argv: List[str]) -> int:
        write = "--write" in argv
        print_only = "--print" in argv or not write
        base = Path.cwd()
        if print_only:
            print(example_settings_yaml(base))
        if not write:
            return 0
        if not self.engine:
            print(self.config_error or "Completion Proof unavailable", file=sys.stderr)
            return 2
        if self.engine.settings.checks:
            print(
                "Refusing to overwrite existing checks. Edit config.yaml or remove the "
                "checks entry first.",
                file=sys.stderr,
            )
            return 2
        self.ctx.set_config("checks", template_checks(base))
        print("Wrote a starter check to plugins.entries.completion-proof.settings.checks.")
        print("Review the command and globs in config.yaml before trusting it.")
        return 0


def register(ctx: Any) -> None:
    runtime = Runtime(ctx)
    ctx.register_tool(
        name="proof_list_checks",
        toolset=TOOLSET,
        schema=schemas.PROOF_LIST_CHECKS,
        handler=runtime.tool_list_checks,
        description="List configured Completion Proof checks",
        emoji="🧾",
    )
    ctx.register_tool(
        name="proof_status",
        toolset=TOOLSET,
        schema=schemas.PROOF_STATUS,
        handler=runtime.tool_status,
        description="Show evidence receipts and staleness",
        emoji="🧾",
    )
    ctx.register_tool(
        name="proof_verify",
        toolset=TOOLSET,
        schema=schemas.PROOF_VERIFY,
        handler=runtime.tool_verify,
        description="Run configured checks and record receipts",
        emoji="✅",
    )
    ctx.register_tool(
        name="proof_report",
        toolset=TOOLSET,
        schema=schemas.PROOF_REPORT,
        handler=runtime.tool_report,
        description="Render the local evidence report",
        emoji="📋",
    )
    ctx.register_hook("post_tool_call", runtime.on_post_tool_call)
    ctx.register_hook("pre_verify", runtime.on_pre_verify)

    def setup_cli(subparser: Any) -> None:
        subs = subparser.add_subparsers(dest="completion_proof_command")
        status = subs.add_parser("status", help="Show latest receipts and freshness")
        status.add_argument("--check", action="append", default=[])
        status.add_argument("--json", action="store_true")
        status.set_defaults(func=lambda args: runtime.cli(_status_args(args)))

        verify = subs.add_parser("verify", help="Run configured checks and record receipts")
        verify.add_argument("--check", action="append", default=[])
        verify.add_argument("--json", action="store_true")
        verify.set_defaults(func=lambda args: runtime.cli(_verify_args(args)))

        checks = subs.add_parser("checks", help="List configured checks")
        checks.add_argument("--json", action="store_true")
        checks.set_defaults(func=lambda args: runtime.cli(_checks_args(args)))

        report = subs.add_parser("report", help="Render the evidence report")
        report.add_argument("--check", action="append", default=[])
        report.add_argument("--json", action="store_true")
        report.set_defaults(func=lambda args: runtime.cli(_report_args(args)))

        gc = subs.add_parser("gc", help="Apply retention to the local ledger")
        gc.add_argument("--json", action="store_true")
        gc.set_defaults(func=lambda args: runtime.cli(_gc_args(args)))

        init = subs.add_parser("init", help="Print or write a starter check configuration")
        init.add_argument("--write", action="store_true")
        init.add_argument("--print", action="store_true")
        init.set_defaults(func=lambda args: runtime.cli(_init_args(args)))

    ctx.register_cli_command(
        name="completion-proof",
        help="Local evidence receipts for configured checks",
        setup_fn=setup_cli,
        description=(
            "Run user-configured checks, fingerprint their declared source/test/artifact scope, "
            "and report PASS/FAIL/UNKNOWN/STALE receipts. Never accepts arbitrary commands."
        ),
    )
    ctx.register_command(
        "proof",
        handler=runtime.slash,
        description="Completion Proof status, checks, verify, report",
        args_hint="[status|checks|verify|report]",
    )
    logger.info(
        "Completion Proof registered: %d check(s) configured%s",
        len(runtime.settings.checks) if runtime.settings else 0,
        " (configuration error: " + runtime.config_error + ")" if runtime.config_error else "",
    )


def _status_args(args: Any) -> List[str]:
    out: List[str] = []
    for check in getattr(args, "check", []) or []:
        out += ["--check", check]
    if getattr(args, "json", False):
        out.append("--json")
    return ["status"] + out


def _verify_args(args: Any) -> List[str]:
    out: List[str] = []
    for check in getattr(args, "check", []) or []:
        out += ["--check", check]
    if getattr(args, "json", False):
        out.append("--json")
    return ["verify"] + out


def _checks_args(args: Any) -> List[str]:
    return ["checks"] + (["--json"] if getattr(args, "json", False) else [])


def _report_args(args: Any) -> List[str]:
    out: List[str] = []
    for check in getattr(args, "check", []) or []:
        out += ["--check", check]
    if getattr(args, "json", False):
        out.append("--json")
    return ["report"] + out


def _gc_args(args: Any) -> List[str]:
    return ["gc"] + (["--json"] if getattr(args, "json", False) else [])


def _init_args(args: Any) -> List[str]:
    out = ["init"]
    if getattr(args, "write", False):
        out.append("--write")
    if getattr(args, "print", False):
        out.append("--print")
    return out
