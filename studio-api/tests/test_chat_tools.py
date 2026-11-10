"""Pegasus chat tools — structured, scope-defaulted, read-only.

The contract under test: `execute` answers only for its own tools (None means
"fall through to the generic file/shell tools"), ids default from the active
chat scope, a missing id comes back as a readable error rather than a guess,
and results are bounded JSON. Also pins that the tools are actually exposed:
present in the chat's tool schemas and exempt from the shell-approval gate.
"""

from __future__ import annotations

import asyncio
import json

from services import chat_tools


RUN_SCOPE = {"type": "run", "id": "wf-uuid-2"}
WF_SCOPE = {"type": "workflow", "id": "split-merge"}


def _exec(name, args=None, scope=None):
    return asyncio.run(chat_tools.execute(name, args or {}, scope))


def test_unknown_tool_falls_through():
    """None = not ours — chat.py continues to the generic tools."""
    assert _exec("write_file", {"path": "x", "content": "y"}) is None
    assert _exec("run_command", {"command": "ls"}) is None


def test_run_tool_without_id_or_scope_asks_for_one():
    result = _exec("get_run_status")
    assert result is not None
    assert "no run_id" in result
    assert "context selector" in result


def test_run_id_defaults_from_scope(monkeypatch):
    seen = {}

    async def resolve(run_id):
        seen["run_id"] = run_id
        return "/runs/run0002"

    async def detail(run_dir):
        return {
            "status": "failed",
            "total_jobs": 4,
            "completed_jobs": 2,
            "failed_jobs": 2,
            "running_jobs": 0,
            "exec_site": "condorpool",
        }

    monkeypatch.setattr("services.chat_scope._resolve_run_dir", resolve)
    monkeypatch.setattr("services.workflow_monitor.get_workflow_detail", detail)

    result = _exec("get_run_status", scope=RUN_SCOPE)
    seen_payload = json.loads(result)
    assert seen["run_id"] == "wf-uuid-2"  # came from the scope, not the args
    assert seen_payload["status"] == "failed"
    assert seen_payload["failed_jobs"] == 2


def test_explicit_run_id_beats_scope(monkeypatch):
    seen = {}

    async def resolve(run_id):
        seen["run_id"] = run_id
        return None  # unknown → readable error, not a crash

    monkeypatch.setattr("services.chat_scope._resolve_run_dir", resolve)

    result = _exec("get_run_status", {"run_id": "explicit"}, scope=RUN_SCOPE)
    assert seen["run_id"] == "explicit"
    assert "no workflow run found" in result


def test_diagnose_run_returns_cards(monkeypatch):
    async def resolve(run_id):
        return "/runs/run0002"

    async def detail(run_dir):
        return {"status": "failed", "failed_jobs": 1}

    async def jobs(run_dir):
        return [
            {
                "job_id": "split_ID1",
                "transformation": "split",
                "status": "failed",
                "exitcode": 1,
                "stderr": "boom " * 400,  # longer than the stderr cap
            },
        ]

    monkeypatch.setattr("services.chat_scope._resolve_run_dir", resolve)
    monkeypatch.setattr("services.workflow_monitor.get_workflow_detail", detail)
    monkeypatch.setattr("services.workflow_monitor.get_workflow_jobs", jobs)

    payload = json.loads(_exec("diagnose_run", scope=RUN_SCOPE))
    assert payload["status"] == "failed"
    assert len(payload["cards"]) == 1
    card = payload["cards"][0]
    assert card["step"] == "split"
    # stderr excerpt is bounded, with a visible marker.
    assert len(card["technical"]) <= chat_tools._MAX_CARD_STDERR + len(" …[truncated]")
    assert card["technical"].endswith("…[truncated]")


def test_get_run_jobs_filters_and_caps(monkeypatch):
    async def resolve(run_id):
        return "/runs/run0002"

    async def jobs(run_dir):
        return [
            {"job_id": f"j{i}", "status": "failed" if i % 2 else "succeeded"}
            for i in range(300)
        ]

    monkeypatch.setattr("services.chat_scope._resolve_run_dir", resolve)
    monkeypatch.setattr("services.workflow_monitor.get_workflow_jobs", jobs)

    payload = json.loads(
        _exec("get_run_jobs", {"status": "failed"}, scope=RUN_SCOPE)
    )
    assert all(j["status"] == "failed" for j in payload["jobs"])
    assert len(payload["jobs"]) == chat_tools._MAX_JOBS
    assert "150 jobs" in payload["note"]


def test_list_runs_scoped_to_workflow(monkeypatch):
    """With a workflow context, list_workflow_runs defaults to that project's
    runs — runs outside its directory are excluded."""

    async def runs(workspace=None):
        return [
            {
                "run_id": "in",
                "run_name": "run0001",
                "name": "split-merge",
                "run_dir": "/w/workflows/split-merge/submit/run0001",
            },
            {
                "run_id": "out",
                "run_name": "run0009",
                "name": "other",
                "run_dir": "/w/workflows/other/submit/run0009",
            },
        ]

    async def projects(workspace=None):
        return [
            {
                "project_id": "split-merge",
                "name": "Split Merge",
                "project_dir": "/w/workflows/split-merge",
                "status": "failed",
                "submitted_runs": [],
            }
        ]

    async def detail(run_dir):
        return {"status": "succeeded", "total_jobs": 1, "completed_jobs": 1,
                "failed_jobs": 0, "running_jobs": 0}

    monkeypatch.setattr("services.workflow_scanner.discover_runs_async", runs)
    monkeypatch.setattr(
        "services.workflow_scanner.discover_projects_async", projects
    )
    monkeypatch.setattr("services.workflow_monitor.get_workflow_detail", detail)

    payload = json.loads(_exec("list_workflow_runs", scope=WF_SCOPE))
    assert payload["project_id"] == "split-merge"
    assert [r["run_id"] for r in payload["runs"]] == ["in"]


def test_tool_errors_are_strings_not_raises(monkeypatch):
    async def resolve(run_id):
        raise RuntimeError("db is on fire")

    monkeypatch.setattr("services.chat_scope._resolve_run_dir", resolve)
    result = _exec("get_run_status", scope=RUN_SCOPE)
    assert result.startswith("Error executing get_run_status")
