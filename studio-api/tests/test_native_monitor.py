"""Hold and idle diagnosis from the native pegasus-monitor's --json (#662).

The documents under fixtures/pegasus_monitor are real output (see the README
there). What is pinned here is what a trial against real workflows showed
Studio has to get right: a hold is reported from the scheduler before the
stampede DB knows about it, a run too young to have state is "starting" and
not an error, a pool that was not queried is "not known" and not empty, and
no usable tool means the feature is inert.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

from services import chat_tools, native_monitor

FIXTURES = Path(__file__).parent / "fixtures" / "pegasus_monitor"


def _doc(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    native_monitor.reset_for_tests()
    monkeypatch.delenv("STUDIO_NATIVE_MONITOR", raising=False)
    yield
    native_monitor.reset_for_tests()


def _install_tool(
    tmp_path, monkeypatch, *, help_text="  --json   machine output", body=""
):
    """Put a stand-in ``pegasus-monitor`` first on PATH; returns its call log."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    calls = tmp_path / "calls.log"
    tool = bindir / "pegasus-monitor"
    tool.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "--help" ]; then echo "{help_text}"; exit 0; fi\n'
        f'echo "$@" >> "{calls}"\n'
        f"{body}\n"
    )
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return calls


# ---- reduce(): real documents ------------------------------------------------


def test_held_job_is_classified_before_the_stampede_db_knows():
    """The DB still said EXECUTE here; the hold comes from the HTCondor queue."""
    doc = _doc("diagnose-held-before-db.json")
    assert doc["summary"]["job_counts"]["held"] == 0  # the DB's view

    result = native_monitor.reduce(doc)
    assert result["state"] == "ok"
    assert result["diagnosis_available"] is True
    assert result["failed"] == []
    (held,) = result["held"]
    assert held["job_id"] == "proc_holdme"
    assert held["code"] == "user_hold"
    assert held["hold_reason"] == "via condor_hold (by user submit)"
    assert held["suggestions"]


def test_combined_call_reports_the_hold_and_why_the_other_job_is_idle():
    result = native_monitor.reduce(_doc("diagnose-why-idle-held-idle.json"))
    assert result["state"] == "ok"
    assert [h["job_id"] for h in result["held"]] == ["proc_holdme"]

    idle = result["idle"]
    assert idle["queued_jobs"] == 1
    (mismatch,) = idle["mismatches"]
    assert mismatch["job_id"] == "proc_idleme"
    assert mismatch["reason"] == "exceeds_single_slot_capacity"
    assert mismatch["requested_memory_mb"] == 9000000
    assert "requirements" not in mismatch  # a raw ClassAd expression
    assert idle["pool"]["total_cpus"] == 4
    assert idle["priority"]["owner"] == "submit"
    assert idle["findings"] and idle["suggestions"]
    assert result["not_checked"] == []


def test_stall_is_not_passed_on():
    """Its one-shot assessment was wrong in both directions in the trial."""
    doc = _doc("diagnose-why-idle-held-idle.json")
    assert doc["stall"] is not None
    assert "stall" not in native_monitor.reduce(doc)


def test_failed_job_finding():
    result = native_monitor.reduce(_doc("diagnose-failed.json"))
    assert result["held"] == []
    (failed,) = result["failed"]
    assert failed["job_id"] == "proc_bad"
    assert failed["code"] == "exit_code_1"
    assert failed["hold_reason"] is None
    assert result["idle"] is None  # that mode was not asked for


def test_clean_run_has_nothing_to_report():
    result = native_monitor.reduce(_doc("diagnose-success.json"))
    assert result["state"] == "ok"
    assert result["held"] == [] and result["failed"] == []
    assert result["workflow_state"] == "WORKFLOW_TERMINATED"


def test_unqueried_pool_is_unknown_not_empty():
    """Without HTCondor every pool number is null and the sources say so."""
    result = native_monitor.reduce(_doc("why-idle-unmatchable-nocondor.json"))
    assert result["idle"]["pool"] is None
    assert result["idle"]["mismatches"] == []
    assert {s["source"] for s in result["not_checked"]} >= {
        "condor_queue",
        "condor_pool",
    }
    assert {s["state"] for s in result["not_checked"]} == {"disabled"}


def test_run_with_no_recorded_state_is_starting_not_an_error():
    doc = _doc("error-no-snapshot-workflow-state-waiting.json")
    assert doc["exit_code"] == 1
    result = native_monitor.reduce(doc)
    assert result["state"] == "starting"
    assert "ask again" in result["message"]


def test_other_errors_carry_the_tools_code_and_message():
    result = native_monitor.reduce(_doc("error-nonexistent.json"))
    assert result["state"] == "error"
    assert result["code"] == "workflow_location_error"
    assert "does not exist" in result["message"]


def test_a_newer_format_version_is_not_guessed_at():
    doc = _doc("diagnose-success.json")
    doc["format_version"] = 2
    result = native_monitor.reduce(doc)
    assert result["state"] == "unavailable"
    assert "format" in result["message"]


@pytest.mark.parametrize("doc", [None, [], "text", {}, {"format": "something-else"}])
def test_anything_else_is_an_error_not_a_crash(doc):
    assert native_monitor.reduce(doc)["state"] == "error"


def test_unknown_keys_and_missing_sections_are_tolerated():
    """The contract adds keys without a version bump; sections can be null."""
    doc = _doc("diagnose-held-before-db.json")
    doc["a_new_section"] = {"x": 1}
    doc["diagnostics"]["findings"][0]["a_new_key"] = True
    doc["summary"] = None
    doc["sources"] = None
    result = native_monitor.reduce(doc)
    assert result["state"] == "ok"
    assert result["held"][0]["code"] == "user_hold"
    assert result["counts"] == {}


def test_findings_are_bounded():
    doc = _doc("diagnose-held-before-db.json")
    doc["diagnostics"]["findings"] = doc["diagnostics"]["findings"] * 80
    result = native_monitor.reduce(doc)
    assert len(result["held"]) == 50
    assert result["findings_omitted"] == 30


# ---- explain(): the process boundary ------------------------------------------


def test_no_tool_installed_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    result = asyncio.run(native_monitor.explain("/runs/run0001"))
    assert result["state"] == "unavailable"


def test_a_tool_without_json_support_is_unavailable_and_never_run(
    tmp_path, monkeypatch
):
    calls = _install_tool(tmp_path, monkeypatch, help_text="  --once   one shot")
    result = asyncio.run(native_monitor.explain("/runs/run0001"))
    assert result["state"] == "unavailable"
    assert not calls.exists()


def test_switch_turns_it_off(tmp_path, monkeypatch):
    calls = _install_tool(tmp_path, monkeypatch)
    monkeypatch.setenv("STUDIO_NATIVE_MONITOR", "0")
    assert (
        asyncio.run(native_monitor.explain("/runs/run0001"))["state"] == "unavailable"
    )
    assert not calls.exists()


def test_runs_the_tool_with_condor_queries_on_and_caches_briefly(tmp_path, monkeypatch):
    fixture = FIXTURES / "diagnose-why-idle-held-idle.json"
    calls = _install_tool(tmp_path, monkeypatch, body=f'cat "{fixture}"')

    async def twice():
        first = await native_monitor.explain("/runs/run0002")
        second = await native_monitor.explain("/runs/run0002")
        return first, second

    first, second = asyncio.run(twice())
    assert first["state"] == "ok"
    assert first["held"][0]["code"] == "user_hold"
    assert second == first

    (argv,) = calls.read_text().splitlines()  # one process for two asks
    assert argv == "--json --diagnose --why-idle /runs/run0002"
    assert "--no-condor" not in argv  # that loses hold diagnosis


def test_concurrent_asks_for_one_run_share_a_process(tmp_path, monkeypatch):
    fixture = FIXTURES / "diagnose-success.json"
    calls = _install_tool(tmp_path, monkeypatch, body=f'sleep 0.3; cat "{fixture}"')

    async def together():
        return await asyncio.gather(
            *[native_monitor.explain("/runs/run0003") for _ in range(4)]
        )

    results = asyncio.run(together())
    assert all(r["state"] == "ok" for r in results)
    assert len(calls.read_text().splitlines()) == 1


def test_error_document_with_exit_1_is_still_read(tmp_path, monkeypatch):
    fixture = FIXTURES / "error-no-snapshot-workflow-state-waiting.json"
    _install_tool(tmp_path, monkeypatch, body=f'cat "{fixture}"; exit 1')
    assert asyncio.run(native_monitor.explain("/runs/new"))["state"] == "starting"


def test_garbage_output_is_an_error_with_the_tools_stderr(tmp_path, monkeypatch):
    _install_tool(tmp_path, monkeypatch, body='echo "Traceback: boom" >&2; exit 3')
    result = asyncio.run(native_monitor.explain("/runs/run0004"))
    assert result["state"] == "error"
    assert result["code"] == "bad_output"
    assert "boom" in result["message"]


def test_a_hung_tool_is_killed_and_reported(tmp_path, monkeypatch):
    _install_tool(tmp_path, monkeypatch, body="sleep 30")
    monkeypatch.setattr(native_monitor, "_TIMEOUT_S", 0.3)
    result = asyncio.run(native_monitor.explain("/runs/run0005"))
    assert result["state"] == "error"
    assert result["code"] == "timeout"


# ---- the two ways in -----------------------------------------------------------


def test_chat_tool_returns_the_reduced_result_with_a_hint(monkeypatch):
    async def resolve(run_id):
        return "/runs/run0002"

    async def explain(run_dir):
        assert run_dir == "/runs/run0002"
        return native_monitor.reduce(_doc("diagnose-why-idle-held-idle.json"))

    monkeypatch.setattr("services.chat_scope._resolve_run_dir", resolve)
    monkeypatch.setattr(native_monitor, "explain", explain)

    out = asyncio.run(
        chat_tools.execute(
            "explain_run_waiting", {}, {"type": "run", "id": "wf-uuid-2"}
        )
    )
    payload = json.loads(out)
    assert payload["state"] == "ok"
    assert payload["held"][0]["job_id"] == "proc_holdme"
    assert "not_checked" in payload["hint"]


def test_chat_tool_tells_the_model_what_to_do_when_unavailable(monkeypatch):
    async def resolve(run_id):
        return "/runs/run0002"

    monkeypatch.setattr("services.chat_scope._resolve_run_dir", resolve)
    monkeypatch.setenv("STUDIO_NATIVE_MONITOR", "0")

    out = asyncio.run(chat_tools.execute("explain_run_waiting", {"run_id": "x"}, None))
    payload = json.loads(out)
    assert payload["state"] == "unavailable"
    assert "diagnose_run" in payload["hint"]


def test_endpoint_always_answers_with_a_state(monkeypatch):
    from routers import workflows

    async def resolve(run_id):
        return "/runs/run0002"

    async def explain(run_dir):
        return {"state": "starting", "message": "too new"}

    monkeypatch.setattr(workflows, "_resolve_run_dir", resolve)
    monkeypatch.setattr(native_monitor, "explain", explain)

    body = asyncio.run(workflows.explain_waiting("wf-uuid-2"))
    assert body == {"run_id": "wf-uuid-2", "state": "starting", "message": "too new"}
