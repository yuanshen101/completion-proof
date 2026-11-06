"""SQLite-backed evidence ledger.

Opened through the public ``plugins.plugin_storage.plugin_db`` API in Hermes so
the database lives under ``<HERMES_HOME>/plugin-data/completion-proof/data.db``
and follows the active profile. The unit tests construct the same store on a
temporary path.

Design notes
------------
* Receipts are immutable rows; freshness is derived by comparing the receipt's
  input fingerprint with the current one, never by mutating history.
* A corrupt database is surfaced as ``StoreCorruptError``; the runtime refuses to
  fabricate evidence rather than silently starting a new ledger.
* Writes use a short busy timeout plus retries so a dashboard reader or a second
  Hermes process cannot turn a verification into a crash.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .models import Receipt, StoreBusyError, StoreCorruptError, utc_now_iso
from .version import SCHEMA_VERSION

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verifications (
    verification_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    source TEXT NOT NULL,
    summary_status TEXT,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts (
    receipt_id TEXT PRIMARY KEY,
    verification_id TEXT NOT NULL,
    check_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    check_digest TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    run_key TEXT NOT NULL,
    duplicate_of TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_receipts_check_created
    ON receipts(check_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_receipts_run_key
    ON receipts(run_key);
CREATE TABLE IF NOT EXISTS observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT '',
    command_sha256 TEXT NOT NULL DEFAULT '',
    result_sha256 TEXT NOT NULL DEFAULT '',
    exit_code INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
"""


class Store:
    def __init__(self, connection: sqlite3.Connection):
        self._conn = connection
        self._lock = threading.RLock()
        self._read_lock = threading.Lock()
        self._read_conn: Optional[sqlite3.Connection] = None
        self._closed = False
        with self._lock:
            try:
                # Hooks must stay responsive even under a competing writer; the
                # retry loop still tolerates transient locks, but a single wait is
                # bounded to half a second rather than five seconds.
                self._conn.execute("PRAGMA busy_timeout = 500")
                try:
                    self._conn.execute("PRAGMA journal_mode = WAL")
                except sqlite3.DatabaseError:
                    pass
                self._conn.executescript(_SCHEMA)
                current = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
                if current > SCHEMA_VERSION:
                    raise StoreCorruptError(
                        f"evidence database schema {current} is newer than this plugin "
                        f"({SCHEMA_VERSION}); refusing to downgrade"
                    )
                if current < SCHEMA_VERSION:
                    self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self._conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
                self._conn.commit()
                rows = self._conn.execute("PRAGMA database_list").fetchall()
                db_path = str(rows[0][2]) if rows else ""
                if db_path and db_path != ":memory:":
                    # A separate read-only connection lets a lightweight hook
                    # read status even while the observation worker holds the
                    # write lock. It never waits on a busy writer: a snapshot
                    # that is not immediately available is reported as busy.
                    self._read_conn = sqlite3.connect(
                        db_path, timeout=0.01, check_same_thread=False
                    )
                    self._read_conn.execute("PRAGMA busy_timeout = 0")
                    self._read_conn.execute("PRAGMA query_only = ON")
            except sqlite3.DatabaseError as exc:
                raise StoreCorruptError(f"cannot open evidence database: {exc}") from exc

    @classmethod
    def open_path(cls, path: Path) -> "Store":
        try:
            conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
        except sqlite3.DatabaseError as exc:
            raise StoreCorruptError(f"cannot open evidence database: {exc}") from exc
        return cls(conn)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                if self._read_conn is not None:
                    try:
                        self._read_conn.close()
                    except sqlite3.Error:
                        pass
                    self._read_conn = None
                self._conn.close()

    def _retry(self, fn, attempts: int = 5):
        delay = 0.01
        last: Optional[BaseException] = None
        for _ in range(attempts):
            try:
                with self._lock:
                    result = fn()
                    self._conn.commit()
                    return result
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                last = exc
                time.sleep(delay)
                delay = min(0.4, delay * 2)
            except sqlite3.DatabaseError as exc:
                raise StoreCorruptError(f"evidence database error: {exc}") from exc
        raise StoreCorruptError(f"evidence database stayed locked: {last}")

    # -- receipts ---------------------------------------------------------

    def insert_receipt(self, receipt: Receipt) -> None:
        payload = json.dumps(receipt.to_public_dict(), sort_keys=True)
        self._retry(
            lambda: self._conn.execute(
                "INSERT INTO receipts(receipt_id, verification_id, check_id, created_at, status,"
                " check_digest, input_fingerprint, run_key, duplicate_of, payload_json)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    receipt.receipt_id,
                    receipt.verification_id,
                    receipt.check_id,
                    receipt.created_at,
                    receipt.status,
                    receipt.check_digest,
                    receipt.input_fingerprint,
                    receipt.run_key,
                    receipt.duplicate_of,
                    payload,
                ),
            )
        )

    def _row_to_receipt(self, row: Tuple[Any, ...]) -> Dict[str, Any]:
        try:
            data = json.loads(row[0])
        except (TypeError, ValueError) as exc:
            raise StoreCorruptError(f"receipt payload is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise StoreCorruptError("receipt payload is not an object")
        return data

    def latest_receipt(self, check_id: str) -> Optional[Dict[str, Any]]:
        sql = (
            "SELECT payload_json FROM receipts WHERE check_id = ?"
            " ORDER BY created_at DESC, rowid DESC LIMIT 1"
        )
        if self._read_conn is not None:
            try:
                with self._read_lock:
                    row = self._read_conn.execute(sql, (check_id,)).fetchone()
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    raise StoreBusyError(
                        "ledger is busy; returning UNKNOWN within the hook budget"
                    ) from exc
                raise StoreCorruptError(f"evidence database error: {exc}") from exc
            return self._row_to_receipt(row) if row else None

        row = self._retry(lambda: self._conn.execute(sql, (check_id,)).fetchone())
        return self._row_to_receipt(row) if row else None

    def latest_receipts(self) -> Dict[str, Dict[str, Any]]:
        def query():
            return self._conn.execute(
                "SELECT r.check_id, r.payload_json FROM receipts r"
                " JOIN (SELECT check_id, MAX(rowid) AS newest_rowid FROM receipts GROUP BY check_id) m"
                " ON r.check_id = m.check_id AND r.rowid = m.newest_rowid"
            ).fetchall()

        rows = self._retry(query)
        result: Dict[str, Dict[str, Any]] = {}
        for check_id, payload in rows:
            try:
                data = json.loads(payload)
            except (TypeError, ValueError) as exc:
                raise StoreCorruptError(f"receipt payload is not valid JSON: {exc}") from exc
            if isinstance(data, dict):
                result[check_id] = data
        return result

    def receipts_for_check(self, check_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        def query():
            return self._conn.execute(
                "SELECT payload_json FROM receipts WHERE check_id = ?"
                " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (check_id, max(1, min(limit, 1000))),
            ).fetchall()

        return [self._row_to_receipt(row) for row in self._retry(query)]

    def find_recent_run_key(self, run_key: str, since_iso: str) -> Optional[Dict[str, Any]]:
        def query():
            return self._conn.execute(
                "SELECT payload_json FROM receipts WHERE run_key = ? AND created_at >= ?"
                " ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (run_key, since_iso),
            ).fetchone()

        row = self._retry(query)
        return self._row_to_receipt(row) if row else None

    # -- verifications ----------------------------------------------------

    def insert_verification(self, payload: Dict[str, Any]) -> None:
        self._retry(
            lambda: self._conn.execute(
                "INSERT INTO verifications(verification_id, started_at, finished_at, source,"
                " summary_status, payload_json) VALUES(?,?,?,?,?,?)",
                (
                    payload["verification_id"],
                    payload["started_at"],
                    payload.get("finished_at", ""),
                    payload.get("source", "tool"),
                    payload.get("summary_status", ""),
                    json.dumps(payload, sort_keys=True),
                ),
            )
        )

    def update_verification(self, payload: Dict[str, Any]) -> None:
        self._retry(
            lambda: self._conn.execute(
                "UPDATE verifications SET finished_at = ?, summary_status = ?, payload_json = ?"
                " WHERE verification_id = ?",
                (
                    payload.get("finished_at", ""),
                    payload.get("summary_status", ""),
                    json.dumps(payload, sort_keys=True),
                    payload["verification_id"],
                ),
            )
        )

    def verification(self, verification_id: str) -> Optional[Dict[str, Any]]:
        def query():
            return self._conn.execute(
                "SELECT payload_json FROM verifications WHERE verification_id = ?",
                (verification_id,),
            ).fetchone()

        row = self._retry(query)
        if not row:
            return None
        try:
            data = json.loads(row[0])
        except (TypeError, ValueError) as exc:
            raise StoreCorruptError(f"verification payload is not valid JSON: {exc}") from exc
        return data if isinstance(data, dict) else None

    # -- observed tool calls (untrusted context, never evidence) ----------

    def add_observation(
        self,
        *,
        tool_name: str,
        duration_ms: int = 0,
        status: str = "",
        command_sha256: str = "",
        result_sha256: str = "",
        exit_code: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._retry(
            lambda: self._conn.execute(
                "INSERT INTO observations(created_at, tool_name, duration_ms, status,"
                " command_sha256, result_sha256, exit_code, metadata_json)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (
                    utc_now_iso(),
                    str(tool_name)[:80],
                    int(duration_ms or 0),
                    str(status)[:40],
                    command_sha256,
                    result_sha256,
                    exit_code,
                    json.dumps(metadata or {}, sort_keys=True),
                ),
            )
        )

    def observations(self, limit: int = 20) -> List[Dict[str, Any]]:
        def query():
            return self._conn.execute(
                "SELECT created_at, tool_name, duration_ms, status, command_sha256,"
                " result_sha256, exit_code FROM observations ORDER BY observation_id DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()

        rows = self._retry(query)
        return [
            {
                "created_at": row[0],
                "tool_name": row[1],
                "duration_ms": row[2],
                "status": row[3],
                "command_sha256": row[4],
                "result_sha256": row[5],
                "exit_code": row[6],
                "trust": "UNTRUSTED_CONTEXT",
            }
            for row in rows
        ]

    # -- housekeeping -----------------------------------------------------

    def gc(self, *, retention_days: int, max_per_check: int) -> Dict[str, int]:
        cutoff = ""
        if retention_days > 0:
            from datetime import datetime, timedelta, timezone

            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=retention_days)
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z")

        def run():
            removed = 0
            if cutoff:
                cursor = self._conn.execute(
                    "DELETE FROM receipts WHERE created_at < ?", (cutoff,)
                )
                removed += cursor.rowcount or 0
                self._conn.execute(
                    "DELETE FROM verifications WHERE finished_at != '' AND finished_at < ?",
                    (cutoff,),
                )
                self._conn.execute(
                    "DELETE FROM observations WHERE created_at < ?", (cutoff,)
                )
            checks = [row[0] for row in self._conn.execute(
                "SELECT DISTINCT check_id FROM receipts").fetchall()]
            for check_id in checks:
                self._conn.execute(
                    "DELETE FROM receipts WHERE check_id = ? AND receipt_id NOT IN ("
                    " SELECT receipt_id FROM receipts WHERE check_id = ?"
                    " ORDER BY created_at DESC LIMIT ?)",
                    (check_id, check_id, max(1, max_per_check)),
                )
            return removed

        return {"receipts_removed_before_retention": self._retry(run)}

    def stats(self) -> Dict[str, Any]:
        def query():
            receipts = self._conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
            verifications = self._conn.execute("SELECT COUNT(*) FROM verifications").fetchone()[0]
            observations = self._conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            return int(receipts), int(verifications), int(observations)

        receipts, verifications, observations = self._retry(query)
        return {
            "receipts": receipts,
            "verifications": verifications,
            "observations": observations,
            "schema_version": SCHEMA_VERSION,
        }
