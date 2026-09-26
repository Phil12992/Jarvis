"""Persistentes Memory.

SQLite laeuft synchron, wird aber ueber asyncio.to_thread aufgerufen, damit
der Agent-Loop nicht blockiert. Auf einem CT mit 2 CPU-Kernen ist das
richtig: SQLite mit WAL ist hier sub-millisekundig, und ein Async-Treiber
wuerde nur einen weiteren Thread und weiteren RAM kosten.

Ein einzelner Lock serialisiert den Zugriff. SQLite verkraftet parallele
Leser, aber der Schreiblast-Teil eines Agent-Loops ist klein genug, dass
Serialisieren billiger ist als Fehlerbehandlung fuer SQLITE_BUSY.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger("jarvis.memory")

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Memory:
    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._lock = asyncio.Lock()
        self._conn: sqlite3.Connection | None = None

    # --- Lebenszyklus -------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self._conn = conn
        log.info("memory_geoeffnet", pfad=self._path)
        return conn

    async def startup(self) -> None:
        await asyncio.to_thread(self._connect)

    async def close(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    def _run(self, fn, *args, **kwargs):
        conn = self._connect()

        def _call():
            return fn(conn, *args, **kwargs)

        async def _wrapped():
            async with self._lock:
                return await asyncio.to_thread(_call)

        return _wrapped

    # --- Sessions -----------------------------------------------------------

    async def touch_session(
        self, session_id: str, channel: str, peer: str, summary: str | None = None
    ) -> None:
        now = int(time.time())

        def _op(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO sessions (id, channel, peer, created_at, last_seen_at, summary)
                VALUES (?, ?, ?, ?, ?, COALESCE(?, ''))
                ON CONFLICT(id) DO UPDATE SET
                    last_seen_at = excluded.last_seen_at,
                    summary      = COALESCE(NULLIF(excluded.summary, ''), sessions.summary)
                """,
                (session_id, channel, peer, now, now, summary),
            )

        await self._run(_op)()

    async def get_summary(self, session_id: str) -> str:
        def _op(conn: sqlite3.Connection) -> str:
            row = conn.execute(
                "SELECT summary FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            return (row["summary"] if row else "") or ""

        return await self._run(_op)()

    async def set_summary(self, session_id: str, summary: str) -> None:
        def _op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE sessions SET summary = ?, last_seen_at = ? WHERE id = ?",
                (summary, int(time.time()), session_id),
            )

        await self._run(_op)()

    async def reap_idle(self, ttl_hours: int) -> int:
        """Raeumt Sessions auf, die zu lange nichts mehr gesehen wurden.

        `<=` statt `<`: eine Session, die exakt auf der Grenze steht, ist
       idle, nicht frisch. Mit `<` bleibt sie bei ttl_hours=0 ewig liegen.
        """

        def _op(conn: sqlite3.Connection) -> int:
            cutoff = int(time.time()) - ttl_hours * 3600
            cur = conn.execute(
                "DELETE FROM messages WHERE session_id IN "
                "(SELECT id FROM sessions WHERE last_seen_at <= ?)",
                (cutoff,),
            )
            conn.execute("DELETE FROM sessions WHERE last_seen_at <= ?", (cutoff,))
            return cur.rowcount or 0

        return await self._run(_op)()


    # --- Nachrichten --------------------------------------------------------

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str = "",
        *,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        model: str | None = None,
    ) -> None:
        def _op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO messages "
                "(session_id, role, content, tool_call_id, tool_name, model, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, role, content, tool_call_id, tool_name, model, int(time.time())),
            )

        await self._run(_op)()

    async def get_messages(self, session_id: str, limit: int = 40) -> list[dict[str, Any]]:
        """Letzte `limit` Nachrichten in chronologischer Reihenfolge."""

        def _op(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = conn.execute(
                "SELECT role, content, tool_call_id, tool_name, model FROM messages "
                "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

        return await self._run(_op)()

    # --- Langzeit-Erinnerungen ---------------------------------------------

    async def remember(
        self,
        content: str,
        *,
        kind: str = "fact",
        importance: int = 3,
        source: str = "",
        pinned: bool = False,
    ) -> int:
        content = content.strip()
        if not content:
            return 0
        now = int(time.time())

        def _op(conn: sqlite3.Connection) -> int:
            dup = conn.execute(
                "SELECT id FROM memories WHERE content = ?", (content,)
            ).fetchone()
            if dup:
                conn.execute(
                    "UPDATE memories SET importance = MAX(importance, ?), updated_at = ? "
                    "WHERE id = ?",
                    (importance, now, dup["id"]),
                )
                return int(dup["id"])
            cur = conn.execute(
                "INSERT INTO memories (kind, content, source, importance, pinned, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (kind, content, source, importance, int(pinned), now, now),
            )
            return int(cur.lastrowid or 0)

        row_id = await self._run(_op)()
        log.info("gemerkt", id=row_id, art=kind, wichtigkeit=importance)
        return row_id

    async def forget(self, memory_id: int) -> bool:
        def _op(conn: sqlite3.Connection) -> bool:
            cur = conn.execute("DELETE FROM memories WHERE id = ? AND pinned = 0", (memory_id,))
            return (cur.rowcount or 0) > 0

        return await self._run(_op)()

    async def recall(
        self, query: str = "", limit: int = 8, min_importance: int = 1
    ) -> list[dict[str, Any]]:
        """Ohne query: die wichtigsten. Mit query: FTS5-Volltextsuche."""
        limit = max(1, min(limit, 50))

        def _op(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            if not query.strip():
                rows = conn.execute(
                    "SELECT id, kind, content, importance, hits FROM memories "
                    "WHERE importance >= ? ORDER BY pinned DESC, importance DESC, updated_at DESC "
                    "LIMIT ?",
                    (min_importance, limit),
                ).fetchall()
            else:
                # Sanitisiert, weil FTS5 eine Query-Syntax hat und ein
                # nacktes Minuszeichen sonst eine Ausnahme ausloest.
                safe = " ".join(
                    "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in query).split()
                )
                if not safe:
                    return []
                try:
                    rows = conn.execute(
                        """
                        SELECT m.id, m.kind, m.content, m.importance, m.hits,
                               bm25(memories_fts) AS rank
                        FROM memories_fts
                        JOIN memories m ON m.id = memories_fts.rowid
                        WHERE memories_fts MATCH ? AND m.importance >= ?
                        ORDER BY rank
                        LIMIT ?
                        """,
                        (f'"{safe}"*', min_importance, limit),
                    ).fetchall()
                except sqlite3.OperationalError as exc:
                    log.warning("fts_query_fehlgeschlagen", fehler=str(exc))
                    return []
            return [dict(r) for r in rows]

        return await self._run(_op)()

    async def mark_used(self, memory_ids: Sequence[int]) -> None:
        if not memory_ids:
            return

        def _op(conn: sqlite3.Connection) -> None:
            conn.executemany(
                "UPDATE memories SET hits = hits + 1 WHERE id = ?", [(i,) for i in memory_ids]
            )

        await self._run(_op)()

    async def prune_memories(self, keep: int = 500) -> int:
        """Beschneidet Memory auf `keep` Eintraege. Gepinnte bleiben immer."""
        keep = max(50, keep)

        def _op(conn: sqlite3.Connection) -> int:
            cur = conn.execute(
                "DELETE FROM memories WHERE pinned = 0 AND id NOT IN ("
                "  SELECT id FROM memories WHERE pinned = 0 "
                "  ORDER BY importance DESC, hits DESC, updated_at DESC LIMIT ?"
                ")",
                (keep,),
            )
            return cur.rowcount or 0

        removed = await self._run(_op)()
        if removed:
            log.info("memory_gekuerzt", entfernt=removed, behalten=keep)
        return removed

    # --- KV -----------------------------------------------------------------

    async def kv_get(self, key: str, default: str = "") -> str:
        def _op(conn: sqlite3.Connection) -> str:
            row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

        return await self._run(_op)()

    async def kv_set(self, key: str, value: str) -> None:
        def _op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, value, int(time.time())),
            )

        await self._run(_op)()

    async def note_skill_use(self, name: str) -> None:
        def _op(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO skill_usage (name, uses, last_used) VALUES (?, 1, ?) "
                "ON CONFLICT(name) DO UPDATE SET uses = uses + 1, last_used = excluded.last_used",
                (name, int(time.time())),
            )

        await self._run(_op)()

    # --- Diagnose -----------------------------------------------------------

    async def stats(self) -> dict[str, Any]:
        def _op(conn: sqlite3.Connection) -> dict[str, Any]:
            def count(table: str, where: str = "") -> int:
                q = f"SELECT COUNT(*) AS c FROM {table}"  # noqa: S608 - feste Tabellenliste
                if where:
                    q += f" WHERE {where}"
                return int(conn.execute(q).fetchone()["c"])

            return {
                "sessions": count("sessions"),
                "messages": count("messages"),
                "memories": count("memories"),
                "pinned": count("memories", "pinned = 1"),
                "db_bytes": Path(self._path).stat().st_size if Path(self._path).exists() else 0,
            }

        return await self._run(_op)()


_memory: Memory | None = None


def get_memory() -> Memory:
    global _memory
    if _memory is None:
        from jarvis.config import get_settings

        _memory = Memory(get_settings().memory_db_path)
    return _memory
