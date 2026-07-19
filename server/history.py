"""server/history.py — SQLite-backed query history with favorites."""

import sqlite3
import os
from datetime import datetime

from config.settings import HISTORY_DB_PATH


def _conn():
    os.makedirs(os.path.dirname(HISTORY_DB_PATH), exist_ok=True)
    c = sqlite3.connect(HISTORY_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT,
                question    TEXT NOT NULL,
                sql         TEXT,
                row_count   INTEGER DEFAULT 0,
                favorited   INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL
            )
        """)


def save(session_id: str, question: str, sql: str, row_count: int = 0) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO history (session_id,question,sql,row_count,created_at) VALUES(?,?,?,?,?)",
            (session_id, question, sql, row_count, datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def get_all(limit: int = 100) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM history ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def toggle_favorite(history_id: int) -> bool:
    with _conn() as c:
        row = c.execute(
            "SELECT favorited FROM history WHERE id=?", (history_id,)
        ).fetchone()
        if not row:
            return False
        new_val = 0 if row["favorited"] else 1
        c.execute("UPDATE history SET favorited=? WHERE id=?", (new_val, history_id))
    return bool(new_val)


def delete(history_id: int):
    with _conn() as c:
        c.execute("DELETE FROM history WHERE id=?", (history_id,))
