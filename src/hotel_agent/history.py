"""Lightweight SQLite audit log of agent decisions (approved vs disapproved answers).

Separate from the LangGraph checkpointer: the checkpointer stores opaque run state for
pause/resume, whereas this is a flat, queryable table the dashboard reads to show which
guest replies were approved, auto-approved, or rejected — and whether each one wrote to
the PMS. Uses only the stdlib `sqlite3`; writes are best-effort and never break a run.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

_DDL = """
CREATE TABLE IF NOT EXISTS decisions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT    NOT NULL,
    thread_id  TEXT,
    intent     TEXT,
    risky      INTEGER,
    approval   TEXT,            -- auto_approved | approved | rejected | not_required
    approved   INTEGER,         -- 1 = approved/auto, 0 = rejected
    status     TEXT,
    wrote_pms  INTEGER,         -- 1 = a write workflow actually committed
    actions    TEXT,            -- JSON list of workflow names
    email      TEXT,            -- inbound guest email (truncated)
    answer     TEXT,            -- the drafted/sent reply
    sent_to    TEXT
)
"""


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(_DDL)
    return conn


def record_decision(db_path: str, **fields: Any) -> None:
    """Append one decision row. Best-effort: any failure is swallowed."""
    row = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "thread_id": fields.get("thread_id"),
        "intent": fields.get("intent"),
        "risky": int(bool(fields.get("risky"))),
        "approval": fields.get("approval"),
        "approved": int(fields.get("approval") in ("approved", "auto_approved")),
        "status": fields.get("status"),
        "wrote_pms": int(bool(fields.get("wrote_pms"))),
        "actions": json.dumps(fields.get("actions") or []),
        "email": (fields.get("email") or "")[:500],
        "answer": fields.get("answer") or "",
        "sent_to": fields.get("sent_to") or "",
    }
    try:
        with _connect(db_path) as conn:
            conn.execute(
                f"INSERT INTO decisions ({', '.join(row)}) "
                f"VALUES ({', '.join(':' + k for k in row)})",
                row,
            )
    except Exception:  # noqa: BLE001 — auditing must never break the agent
        pass


def load_decisions(db_path: str) -> list[dict]:
    """Return all decisions, newest first. Empty list if the store doesn't exist yet.

    Reading never creates the file — only `record_decision` does — so merely opening the
    dashboard doesn't litter an empty database."""
    if not Path(db_path).exists():
        return []
    try:
        with _connect(db_path) as conn:
            rows = conn.execute("SELECT * FROM decisions ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        return []
