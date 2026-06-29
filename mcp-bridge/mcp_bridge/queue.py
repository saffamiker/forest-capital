"""mcp_bridge.queue — SQLite-backed prompt queue.

The bridge's source of truth. Prompts pushed from mobile land in a
'pending' state; the live Claude Code session OR the worker daemon
claims and runs them; results are posted back and made available
via get_result.

State machine (column `status`):
  pending  — pushed by mobile, not yet claimed
  running  — claimed by a consumer (worker or live CC session)
  complete — result posted, success
  failed   — result posted, error

A prompt row carries:
  id            INTEGER PK
  prompt        TEXT     the actual prompt text from mobile
  session_id    TEXT     optional session hint from the pusher
  status        TEXT     state machine value above
  created_at    TIMESTAMP set on insert
  claimed_at    TIMESTAMP set on claim_next
  completed_at  TIMESTAMP set on post_result
  claimed_by    TEXT     identifier of the consumer (worker name
                          or 'live' for slash-command fetches)
  result        TEXT     stringified result on success (NULL on
                          failure)
  error         TEXT     error message on failure (NULL on success)

The schema is intentionally simple — no foreign keys, no
constraints beyond NOT NULL on prompt. The queue must run on a
fresh SQLite file with no setup beyond `Queue(...)` constructor.

Fail-open: every method handles a corrupt / missing DB gracefully
by raising sqlite3.OperationalError up to the caller — the FastAPI
server catches and returns a 500 with a structured detail object,
the worker logs and continues to the next poll. Nothing silently
swallows a SQL error.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Column names matching the schema below. Kept as a tuple so the
# row dict builder stays in sync with the table.
_COLUMNS = (
    "id", "prompt", "session_id", "status", "created_at",
    "claimed_at", "completed_at", "claimed_by", "result", "error",
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS prompts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt       TEXT NOT NULL,
    session_id   TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL,
    claimed_at   TEXT,
    completed_at TEXT,
    claimed_by   TEXT,
    result       TEXT,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_prompts_status_created
    ON prompts(status, created_at);
"""


def _now_iso() -> str:
    """ISO 8601 UTC timestamp string. Stored as TEXT so SQLite
    behaves the same on every platform."""
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """sqlite3.Row → plain dict keyed by column name. Stable shape
    for the MCP tool responses."""
    return {k: row[k] for k in _COLUMNS}


@dataclass
class Queue:
    """SQLite-backed job queue. One instance per process is fine —
    SQLite handles concurrent writers via WAL mode (enabled below).
    """
    db_path: str

    def __post_init__(self) -> None:
        # Create the parent directory + initialise the schema on
        # first construction. Idempotent — calling the constructor
        # repeatedly on the same path is safe.
        p = Path(self.db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as c:
            # WAL mode lets the worker poll while the server writes
            # without blocking either side. Synchronous=NORMAL is
            # the recommended trade-off for a queue — durable
            # enough, fast enough.
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    # ── Producer side ─────────────────────────────────────────────────

    def enqueue(self, prompt: str,
                session_id: str | None = None) -> int:
        """Inserts a new pending row. Returns the new prompt id."""
        with self._connect() as c:
            cur = c.execute(
                "INSERT INTO prompts "
                "(prompt, session_id, status, created_at) "
                "VALUES (?, ?, 'pending', ?)",
                (prompt, session_id, _now_iso()))
            return int(cur.lastrowid)

    # ── Consumer side ─────────────────────────────────────────────────

    def claim_next(self, claimed_by: str) -> dict[str, Any] | None:
        """Atomically claims the oldest pending prompt. Returns the
        claimed row as a dict, or None when the queue is empty.

        Concurrency model: SQLite's BEGIN IMMEDIATE serialises the
        SELECT+UPDATE so two workers / sessions can never claim the
        same row. WAL keeps reads unblocked.
        """
        now = _now_iso()
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                row = c.execute(
                    "SELECT * FROM prompts "
                    "WHERE status = 'pending' "
                    "ORDER BY created_at ASC LIMIT 1"
                ).fetchone()
                if row is None:
                    c.execute("COMMIT")
                    return None
                c.execute(
                    "UPDATE prompts "
                    "SET status = 'running', claimed_at = ?, "
                    " claimed_by = ? "
                    "WHERE id = ?",
                    (now, claimed_by, row["id"]))
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            # Re-fetch so the returned dict reflects the new
            # status / claimed_at / claimed_by values.
            updated = c.execute(
                "SELECT * FROM prompts WHERE id = ?",
                (row["id"],)).fetchone()
            return _row_to_dict(updated)

    def post_result(self, prompt_id: int,
                     result: str | None = None,
                     error: str | None = None) -> bool:
        """Sets the row to complete (success) or failed (error).
        Returns True when the row was found and updated, False
        when the id is unknown. Caller must supply EITHER result
        OR error — not both, not neither.
        """
        if (result is None) == (error is None):
            raise ValueError(
                "post_result requires exactly one of result or "
                "error to be non-None.")
        new_state = "failed" if error is not None else "complete"
        with self._connect() as c:
            cur = c.execute(
                "UPDATE prompts "
                "SET status = ?, completed_at = ?, "
                "    result = ?, error = ? "
                "WHERE id = ? AND status IN ('pending', 'running')",
                (new_state, _now_iso(), result, error, prompt_id))
            return cur.rowcount > 0

    # ── Read side ─────────────────────────────────────────────────────

    def get(self, prompt_id: int) -> dict[str, Any] | None:
        with self._connect() as c:
            row = c.execute(
                "SELECT * FROM prompts WHERE id = ?",
                (prompt_id,)).fetchone()
            return _row_to_dict(row) if row else None

    def list_pending(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT * FROM prompts WHERE status = 'pending' "
                "ORDER BY created_at ASC LIMIT ?",
                (int(limit),)).fetchall()
            return [_row_to_dict(r) for r in rows]

    def purge_pending_and_running(self) -> int:
        """Mark every pending or running prompt as cancelled. Returns
        the number of rows updated.

        Operator-facing queue reset — use when the queue gets jammed
        (e.g. a worker died mid-prompt leaving rows stuck in
        'running', or a flood of pending prompts queued behind a
        long-running one needs to be aborted). Stops the worker from
        picking those rows up on the next poll without needing shell
        access to the SQLite db. Completed and failed rows are
        preserved so the audit trail is intact.

        'cancelled' is a new terminal status — the state machine's
        existing transitions (pending → running → complete | failed)
        gain `pending | running → cancelled` as an operator-driven
        escape hatch. status_snapshot() surfaces the count under its
        own key, the same way it picks up any new status row by
        querying GROUP BY status.

        June 3 2026.
        """
        with self._connect() as c:
            cur = c.execute(
                "UPDATE prompts SET status='cancelled', "
                "completed_at=COALESCE(completed_at, CURRENT_TIMESTAMP) "
                "WHERE status IN ('pending','running')")
            return int(cur.rowcount or 0)

    def status_snapshot(self) -> dict[str, Any]:
        """Aggregate health: counts by state + last completion time.
        Powers the bridge's /status endpoint and the mobile
        side's status check."""
        with self._connect() as c:
            counts: dict[str, int] = {
                "pending": 0, "running": 0,
                "complete": 0, "failed": 0,
            }
            for row in c.execute(
                "SELECT status, COUNT(*) AS n FROM prompts "
                "GROUP BY status").fetchall():
                counts[row["status"]] = int(row["n"])
            last = c.execute(
                "SELECT completed_at FROM prompts "
                "WHERE completed_at IS NOT NULL "
                "ORDER BY completed_at DESC LIMIT 1").fetchone()
            return {
                "counts":             counts,
                "last_completed_at":  (last["completed_at"]
                                        if last else None),
            }
