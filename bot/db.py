"""Tiny thread-safe SQLite storage for the servers the bot creates."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS servers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id      INTEGER NOT NULL,
    chat_id       INTEGER NOT NULL,
    name          TEXT    NOT NULL,
    container     TEXT    NOT NULL,
    volume        TEXT    NOT NULL,
    port          INTEGER NOT NULL,
    address       TEXT    NOT NULL DEFAULT '',
    publish_kind  TEXT    NOT NULL DEFAULT 'manual',
    publish_meta  TEXT    NOT NULL DEFAULT '{}',
    status        TEXT    NOT NULL DEFAULT 'creating',
    created_at    REAL    NOT NULL,
    expires_at    REAL,
    deleted_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_servers_owner  ON servers(owner_id);
CREATE INDEX IF NOT EXISTS idx_servers_status ON servers(status);
"""

LIVE_STATUSES = ("creating", "running", "stopped", "error")


class Database:
    """All access goes through one connection guarded by a re-entrant lock."""

    def __init__(self, path: str) -> None:
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------ write

    def create_server(
        self,
        *,
        owner_id: int,
        chat_id: int,
        name: str,
        container: str,
        volume: str,
        port: int,
        expires_at: Optional[float] = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO servers
                    (owner_id, chat_id, name, container, volume, port,
                     status, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, 'creating', ?, ?)
                """,
                (
                    owner_id,
                    chat_id,
                    name,
                    container,
                    volume,
                    port,
                    time.time(),
                    expires_at,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def update(self, server_id: int, **fields: Any) -> None:
        if not fields:
            return
        if "publish_meta" in fields and not isinstance(fields["publish_meta"], str):
            fields["publish_meta"] = json.dumps(fields["publish_meta"], ensure_ascii=False)
        allowed = {
            "owner_id", "chat_id", "name", "container", "volume", "port",
            "address", "publish_kind", "publish_meta", "status",
            "expires_at", "deleted_at",
        }
        pairs = {k: v for k, v in fields.items() if k in allowed}
        if not pairs:
            return
        assignments = ", ".join(f"{key} = ?" for key in pairs)
        with self._lock:
            self._conn.execute(
                f"UPDATE servers SET {assignments} WHERE id = ?",
                (*pairs.values(), server_id),
            )
            self._conn.commit()

    def mark_deleted(self, server_id: int) -> None:
        self.update(server_id, status="deleted", deleted_at=time.time())

    def purge(self, server_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM servers WHERE id = ?", (server_id,))
            self._conn.commit()

    # ------------------------------------------------------------------- read

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        try:
            data["publish_meta"] = json.loads(data.get("publish_meta") or "{}")
        except (TypeError, ValueError):
            data["publish_meta"] = {}
        return data

    def get(self, server_id: int) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM servers WHERE id = ?", (server_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_by_owner(self, owner_id: int, include_deleted: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM servers WHERE owner_id = ?"
        if not include_deleted:
            query += " AND status != 'deleted'"
        query += " ORDER BY id"
        with self._lock:
            rows = self._conn.execute(query, (owner_id,)).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def list_live(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM servers WHERE status != 'deleted' ORDER BY id"
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def count_by_owner(self, owner_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM servers "
                "WHERE owner_id = ? AND status != 'deleted'",
                (owner_id,),
            ).fetchone()
        return int(row["n"]) if row else 0

    def used_ports(self) -> set[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT port FROM servers WHERE status != 'deleted'"
            ).fetchall()
        return {int(row["port"]) for row in rows}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
