"""SQLite state persistence for studio-api via aiosqlite."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import structlog

import secretbox

log = structlog.get_logger()

# Connection tuning. On this deployment the SQLite file lives on an NFS home,
# where acquiring an exclusive write lock (e.g. the one-time ALTER TABLE in
# `_migrate`) can otherwise block indefinitely on a stale NFSv4 lock lease.
# Bound every lock wait so a contended write fails fast (SQLITE_BUSY) rather
# than hanging the process — `connect()` runs at container start, before
# supervisord, so a hang there stalls the whole notebook server past
# JupyterHub's spawn timeout.
_BUSY_TIMEOUT_MS = 5000
_CONNECT_ATTEMPTS = 3
_CONNECT_RETRY_DELAY_S = 2.0

def _legacy_workspace() -> str:
    """The pre-#401 workspace root, for finding a database left behind in it.

    Not where anything is written now — see workspace.py. An explicit
    STUDIO_WORKSPACE_DIR is still honored, because a deployment that set one
    had its old database under that directory too.
    """
    explicit = os.environ.get("STUDIO_WORKSPACE_DIR")
    if explicit:
        return explicit
    home = os.environ.get("HOME")
    if home:
        return os.path.join(home, "work")
    return "/tmp/studio-work"

def _state_dir() -> str:
    """Where Studio keeps its own state.

    ``$HOME/.studio``, not ``$HOME/work/.studio``. The workspace holds workflow
    projects; the database is Studio's, and burying it inside the directory
    users are told to work in meant a `rm -rf` there could take the settings,
    LLM configuration and history with it. ``$HOME`` also matches where every
    other piece of generated config now lives.
    """
    home = os.environ.get("HOME")
    if home:
        return os.path.join(home, ".studio")
    return "/tmp/studio-state"


_DEFAULT_DB_DIR = _state_dir()
_DEFAULT_DB_PATH = os.path.join(_DEFAULT_DB_DIR, "studio.db")

# Where the database used to live. Kept only to move it on first start.
_LEGACY_DB_PATH = os.path.join(_legacy_workspace(), ".studio", "studio.db")


def _migrate_legacy_db(new_path: str) -> bool:
    """Move a database left at the old workspace location.

    Copies rather than repoints: the database holds the user's settings, LLM
    configuration, chat history and run records, so simply pointing at a new
    empty path would look exactly like data loss.

    The -wal and -shm siblings come too — leaving them behind risks a stale
    write-ahead log being applied to the wrong file later.
    """
    if os.path.exists(new_path) or not os.path.isfile(_LEGACY_DB_PATH):
        return False
    try:
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            src = _LEGACY_DB_PATH + suffix
            if os.path.isfile(src):
                shutil.move(src, new_path + suffix)
    except OSError as e:
        log.warning("db_migrate_failed", src=_LEGACY_DB_PATH, dst=new_path, error=str(e))
        return False
    log.info("db_migrated", src=_LEGACY_DB_PATH, dst=new_path)
    return True

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_installations (
    tool_id         TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'installed',
    config          TEXT DEFAULT '{}',
    process_pid     INTEGER,
    web_port        INTEGER,
    installed_at    TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_config (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    provider        TEXT NOT NULL,
    model           TEXT,
    api_key         TEXT,
    base_url        TEXT,
    extra_config    TEXT DEFAULT '{}',
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id          TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    run_dir         TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL,
    total_jobs      INTEGER DEFAULT 0,
    completed_jobs  INTEGER DEFAULT 0,
    failed_jobs     INTEGER DEFAULT 0,
    exec_site       TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_configs (
    provider_id     TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    api_key         TEXT DEFAULT '',
    base_url        TEXT DEFAULT '',
    default_model   TEXT DEFAULT '',
    is_active       INTEGER DEFAULT 0,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT,
    request_id      TEXT,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    agent_id        TEXT,
    tool_calls      TEXT,
    created_at      TEXT NOT NULL,
    -- User rating of an assistant reply: 1 = useful, -1 = not useful,
    -- NULL = no rating. Added via _migrate() on pre-existing DBs.
    feedback        INTEGER
);

-- NOTE: the idx_chat_messages_conv index is created in `_migrate()`, NOT here.
-- On a pre-existing DB, `CREATE TABLE IF NOT EXISTS` is a no-op so the
-- `conversation_id` column doesn't exist until `_migrate()` adds it; creating
-- the index here would reference a missing column and abort the whole schema
-- script.

CREATE TABLE IF NOT EXISTS notifications (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,          -- workflow_state | job_failure | resource | manual
    severity        TEXT NOT NULL DEFAULT 'info',  -- info | success | warning | error
    title           TEXT NOT NULL,
    body            TEXT,
    run_id          TEXT,                   -- associated workflow run, if any
    link            TEXT,                   -- optional in-app path to open
    dedup_key       TEXT,                   -- collapse repeated events
    meta            TEXT DEFAULT '{}',
    read            INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notifications_created
    ON notifications (created_at DESC);

-- Durable ledger of every dedup_key ever delivered. Unlike the notifications
-- table it is NOT emptied by clear/delete, so the watch loop's "fire once"
-- guards can't resurrect a notification the user already cleared when the
-- server restarts (e.g. on logout/login re-spawn).
CREATE TABLE IF NOT EXISTS notification_log (
    dedup_key       TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,          -- JSON-encoded
    updated_at      TEXT NOT NULL
);

-- Credentials a workflow's generator needs (KAGGLE_KEY, an OpenAQ token, ...).
-- Kept out of app_settings deliberately: values here are secretbox-encrypted
-- and must never be returned over the API, and app_settings is read and echoed
-- freely. Its own table makes "never echo this" a property of the storage
-- rather than a rule every reader has to remember.
CREATE TABLE IF NOT EXISTS workflow_secrets (
    name            TEXT PRIMARY KEY,       -- environment variable name
    value           TEXT NOT NULL,          -- secretbox-encrypted (S7)
    updated_at      TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_id() -> str:
    return uuid.uuid4().hex[:12]


class Database:
    """Async SQLite database wrapper for studio-api state."""

    def __init__(self, db_path: str | None = None) -> None:
        self._path = db_path or _DEFAULT_DB_PATH
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        _migrate_legacy_db(self._path)
        last_err: Exception | None = None
        for attempt in range(1, _CONNECT_ATTEMPTS + 1):
            try:
                self._db = await aiosqlite.connect(self._path)
                self._db.row_factory = aiosqlite.Row
                # Bound lock waits so a contended write can't hang forever on
                # NFS — SQLite returns SQLITE_BUSY after this many ms instead.
                await self._db.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
                await self._db.executescript(_SCHEMA)
                await self._migrate()
                await self._db.commit()
                log.info("database_connected", path=self._path)
                return
            except Exception as e:  # noqa: BLE001 — retry any connect/migrate error
                last_err = e
                log.warning(
                    "database_connect_failed",
                    path=self._path,
                    attempt=attempt,
                    error=str(e),
                )
                if self._db is not None:
                    try:
                        await self._db.close()
                    finally:
                        self._db = None
                if attempt < _CONNECT_ATTEMPTS:
                    await asyncio.sleep(_CONNECT_RETRY_DELAY_S)
        assert last_err is not None
        raise last_err

    async def _migrate(self) -> None:
        """Lightweight, idempotent column migrations for pre-existing DBs.

        `CREATE TABLE IF NOT EXISTS` won't add new columns to a table that
        already exists, so add them explicitly if missing.
        """
        async with self.db.execute("PRAGMA table_info(chat_messages)") as cur:
            cols = {row["name"] for row in await cur.fetchall()}
        if "conversation_id" not in cols:
            try:
                await self.db.execute(
                    "ALTER TABLE chat_messages ADD COLUMN conversation_id TEXT"
                )
                log.info("db_migrated", change="chat_messages.conversation_id")
            except Exception as e:  # noqa: BLE001
                # A concurrent connector (propagate_llm.py now runs alongside
                # studio-api) may have added the column first; tolerate that.
                if "duplicate column" not in str(e).lower():
                    raise
        if "feedback" not in cols:
            try:
                await self.db.execute(
                    "ALTER TABLE chat_messages ADD COLUMN feedback INTEGER"
                )
                log.info("db_migrated", change="chat_messages.feedback")
            except Exception as e:  # noqa: BLE001
                if "duplicate column" not in str(e).lower():
                    raise
        # Create the index only now that `conversation_id` is guaranteed to
        # exist (whether from CREATE TABLE on a fresh DB or the ALTER above).
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_messages_conv "
            "ON chat_messages (conversation_id, id)"
        )
        # Seed the durable dedup ledger from any notifications already in the
        # feed so upgrading an existing DB doesn't re-fire their alerts.
        await self.db.execute(
            """INSERT OR IGNORE INTO notification_log (dedup_key, created_at)
               SELECT dedup_key, created_at FROM notifications
               WHERE dedup_key IS NOT NULL"""
        )

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Database not connected"
        return self._db

    # --- LLM Config ---

    async def llm_config_exists(self) -> bool:
        """Whether the user has explicitly saved an LLM config row."""
        async with self.db.execute(
            "SELECT 1 FROM llm_config WHERE id = 1"
        ) as cursor:
            return await cursor.fetchone() is not None

    async def get_llm_config(self) -> dict[str, Any] | None:
        async with self.db.execute(
            "SELECT * FROM llm_config WHERE id = 1"
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                # Ship a working default: an internal OpenAI-compatible gateway,
                # so chat/tools work with zero configuration. Users can override
                # this in Settings, which writes a real row. None when there
                # is no gateway to default to (#498): every caller already
                # treats None as "nothing configured".
                from llm.providers import (
                    GATEWAY_BASE_URL,
                    GATEWAY_DEFAULT_MODEL,
                    gateway_configured,
                )

                if not gateway_configured():
                    return None

                return {
                    "provider": "gateway",
                    "model": GATEWAY_DEFAULT_MODEL,
                    "api_key": "",
                    "base_url": GATEWAY_BASE_URL,
                    "extra_config": {},
                    "updated_at": None,
                }
            return {
                "provider": row["provider"],
                "model": row["model"],
                "api_key": secretbox.decrypt(row["api_key"]),
                "base_url": row["base_url"],
                "extra_config": json.loads(row["extra_config"] or "{}"),
                "updated_at": row["updated_at"],
            }

    async def clear_llm_config(self) -> None:
        """Forget the active LLM config (#498): with no gateway to fall back
        to, deleting the last provider leaves nothing configured rather than a
        config pointing at a host that does not exist."""
        await self.db.execute("DELETE FROM llm_config WHERE id = 1")
        await self.db.commit()

    async def set_llm_config(
        self,
        provider: str,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        extra_config: dict[str, Any] | None = None,
    ) -> None:
        now = _now()
        extra_json = json.dumps(extra_config or {})
        api_key = secretbox.encrypt(api_key)  # at-rest encryption (S7)
        await self.db.execute(
            """INSERT INTO llm_config (id, provider, model, api_key, base_url, extra_config, updated_at)
               VALUES (1, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   provider = excluded.provider,
                   model = excluded.model,
                   api_key = excluded.api_key,
                   base_url = excluded.base_url,
                   extra_config = excluded.extra_config,
                   updated_at = excluded.updated_at""",
            (provider, model, api_key, base_url, extra_json, now),
        )
        await self.db.commit()

    # --- Provider Configs ---

    async def list_provider_configs(self) -> list[dict[str, Any]]:
        results = []
        async with self.db.execute(
            "SELECT * FROM provider_configs ORDER BY provider_id"
        ) as cursor:
            async for row in cursor:
                results.append({
                    "provider_id": row["provider_id"],
                    "name": row["name"],
                    "api_key": secretbox.decrypt(row["api_key"]),
                    "base_url": row["base_url"],
                    "default_model": row["default_model"],
                    "is_active": bool(row["is_active"]),
                    "updated_at": row["updated_at"],
                })
        return results

    async def get_provider_config(self, provider_id: str) -> dict[str, Any] | None:
        async with self.db.execute(
            "SELECT * FROM provider_configs WHERE provider_id = ?", (provider_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return {
                "provider_id": row["provider_id"],
                "name": row["name"],
                "api_key": secretbox.decrypt(row["api_key"]),
                "base_url": row["base_url"],
                "default_model": row["default_model"],
                "is_active": bool(row["is_active"]),
                "updated_at": row["updated_at"],
            }

    async def upsert_provider_config(
        self,
        provider_id: str,
        name: str,
        api_key: str = "",
        base_url: str = "",
        default_model: str = "",
        is_active: bool = False,
    ) -> None:
        now = _now()
        api_key = secretbox.encrypt(api_key)  # at-rest encryption (S7)
        await self.db.execute(
            """INSERT INTO provider_configs
               (provider_id, name, api_key, base_url, default_model, is_active, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(provider_id) DO UPDATE SET
                   name = excluded.name,
                   api_key = excluded.api_key,
                   base_url = excluded.base_url,
                   default_model = excluded.default_model,
                   is_active = excluded.is_active,
                   updated_at = excluded.updated_at""",
            (provider_id, name, api_key, base_url, default_model, int(is_active), now),
        )
        await self.db.commit()

    async def set_active_provider(self, provider_id: str) -> None:
        """Set one provider as active, deactivating all others."""
        await self.db.execute(
            "UPDATE provider_configs SET is_active = 0"
        )
        await self.db.execute(
            "UPDATE provider_configs SET is_active = 1 WHERE provider_id = ?",
            (provider_id,),
        )
        await self.db.commit()

        # Also sync to llm_config table for backward compatibility
        config = await self.get_provider_config(provider_id)
        if config:
            # Carry the existing extra_config across. Omitting it wrote {},
            # so activating any provider silently discarded settings such as
            # wire_api and max_tokens — and activation is routine, not rare.
            existing = await self.get_llm_config()
            await self.set_llm_config(
                provider=config["provider_id"],
                model=config["default_model"] or None,
                api_key=config["api_key"] or None,
                base_url=config["base_url"] or None,
                extra_config=(existing or {}).get("extra_config") or {},
            )

    async def delete_provider_config(self, provider_id: str) -> None:
        await self.db.execute(
            "DELETE FROM provider_configs WHERE provider_id = ?", (provider_id,)
        )
        await self.db.commit()

    # --- Tool Installations ---

    async def install_tool(
        self,
        tool_id: str,
        config: dict[str, Any] | None = None,
    ) -> None:
        now = _now()
        await self.db.execute(
            """INSERT INTO tool_installations
               (tool_id, status, config, installed_at, updated_at)
               VALUES (?, 'installed', ?, ?, ?)
               ON CONFLICT(tool_id) DO UPDATE SET
                   status = 'installed',
                   config = excluded.config,
                   updated_at = excluded.updated_at""",
            (tool_id, json.dumps(config or {}), now, now),
        )
        await self.db.commit()

    async def get_tool(self, tool_id: str) -> dict[str, Any] | None:
        async with self.db.execute(
            "SELECT * FROM tool_installations WHERE tool_id = ?", (tool_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return {
                "tool_id": row["tool_id"],
                "status": row["status"],
                "config": json.loads(row["config"] or "{}"),
                "process_pid": row["process_pid"],
                "web_port": row["web_port"],
                "installed_at": row["installed_at"],
                "updated_at": row["updated_at"],
            }

    async def list_tools(self) -> list[dict[str, Any]]:
        results = []
        async with self.db.execute(
            "SELECT * FROM tool_installations ORDER BY installed_at"
        ) as cursor:
            async for row in cursor:
                results.append({
                    "tool_id": row["tool_id"],
                    "status": row["status"],
                    "config": json.loads(row["config"] or "{}"),
                    "process_pid": row["process_pid"],
                    "web_port": row["web_port"],
                    "installed_at": row["installed_at"],
                    "updated_at": row["updated_at"],
                })
        return results

    async def update_tool_status(
        self,
        tool_id: str,
        status: str,
        process_pid: int | None = None,
        web_port: int | None = None,
    ) -> None:
        now = _now()
        await self.db.execute(
            """UPDATE tool_installations
               SET status = ?, process_pid = ?, web_port = ?, updated_at = ?
               WHERE tool_id = ?""",
            (status, process_pid, web_port, now, tool_id),
        )
        await self.db.commit()

    async def remove_tool(self, tool_id: str) -> None:
        await self.db.execute(
            "DELETE FROM tool_installations WHERE tool_id = ?", (tool_id,)
        )
        await self.db.commit()

    # --- Workflow Runs ---

    async def upsert_workflow_run(
        self,
        run_id: str,
        name: str,
        run_dir: str,
        status: str,
        total_jobs: int = 0,
        completed_jobs: int = 0,
        failed_jobs: int = 0,
        exec_site: str | None = None,
    ) -> None:
        now = _now()
        # A run can collide on either unique key: run_dir (replanned in place)
        # or run_id/wf_uuid (rerun produces a new submit dir with the same uuid).
        # ON CONFLICT can only target one column, so use INSERT OR REPLACE, which
        # resolves a conflict on either key. Preserve the earliest created_at of
        # any row we're replacing so the original discovery time survives.
        await self.db.execute(
            """INSERT OR REPLACE INTO workflow_runs
               (run_id, name, run_dir, status, total_jobs, completed_jobs,
                failed_jobs, exec_site, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                       COALESCE(
                           (SELECT MIN(created_at) FROM workflow_runs
                            WHERE run_id = ? OR run_dir = ?),
                           ?),
                       ?)""",
            (run_id, name, run_dir, status, total_jobs, completed_jobs,
             failed_jobs, exec_site, run_id, run_dir, now, now),
        )
        await self.db.commit()

    async def delete_workflow_run(self, run_id: str) -> None:
        """Drop a single run from the persisted list (used when a run's submit
        directory is deleted)."""
        await self.db.execute(
            "DELETE FROM workflow_runs WHERE run_id = ?", (run_id,)
        )
        await self.db.commit()

    async def move_run_dirs(self, old_dir: str, new_dir: str) -> int:
        """Re-point every stored run path under `old_dir` to `new_dir`.

        Used when a project folder is renamed: run history and feedback
        episodes are keyed by run_dir, and rows left on the old path would show
        as vanished runs while the scanner lists the same runs again under the
        new one. Prefix-matched with substr rather than LIKE, because a path may
        contain `_` or `%`. Returns the number of rows changed.
        """
        old = old_dir.rstrip("/")
        new = new_dir.rstrip("/")
        n = len(old)
        changed = 0
        for table, column in (
            ("workflow_runs", "run_dir"),
            ("feedback_episodes", "run_dir"),
            ("feedback_episodes", "rerun_dir"),
        ):
            # The feedback-episodes table belongs to an upstream feature this
            # branch does not include, so it may not exist — on a fresh DB it
            # never does. A rename must not fail for that; episode rows are
            # re-pointed whenever the table is present.
            probe = await self.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            )
            if await probe.fetchone() is None:
                continue
            cursor = await self.db.execute(
                f"UPDATE {table} SET {column} = ? || substr({column}, ?) "
                f"WHERE {column} = ? OR substr({column}, 1, ?) = ?",
                (new, n + 1, old, n + 1, old + "/"),
            )
            changed += cursor.rowcount or 0
        await self.db.commit()
        return changed

    async def list_workflow_runs(self) -> list[dict[str, Any]]:
        results = []
        async with self.db.execute(
            "SELECT * FROM workflow_runs ORDER BY created_at DESC"
        ) as cursor:
            async for row in cursor:
                results.append({
                    "run_id": row["run_id"],
                    "name": row["name"],
                    "run_dir": row["run_dir"],
                    "status": row["status"],
                    "total_jobs": row["total_jobs"],
                    "completed_jobs": row["completed_jobs"],
                    "failed_jobs": row["failed_jobs"],
                    "exec_site": row["exec_site"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                })
        return results

    # --- Chat Messages ---

    async def add_chat_message(
        self,
        role: str,
        content: str,
        agent_id: str | None = None,
        tool_calls: Any | None = None,
        conversation_id: str | None = None,
        request_id: str | None = None,
    ) -> int:
        now = _now()
        async with self.db.execute(
            """INSERT INTO chat_messages
                   (conversation_id, request_id, role, content, agent_id, tool_calls, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                conversation_id,
                request_id,
                role,
                content,
                agent_id,
                json.dumps(tool_calls) if tool_calls else None,
                now,
            ),
        ) as cursor:
            msg_id = cursor.lastrowid
        await self.db.commit()
        return msg_id or 0

    async def get_chat_history(
        self, limit: int = 100, conversation_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Most-recent `limit` messages, oldest-first. Scoped to a conversation
        when `conversation_id` is given."""
        if conversation_id is not None:
            query = (
                "SELECT * FROM chat_messages WHERE conversation_id = ? "
                "ORDER BY id DESC LIMIT ?"
            )
            params: tuple[Any, ...] = (conversation_id, limit)
        else:
            query = "SELECT * FROM chat_messages ORDER BY id DESC LIMIT ?"
            params = (limit,)

        results = []
        async with self.db.execute(query, params) as cursor:
            async for row in cursor:
                results.append({
                    "id": row["id"],
                    "role": row["role"],
                    "content": row["content"],
                    "agent_id": row["agent_id"],
                    "tool_calls": json.loads(row["tool_calls"]) if row["tool_calls"] else None,
                    "created_at": row["created_at"],
                    "feedback": row["feedback"],
                })
        results.reverse()
        return results

    async def delete_chat_history(self, conversation_id: str) -> int:
        """Delete all chat messages for a conversation. Returns rows removed."""
        async with self.db.execute(
            "DELETE FROM chat_messages WHERE conversation_id = ?",
            (conversation_id,),
        ) as cursor:
            deleted = cursor.rowcount or 0
        await self.db.commit()
        return deleted

    async def get_chat_message(self, message_id: int) -> dict[str, Any] | None:
        """Single row by id, with request_id/conversation_id/feedback for the feedback gate."""
        async with self.db.execute(
            "SELECT id, conversation_id, role, agent_id, feedback, request_id FROM chat_messages WHERE id = ?",
            (message_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def set_chat_feedback(self, message_id: int, value: int | None) -> bool:
        """Rate an assistant reply: 1 = useful, -1 = not useful, None = clear.
        Returns True if the message existed."""
        async with self.db.execute(
            "UPDATE chat_messages SET feedback = ? WHERE id = ?",
            (value, message_id),
        ) as cursor:
            changed = (cursor.rowcount or 0) > 0
        await self.db.commit()
        return changed

    async def prune_chat_messages(self, max_rows: int = 5000) -> int:
        """Cap total stored chat messages, deleting the oldest beyond `max_rows`.
        Returns the number deleted. Keeps the table from growing unbounded on a
        long-lived container."""
        async with self.db.execute(
            "DELETE FROM chat_messages WHERE id NOT IN "
            "(SELECT id FROM chat_messages ORDER BY id DESC LIMIT ?)",
            (max_rows,),
        ) as cursor:
            deleted = cursor.rowcount or 0
        await self.db.commit()
        return deleted

    async def prune_workflow_runs(self, max_rows: int = 1000) -> int:
        """Cap stored workflow-run records, deleting the oldest (by created_at)
        beyond `max_rows`. Bounds table growth on a long-lived container."""
        async with self.db.execute(
            "DELETE FROM workflow_runs WHERE run_id NOT IN "
            "(SELECT run_id FROM workflow_runs ORDER BY created_at DESC LIMIT ?)",
            (max_rows,),
        ) as cursor:
            deleted = cursor.rowcount or 0
        await self.db.commit()
        return deleted

    # --- Notifications ---

    async def add_notification(
        self,
        kind: str,
        title: str,
        body: str | None = None,
        severity: str = "info",
        run_id: str | None = None,
        link: str | None = None,
        dedup_key: str | None = None,
        meta: dict[str, Any] | None = None,
        read: bool = False,
    ) -> dict[str, Any]:
        """Insert a notification and return the stored row.

        If ``dedup_key`` matches an existing notification, that row is refreshed
        (bumped to now) instead of inserting a duplicate — this keeps repeated
        state polls from flooding the feed. A refresh never flips an
        already-read notification back to unread, so marking one read sticks
        even as the watch loop keeps touching it.

        Every ``dedup_key`` is also recorded in the durable ``notification_log``
        ledger, which survives clear/delete so fire-once guards don't resurrect
        a cleared notification after a restart.

        ``read=True`` records the notification as already-read: it lands in
        history but does not count toward the unread badge (used for muted
        categories, which are recorded but non-interrupting).
        """
        now = _now()
        read_int = 1 if read else 0
        if dedup_key:
            # Durable delivery record (survives clear/delete).
            await self.db.execute(
                "INSERT OR IGNORE INTO notification_log (dedup_key, created_at) "
                "VALUES (?, ?)",
                (dedup_key, now),
            )
            async with self.db.execute(
                """SELECT id, read FROM notifications
                   WHERE dedup_key = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (dedup_key,),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing:
                # Once read, stays read — a content refresh must not re-surface
                # a notification the user already dismissed.
                new_read = 1 if (existing["read"] or read_int) else 0
                await self.db.execute(
                    """UPDATE notifications
                       SET title = ?, body = ?, severity = ?, run_id = ?,
                           link = ?, meta = ?, read = ?, created_at = ?
                       WHERE id = ?""",
                    (title, body, severity, run_id, link,
                     json.dumps(meta or {}), new_read, now, existing["id"]),
                )
                await self.db.commit()
                return await self.get_notification(existing["id"])  # type: ignore[return-value]

        nid = _gen_id()
        await self.db.execute(
            """INSERT INTO notifications
               (id, kind, severity, title, body, run_id, link, dedup_key,
                meta, read, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (nid, kind, severity, title, body, run_id, link, dedup_key,
             json.dumps(meta or {}), read_int, now),
        )
        await self.db.commit()
        return await self.get_notification(nid)  # type: ignore[return-value]

    @staticmethod
    def _notification_row(row: aiosqlite.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "severity": row["severity"],
            "title": row["title"],
            "body": row["body"],
            "run_id": row["run_id"],
            "link": row["link"],
            "meta": json.loads(row["meta"]) if row["meta"] else {},
            "read": bool(row["read"]),
            "created_at": row["created_at"],
        }

    async def notification_exists(self, dedup_key: str) -> bool:
        """Whether a notification with this dedup_key has ever been delivered.

        Backed by the durable ``notification_log`` ledger, so this stays True
        even after the user clears or deletes the visible notification. That is
        what makes seed-time / first-sighting alerts fire only once across
        restarts without re-appearing after a clear."""
        async with self.db.execute(
            "SELECT 1 FROM notification_log WHERE dedup_key = ? LIMIT 1",
            (dedup_key,),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def get_notification(self, notification_id: str) -> dict[str, Any] | None:
        async with self.db.execute(
            "SELECT * FROM notifications WHERE id = ?", (notification_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return self._notification_row(row) if row else None

    async def list_notifications(
        self, limit: int = 100, unread_only: bool = False
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM notifications"
        if unread_only:
            query += " WHERE read = 0"
        query += " ORDER BY created_at DESC LIMIT ?"
        results = []
        async with self.db.execute(query, (limit,)) as cursor:
            async for row in cursor:
                results.append(self._notification_row(row))
        return results

    async def unread_notification_count(self) -> int:
        async with self.db.execute(
            "SELECT COUNT(*) AS n FROM notifications WHERE read = 0"
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["n"]) if row else 0

    async def mark_notification_read(self, notification_id: str) -> None:
        await self.db.execute(
            "UPDATE notifications SET read = 1 WHERE id = ?", (notification_id,)
        )
        await self.db.commit()

    async def mark_all_notifications_read(self) -> None:
        await self.db.execute("UPDATE notifications SET read = 1 WHERE read = 0")
        await self.db.commit()

    async def delete_notification(self, notification_id: str) -> None:
        await self.db.execute(
            "DELETE FROM notifications WHERE id = ?", (notification_id,)
        )
        await self.db.commit()

    async def clear_notifications(self) -> None:
        await self.db.execute("DELETE FROM notifications")
        await self.db.commit()

    # --- App settings (generic JSON key-value) ---

    async def get_setting(self, key: str, default: Any = None) -> Any:
        async with self.db.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (ValueError, TypeError):
            return default

    async def set_setting(self, key: str, value: Any) -> None:
        await self.db.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   updated_at = excluded.updated_at""",
            (key, json.dumps(value), _now()),
        )
        await self.db.commit()

    # --- Workflow secrets (credentials for generators; encrypted at rest) ---

    async def list_workflow_secrets(self) -> list[dict[str, Any]]:
        """Names and timestamps only — never the values.

        The read path for the UI. `get_workflow_secret_env` is the only method
        that decrypts, and only the run path calls it.
        """
        async with self.db.execute(
            "SELECT name, updated_at FROM workflow_secrets ORDER BY name"
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def set_workflow_secret(self, name: str, value: str) -> None:
        await self.db.execute(
            """INSERT INTO workflow_secrets (name, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                   value = excluded.value,
                   updated_at = excluded.updated_at""",
            (name, secretbox.encrypt(value), _now()),
        )
        await self.db.commit()

    async def delete_workflow_secret(self, name: str) -> bool:
        cursor = await self.db.execute(
            "DELETE FROM workflow_secrets WHERE name = ?", (name,)
        )
        await self.db.commit()
        return bool(cursor.rowcount)

    async def get_workflow_secret_env(self) -> dict[str, str]:
        """Decrypted name -> value, for injecting into a generator's env."""
        async with self.db.execute(
            "SELECT name, value FROM workflow_secrets"
        ) as cursor:
            rows = await cursor.fetchall()
        return {r["name"]: (secretbox.decrypt(r["value"]) or "") for r in rows}

    async def has_prior_activity(self) -> bool:
        """True if this workspace was already in use before now.

        Used by the onboarding funnel (services/onboarding_metrics.py) to tell
        a genuinely new user from an existing one whose DB predates the
        instrumentation. Without the distinction, an upgrading user whose runs
        succeeded months ago records first-seen and first-success within
        milliseconds of each other and reports a ~0s time-to-first-success,
        which would quietly wreck the very baseline the metric exists to
        establish.

        Deliberately cheap and conservative: a single EXISTS against the two
        tables that only get rows once someone has actually done something.
        """
        async with self.db.execute(
            """SELECT EXISTS(SELECT 1 FROM workflow_runs)
                   OR EXISTS(SELECT 1 FROM chat_messages) AS used"""
        ) as cursor:
            row = await cursor.fetchone()
        return bool(row["used"]) if row else False
