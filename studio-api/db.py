"""SQLite state persistence for studio-api via aiosqlite."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import structlog

log = structlog.get_logger()

def _default_workspace() -> str:
    explicit = os.environ.get("STUDIO_WORKSPACE_DIR")
    if explicit:
        return explicit
    home = os.environ.get("HOME")
    if home:
        return os.path.join(home, "work")
    return "/tmp/studio-work"

_DEFAULT_DB_DIR = os.path.join(_default_workspace(), ".studio")
_DEFAULT_DB_PATH = os.path.join(_DEFAULT_DB_DIR, "studio.db")

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
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    agent_id        TEXT,
    tool_calls      TEXT,
    created_at      TEXT NOT NULL
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
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        log.info("database_connected", path=self._path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Database not connected"
        return self._db

    # --- LLM Config ---

    async def get_llm_config(self) -> dict[str, Any] | None:
        async with self.db.execute(
            "SELECT * FROM llm_config WHERE id = 1"
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return {
                "provider": row["provider"],
                "model": row["model"],
                "api_key": row["api_key"],
                "base_url": row["base_url"],
                "extra_config": json.loads(row["extra_config"] or "{}"),
                "updated_at": row["updated_at"],
            }

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
                    "api_key": row["api_key"],
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
                "api_key": row["api_key"],
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
            await self.set_llm_config(
                provider=config["provider_id"],
                model=config["default_model"] or None,
                api_key=config["api_key"] or None,
                base_url=config["base_url"] or None,
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
        await self.db.execute(
            """INSERT INTO workflow_runs
               (run_id, name, run_dir, status, total_jobs, completed_jobs,
                failed_jobs, exec_site, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_dir) DO UPDATE SET
                   run_id = excluded.run_id,
                   name = excluded.name,
                   status = excluded.status,
                   total_jobs = excluded.total_jobs,
                   completed_jobs = excluded.completed_jobs,
                   failed_jobs = excluded.failed_jobs,
                   exec_site = excluded.exec_site,
                   updated_at = excluded.updated_at""",
            (run_id, name, run_dir, status, total_jobs, completed_jobs,
             failed_jobs, exec_site, now, now),
        )
        await self.db.commit()

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
        tool_calls: dict[str, Any] | None = None,
    ) -> int:
        now = _now()
        async with self.db.execute(
            """INSERT INTO chat_messages (role, content, agent_id, tool_calls, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (role, content, agent_id, json.dumps(tool_calls) if tool_calls else None, now),
        ) as cursor:
            msg_id = cursor.lastrowid
        await self.db.commit()
        return msg_id or 0

    async def get_chat_history(self, limit: int = 100) -> list[dict[str, Any]]:
        results = []
        async with self.db.execute(
            "SELECT * FROM chat_messages ORDER BY id DESC LIMIT ?", (limit,)
        ) as cursor:
            async for row in cursor:
                results.append({
                    "id": row["id"],
                    "role": row["role"],
                    "content": row["content"],
                    "agent_id": row["agent_id"],
                    "tool_calls": json.loads(row["tool_calls"]) if row["tool_calls"] else None,
                    "created_at": row["created_at"],
                })
        results.reverse()
        return results
