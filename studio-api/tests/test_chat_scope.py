"""Workflow/run chat scope — the context block behind the panel's picker.

The contract under test: a valid scope produces a bounded, diagnosis-centric
system-prompt block; *every* failure mode (unknown type, unknown id, a service
raising) resolves to None so a chat turn can never break on context.
"""

from __future__ import annotations

import asyncio

from services import chat_scope


PROJECT = {
    "project_id": "split-merge",
    "name": "Split Merge",
    "project_dir": "/home/user/work/workflows/split-merge",
    "status": "failed",
    "has_generator": True,
    "generator_file": "workflow_generator.py",
    "has_workflow_yml": True,
    "has_container_def": False,
    "submitted_runs": [
        {
            "run_id": "wf-uuid-2",
            "run_name": "run0002",
            "name": "split-merge",
            "run_dir": "/home/user/work/workflows/split-merge/submit/run0002",
            "status": "failed",
        },
        {
            "run_id": "wf-uuid-1",
            "run_name": "run0001",
            "name": "split-merge",
            "run_dir": "/home/user/work/workflows/split-merge/submit/run0001",
            "status": "succeeded",
        },
    ],
}


def _build(scope_type, scope_id):
    return asyncio.run(chat_scope.build_scope_block(scope_type, scope_id))


def test_invalid_scope_returns_none():
    assert _build(None, None) is None
    assert _build("slice", "x") is None
    assert _build("workflow", "") is None
    assert _build("run", 42) is None


def test_unknown_workflow_returns_none(monkeypatch):
    async def no_projects(workspace=None):
        return []

    monkeypatch.setattr(
        "services.workflow_scanner.discover_projects_async", no_projects
    )
    assert _build("workflow", "nope") is None


def test_resolver_exception_is_soft(monkeypatch):
    """A service blowing up must degrade to None, not propagate — the chat
    turn continues unscoped."""

    async def boom(workspace=None):
        raise RuntimeError("scanner exploded")

    monkeypatch.setattr(
        "services.workflow_scanner.discover_projects_async", boom
    )
    assert _build("workflow", "split-merge") is None


def test_workflow_scope_block(monkeypatch, tmp_path):
    project = dict(PROJECT, project_dir=str(tmp_path))
    (tmp_path / "README.md").write_text("Splits a file and merges the parts.")

    async def projects(workspace=None):
        return [project]

    monkeypatch.setattr(
        "services.workflow_scanner.discover_projects_async", projects
    )

    block = _build("workflow", "split-merge")
    assert block is not None
    assert block.startswith("## Active Context")
    assert "Workflow: Split Merge" in block
    assert "workflow_generator.py" in block
    assert "run0002 — failed" in block
    assert "Splits a file and merges the parts." in block
    # The behavioral instruction rides with the data.
    assert "resolve bare references" in block


def test_run_scope_block_leads_with_diagnosis(monkeypatch, tmp_path):
    run_dir = tmp_path / "workflows" / "split-merge" / "submit" / "run0002"
    run_dir.mkdir(parents=True)
    project = dict(PROJECT, project_dir=str(tmp_path / "workflows" / "split-merge"))

    async def resolve(run_id):
        return str(run_dir)

    async def detail(rd):
        return {
            "status": "failed",
            "total_jobs": 10,
            "completed_jobs": 7,
            "failed_jobs": 2,
            "running_jobs": 0,
            "exec_site": "condorpool",
        }

    async def jobs(rd):
        return [
            {
                "job_id": "split_ID0000001",
                "transformation": "split",
                "status": "failed",
                "exitcode": 1,
                "stderr": "split: cannot open 'input.txt' for reading",
            },
            {"job_id": "ok_ID2", "status": "succeeded", "exitcode": 0},
        ]

    async def projects(workspace=None):
        return [project]

    monkeypatch.setattr(chat_scope, "_resolve_run_dir", resolve)
    monkeypatch.setattr(
        "services.workflow_monitor.get_workflow_detail", detail
    )
    monkeypatch.setattr("services.workflow_monitor.get_workflow_jobs", jobs)
    monkeypatch.setattr(
        "services.workflow_scanner.discover_projects_async", projects
    )

    block = _build("run", "wf-uuid-2")
    assert block is not None
    assert "Run: run0002" in block
    assert "Run id: wf-uuid-2" in block
    assert "10 total, 7 completed, 2 failed, 0 running" in block
    # Diagnosis is the point: the failed step and its stderr are present,
    # classified by run_diagnosis into cause/action form.
    assert "Diagnosed failures" in block
    assert "**split**" in block
    assert "cannot open 'input.txt'" in block
    # The parent workflow context rides along with a run scope.
    assert "Parent Workflow: Split Merge" in block


def test_run_scope_unknown_run(monkeypatch):
    async def resolve(run_id):
        return None

    monkeypatch.setattr(chat_scope, "_resolve_run_dir", resolve)
    assert _build("run", "missing") is None


def test_block_is_bounded(monkeypatch, tmp_path):
    """One pathological project (a giant run list) can't blow the budget."""
    project = dict(
        PROJECT,
        project_dir=str(tmp_path),
        submitted_runs=[
            {
                "run_id": f"uuid-{i}",
                "run_name": f"run{i:04d}",
                "status": "failed",
                "run_dir": "x" * 500,
            }
            for i in range(500)
        ],
    )

    async def projects(workspace=None):
        return [project]

    monkeypatch.setattr(
        "services.workflow_scanner.discover_projects_async", projects
    )

    block = _build("workflow", "split-merge")
    assert block is not None
    assert len(block) <= chat_scope._MAX_BLOCK_CHARS + len(" …[truncated]")
    # The run list is capped, not dumped.
    assert "and 495 more" in block
