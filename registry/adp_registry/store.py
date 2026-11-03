"""SQLite 存储层：记录存取、探测调度、游标分页、子串检索。

存储层不做任何语义加工：``card`` 以抓到的原始 JSON 文本保存再原样读出（不变式 I2），
Registry 侧的事实字段单独成列。
"""

from __future__ import annotations

import base64
import json
import sqlite3
import threading
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id                TEXT PRIMARY KEY,
    url               TEXT NOT NULL UNIQUE,
    host              TEXT NOT NULL,
    card              TEXT NOT NULL,
    card_hash         TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('ok', 'down')),
    first_seen_at     TEXT NOT NULL,
    checked_at        TEXT NOT NULL,
    last_ok_at        TEXT NOT NULL,
    failures          INTEGER NOT NULL DEFAULT 0,
    changed_at        TEXT,
    prev_hash         TEXT,
    manage_token_hash TEXT NOT NULL,
    next_probe_at     TEXT NOT NULL,
    search_blob       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS agents_order_idx   ON agents (first_seen_at DESC, id ASC);
CREATE INDEX IF NOT EXISTS agents_probe_idx   ON agents (next_probe_at ASC);
CREATE INDEX IF NOT EXISTS agents_changed_idx ON agents (changed_at);
CREATE INDEX IF NOT EXISTS agents_host_idx    ON agents (host);
"""

_COLUMNS = (
    "id, url, host, card, card_hash, status, first_seen_at, checked_at, last_ok_at, "
    "failures, changed_at, prev_hash, manage_token_hash, next_probe_at, search_blob"
)


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def encode_cursor(record: dict[str, Any]) -> str:
    raw = json.dumps(
        {"t": record["firstSeenAt"], "i": record["id"]},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str) -> tuple[str, str]:
    """解析游标；失败抛 ValueError（调用方转成 400 BAD_CURSOR）。"""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw)
        return str(payload["t"]), str(payload["i"])
    except Exception as exc:  # noqa: BLE001 - 任何畸形游标都视为非法
        raise ValueError(f"非法游标：{cursor!r}") from exc


def _row_to_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "url": row["url"],
        "host": row["host"],
        "card": json.loads(row["card"]),
        "cardHash": row["card_hash"],
        "status": row["status"],
        "firstSeenAt": row["first_seen_at"],
        "checkedAt": row["checked_at"],
        "lastOkAt": row["last_ok_at"],
        "failures": row["failures"],
        "changedAt": row["changed_at"],
        "prevHash": row["prev_hash"],
        "_manageTokenHash": row["manage_token_hash"],
        "_nextProbeAt": row["next_probe_at"],
        "_searchBlob": row["search_blob"],
    }


def _to_row(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        record["id"],
        record["url"],
        record["host"],
        json.dumps(record["card"], ensure_ascii=False, separators=(",", ":")),
        record["cardHash"],
        record["status"],
        record["firstSeenAt"],
        record["checkedAt"],
        record["lastOkAt"],
        record["failures"],
        record.get("changedAt"),
        record.get("prevHash"),
        record["_manageTokenHash"],
        record["_nextProbeAt"],
        record.get("_searchBlob", ""),
    )


class Store:
    """线程安全的 SQLite 封装（探测线程与 HTTP 线程共用）。"""

    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ 单条存取

    def insert(self, record: dict[str, Any]) -> None:
        placeholders = ", ".join("?" for _ in _COLUMNS.split(","))
        with self._lock, self._conn:
            self._conn.execute(
                f"INSERT INTO agents ({_COLUMNS}) VALUES ({placeholders})",
                _to_row(record),
            )

    def save(self, record: dict[str, Any]) -> None:
        """整行更新（探测结果写回）。``first_seen_at`` 与 token 保持原值。"""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE agents SET card = ?, card_hash = ?, status = ?, checked_at = ?, "
                "last_ok_at = ?, failures = ?, changed_at = ?, prev_hash = ?, "
                "next_probe_at = ?, search_blob = ? WHERE id = ?",
                (
                    json.dumps(record["card"], ensure_ascii=False, separators=(",", ":")),
                    record["cardHash"],
                    record["status"],
                    record["checkedAt"],
                    record["lastOkAt"],
                    record["failures"],
                    record.get("changedAt"),
                    record.get("prevHash"),
                    record["_nextProbeAt"],
                    record.get("_searchBlob", ""),
                    record["id"],
                ),
            )

    def get(self, agent_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_COLUMNS} FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
        return _row_to_record(row) if row else None

    def get_by_url(self, url: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_COLUMNS} FROM agents WHERE url = ?", (url,)
            ).fetchone()
        return _row_to_record(row) if row else None

    def exists(self, agent_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
        return row is not None

    def delete(self, agent_id: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        return cursor.rowcount > 0

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0])

    def count_host(self, host: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM agents WHERE host = ?", (host,)
            ).fetchone()
        return int(row[0])

    # -------------------------------------------------------------------- 检索

    def query(
        self,
        *,
        q: str | None = None,
        skill: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        changed_since: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """固定排序 ``first_seen_at DESC, id ASC`` —— Registry 不做相关性排序。"""
        clauses: list[str] = []
        params: list[Any] = []

        if q:
            clauses.append("search_blob LIKE ? ESCAPE '\\'")
            params.append(f"%{_escape_like(q.casefold())}%")
        if status:
            clauses.append("status = ?")
            params.append(status)
        if changed_since:
            clauses.append("changed_at IS NOT NULL AND changed_at >= ?")
            params.append(changed_since)
        if skill:
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(agents.card, '$.skills') AS s"
                " WHERE json_extract(s.value, '$.id') = ?)"
            )
            params.append(skill)
        if tag:
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(agents.card, '$.skills') AS s,"
                " json_each(s.value, '$.tags') AS t WHERE t.value = ?)"
            )
            params.append(tag)
        if cursor:
            seen_at, seen_id = decode_cursor(cursor)
            clauses.append("(first_seen_at < ? OR (first_seen_at = ? AND id > ?))")
            params.extend([seen_at, seen_at, seen_id])

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT {_COLUMNS} FROM agents {where} "
            "ORDER BY first_seen_at DESC, id ASC LIMIT ?"
        )
        params.append(limit + 1)

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        records = [_row_to_record(row) for row in rows[:limit]]
        has_more = len(rows) > limit
        next_cursor = encode_cursor(records[-1]) if has_more and records else None
        return records, next_cursor

    # -------------------------------------------------------------------- 探测

    def due(self, now: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM agents WHERE next_probe_at <= ? "
                "ORDER BY next_probe_at ASC LIMIT ?",
                (now, limit),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def next_probe_at(self) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(next_probe_at) FROM agents"
            ).fetchone()
        return row[0] if row and row[0] else None
