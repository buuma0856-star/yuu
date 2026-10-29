"""SQLite によるデータ保存。"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS staff (
    user_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    max_days_per_week INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS availability (
    user_id TEXT NOT NULL,
    date TEXT NOT NULL,
    available INTEGER NOT NULL,
    start_min INTEGER,
    end_min INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, date)
);
CREATE TABLE IF NOT EXISTS assignments (
    month TEXT NOT NULL,
    date TEXT NOT NULL,
    pattern TEXT NOT NULL,
    user_id TEXT NOT NULL,
    published INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, pattern, user_id)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _write(self, sql: str, params: tuple = ()) -> None:
        with self.lock:
            self.conn.execute(sql, params)
            self.conn.commit()

    def _read(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    # --- staff ---
    def get_staff(self, user_id: str) -> sqlite3.Row | None:
        rows = self._read("SELECT * FROM staff WHERE user_id = ?", (user_id,))
        return rows[0] if rows else None

    def upsert_staff(self, user_id: str, name: str) -> None:
        self._write(
            "INSERT INTO staff (user_id, name, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET name = excluded.name, active = 1",
            (user_id, name, self._now()),
        )

    def set_admin(self, user_id: str, is_admin: bool) -> None:
        self._write("UPDATE staff SET is_admin = ? WHERE user_id = ?", (int(is_admin), user_id))

    def set_active(self, user_id: str, active: bool) -> None:
        self._write("UPDATE staff SET active = ? WHERE user_id = ?", (int(active), user_id))

    def set_max_days(self, user_id: str, max_days: int | None) -> None:
        self._write(
            "UPDATE staff SET max_days_per_week = ? WHERE user_id = ?", (max_days, user_id)
        )

    def list_staff(self, active_only: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM staff"
        if active_only:
            sql += " WHERE active = 1"
        return self._read(sql + " ORDER BY created_at")

    def admins(self) -> list[sqlite3.Row]:
        return self._read("SELECT * FROM staff WHERE is_admin = 1 AND active = 1")

    def find_staff_by_name(self, name: str) -> list[sqlite3.Row]:
        return self._read("SELECT * FROM staff WHERE name = ? AND active = 1", (name,))

    # --- availability ---
    def set_availability(
        self, user_id: str, day: date, available: bool, start: int | None, end: int | None
    ) -> None:
        self._write(
            "INSERT INTO availability (user_id, date, available, start_min, end_min, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, date) DO UPDATE SET "
            "available = excluded.available, start_min = excluded.start_min, "
            "end_min = excluded.end_min, updated_at = excluded.updated_at",
            (user_id, day.isoformat(), int(available), start, end, self._now()),
        )

    def delete_availability(self, user_id: str, day: date) -> bool:
        with self.lock:
            cur = self.conn.execute(
                "DELETE FROM availability WHERE user_id = ? AND date = ?",
                (user_id, day.isoformat()),
            )
            self.conn.commit()
            return cur.rowcount > 0

    def get_availability(
        self, start: date, end: date, user_id: str | None = None
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM availability WHERE date BETWEEN ? AND ?"
        params: tuple = (start.isoformat(), end.isoformat())
        if user_id:
            sql += " AND user_id = ?"
            params += (user_id,)
        return self._read(sql + " ORDER BY date", params)

    # --- assignments ---
    def save_assignments(self, month: str, rows: list[tuple[date, str, str]]) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM assignments WHERE month = ?", (month,))
            self.conn.executemany(
                "INSERT INTO assignments (month, date, pattern, user_id) VALUES (?, ?, ?, ?)",
                [(month, d.isoformat(), p, u) for d, p, u in rows],
            )
            self.conn.commit()

    def get_assignments(self, month: str, user_id: str | None = None) -> list[sqlite3.Row]:
        sql = (
            "SELECT a.*, s.name FROM assignments a LEFT JOIN staff s ON a.user_id = s.user_id "
            "WHERE a.month = ?"
        )
        params: tuple = (month,)
        if user_id:
            sql += " AND a.user_id = ?"
            params += (user_id,)
        return self._read(sql + " ORDER BY a.date, a.pattern, s.name", params)

    def set_published(self, month: str) -> None:
        self._write("UPDATE assignments SET published = 1 WHERE month = ?", (month,))

    def is_published(self, month: str) -> bool:
        rows = self._read(
            "SELECT COUNT(*) AS c FROM assignments WHERE month = ? AND published = 1", (month,)
        )
        return rows[0]["c"] > 0

    # --- meta ---
    def get_meta(self, key: str) -> str | None:
        rows = self._read("SELECT value FROM meta WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    def set_meta(self, key: str, value: str) -> None:
        self._write(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
