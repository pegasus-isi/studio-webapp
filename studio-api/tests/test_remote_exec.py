"""Running the planner on the submit host.

Pegasus bakes the planner's own absolute paths into the submit files it
writes, and its local-universe auxiliary jobs then execute those on the schedd
host. Planning in the pod against a remote schedd therefore produces a DAG
referring to files only the pod has, which is what broke every workflow on
2026-09-02 (#334).

These tests cover the mechanism and the decision of where to plan. They stub
HTCondor: the mechanism itself was verified against the live cluster by
planning the earthquake workflow through it: 14 of the generated submit files
referenced the submit host's python3.13 tree and zero referenced the pod's
python3.12.
"""

from __future__ import annotations

import asyncio
import os
import shlex

import pytest

import services.remote_exec as rx
import services.run_flow as rf


def test_the_wrapper_cds_and_records_an_exit_code(tmp_path):
    text = rx._wrapper_text(
        ["pegasus-plan", "-s", "condorpool", "wf.yml"],
        str(tmp_path / "proj"),
        str(tmp_path / "rc"),
    )
    assert text.startswith("#!/bin/bash")
    assert f"cd {shlex.quote(str(tmp_path / 'proj'))}" in text
    assert "pegasus-plan -s condorpool wf.yml" in text
    # The exit code goes to a file: reading it from the job log would mean
    # parsing HTCondor's event format.
    assert f"echo $rc > {shlex.quote(str(tmp_path / 'rc'))}" in text


def test_a_cd_failure_still_writes_an_exit_code(tmp_path):
    """Otherwise a bad directory looks identical to a held job."""
    text = rx._wrapper_text(["true"], "/does/not/exist", str(tmp_path / "rc"))
    assert "echo 127 >" in text


def test_paths_with_spaces_are_quoted(tmp_path):
    text = rx._wrapper_text(["pegasus-plan", "my workflow.yml"], "/a dir", "/rc")
    assert "'my workflow.yml'" in text
    assert "'/a dir'" in text


def test_the_submit_file_never_inherits_the_pods_environment():
    """`getenv = true` would re-import what remote planning is escaping.

    The pod exports PEGASUS_PYTHON, and could export PYTHONPATH; either would
    point the submit host's planner back at the pod's Pegasus.
    """
    sub = rx._submit_text(
        wrapper="/w.sh", out="/o", err="/e", logf="/l", home="/home/u", user="u"
    )
    assert "getenv = false" in sub
    assert "universe = local" in sub
    assert "PEGASUS_PYTHON" not in sub
    assert "PYTHONPATH" not in sub
    # Shared storage: HTCondor must not stage anything.
    assert "should_transfer_files = NO" in sub


def test_a_missing_exit_code_file_is_a_failure_not_a_success(tmp_path, monkeypatch):
    """A held or evicted job leaves no rc file. Empty must not read as 0."""

    async def fake_run(cmd, timeout):
        if cmd[0] == "condor_submit":
            return 0, "1 job(s) submitted to cluster 42.", ""
        return 0, "", ""  # condor_wait "succeeds", but the wrapper never ran

    monkeypatch.setattr(rx, "_run", fake_run)
    monkeypatch.setattr(rx, "available", lambda: True)
    rc, out = asyncio.run(
        rx.run_on_submit_host(["true"], cwd=str(tmp_path), scratch_dir=str(tmp_path))
    )
    assert rc == 125
    assert "did not report an exit code" in out


def test_exit_code_and_output_come_back(tmp_path, monkeypatch):
    scratch = tmp_path / "s"

    async def fake_run(cmd, timeout):
        if cmd[0] == "condor_submit":
            # Emulate the job: write what the wrapper would have written.
            stamp = next(p for p in scratch.glob("remote-*.sub")).stem
            (scratch / f"{stamp}.out").write_text("I have concretized your workflow\n")
            (scratch / f"{stamp}.err").write_text("a warning\n")
            (scratch / f"{stamp}.rc").write_text("0\n")
            return 0, "1 job(s) submitted to cluster 7.", ""
        return 0, "", ""

    monkeypatch.setattr(rx, "_run", fake_run)
    monkeypatch.setattr(rx, "available", lambda: True)
    rc, out = asyncio.run(
        rx.run_on_submit_host(["pegasus-plan"], cwd=str(tmp_path), scratch_dir=str(scratch))
    )
    assert rc == 0
    assert "concretized" in out and "a warning" in out


def test_scratch_is_tidied_on_success_and_kept_on_failure(tmp_path, monkeypatch):
    """Six files per invocation land in the user's project directory.

    On failure they stay: the submit file and the job log are what someone
    debugging a failed plan actually needs.
    """

    def driver(rc_value):
        scratch = tmp_path / f"s{rc_value}"

        async def fake_run(cmd, timeout):
            if cmd[0] == "condor_submit":
                stamp = next(p for p in scratch.glob("remote-*.sub")).stem
                (scratch / f"{stamp}.out").write_text("out\n")
                (scratch / f"{stamp}.err").write_text("")
                (scratch / f"{stamp}.rc").write_text(f"{rc_value}\n")
                # HTCondor writes the job log, not us — the stub has to as
                # well, or the "kept on failure" assertion tests nothing.
                (scratch / f"{stamp}.log").write_text("005 (9.000.000) Job terminated.\n")
                return 0, "submitted to cluster 9.", ""
            return 0, "", ""

        monkeypatch.setattr(rx, "_run", fake_run)
        monkeypatch.setattr(rx, "available", lambda: True)
        rc, _ = asyncio.run(
            rx.run_on_submit_host(["x"], cwd=str(tmp_path), scratch_dir=str(scratch))
        )
        return rc, sorted(p.name for p in scratch.iterdir())

    rc, left = driver(0)
    assert rc == 0 and left == [], f"success should leave nothing, left {left}"

    rc, left = driver(3)
    assert rc == 3 and left, "failure must keep the submit file and job log"
    assert any(n.endswith(".sub") for n in left)
    assert any(n.endswith(".log") for n in left)


def test_submit_failure_is_unavailable_not_a_command_failure(tmp_path, monkeypatch):
    """The mechanism being broken is different from the planner failing.

    run_flow falls back to local planning on RemoteExecUnavailable; it must not
    do that for a genuine planner error.
    """

    async def fake_run(cmd, timeout):
        return 1, "", "ERROR: Failed to connect to schedd"

    monkeypatch.setattr(rx, "_run", fake_run)
    monkeypatch.setattr(rx, "available", lambda: True)
    with pytest.raises(rx.RemoteExecUnavailable):
        asyncio.run(
            rx.run_on_submit_host(["true"], cwd=str(tmp_path), scratch_dir=str(tmp_path))
        )


def test_missing_condor_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(rx, "available", lambda: False)
    with pytest.raises(rx.RemoteExecUnavailable):
        asyncio.run(
            rx.run_on_submit_host(["true"], cwd=str(tmp_path), scratch_dir=str(tmp_path))
        )


# --- where to plan -------------------------------------------------------


def _mode(monkeypatch, *, setting=None, remote, condor=True):
    if setting is None:
        monkeypatch.delenv("STUDIO_PLAN_ON_SUBMIT_HOST", raising=False)
    else:
        monkeypatch.setenv("STUDIO_PLAN_ON_SUBMIT_HOST", setting)

    async def fake_is_remote():
        return remote

    monkeypatch.setattr(rf.submit_host, "is_remote", fake_is_remote)
    monkeypatch.setattr(rf.remote_exec, "available", lambda: condor)
    return asyncio.run(rf._plan_mode())


def test_auto_plans_remotely_when_the_schedd_is_elsewhere(monkeypatch):
    assert _mode(monkeypatch, remote=True) == "remote"


def test_auto_plans_locally_when_the_schedd_is_this_machine(monkeypatch):
    """A native or Open OnDemand deployment is already co-located.

    Remoting a job to yourself would add a queue round-trip for nothing.
    """
    assert _mode(monkeypatch, remote=False) == "local"


def test_auto_plans_locally_when_it_cannot_tell(monkeypatch):
    """Undetermined keeps today's behaviour rather than guessing."""
    assert _mode(monkeypatch, remote=None) == "local"


def test_auto_plans_locally_without_condor(monkeypatch):
    assert _mode(monkeypatch, remote=True, condor=False) == "local"


def test_the_setting_can_force_either_way(monkeypatch):
    assert _mode(monkeypatch, setting="never", remote=True) == "local"
    assert _mode(monkeypatch, setting="always", remote=False) == "remote"


# --- the preflight must not block what remote planning has fixed ----------


def _execute_with(monkeypatch, *, compat_status, plan_mode):
    """Drive run_flow._execute far enough to see whether it blocks."""
    flow = rf.RunFlow(
        flow_id="f1",
        project_id="p1",
        stages=[rf.Stage(key="plan", label="Checking the plan"),
                rf.Stage(key="submit", label="Sending it to run")],
    )

    async def fake_check():
        return rf.submit_host.Compat(
            status=compat_status,
            detail="different places",
            local_share_dir="/x/python3.12/site-packages/Pegasus/data",
            remote_share_dir="/x/python3.13/site-packages/Pegasus/data",
            schedd="ap.pegasus-ai.org",
        )

    async def fake_mode():
        return plan_mode

    async def fake_plan(*a, **k):
        return False  # stop right after the preflight

    monkeypatch.setattr(rf.submit_host, "check", fake_check)
    monkeypatch.setattr(rf, "_plan_mode", fake_mode)
    monkeypatch.setattr(rf, "_run_plan", fake_plan)
    asyncio.run(
        rf._execute(
            flow,
            project_dir="/tmp",
            generator_file=None,
            generator_args=[],
            site="condorpool",
            output_site="local",
            force_build=False,
        )
    )
    return flow


def test_a_mismatch_blocks_when_planning_happens_here(monkeypatch):
    """Locally-planned submit files carry this pod's paths, so it is fatal."""
    flow = _execute_with(monkeypatch, compat_status="mismatch", plan_mode="local")
    assert flow.state == "failed"
    assert flow.failure is not None
    assert "different places" in flow.failure.cause


def test_a_mismatch_does_not_block_when_planning_happens_on_the_submit_host(
    monkeypatch,
):
    """The regression this guards against.

    #335 blocks on a mismatch; #337 makes the mismatch harmless by planning
    where the workflow is submitted. Left unconditional, the guardrail would
    refuse every run on a deployment where runs work — turning a difference
    that no longer matters into an outage.
    """
    flow = _execute_with(monkeypatch, compat_status="mismatch", plan_mode="remote")
    # It stopped because the stubbed plan stage returned False, not because
    # the preflight refused: a refusal sets `failure` before planning.
    assert flow.failure is None or "different places" not in flow.failure.cause


def test_an_unknown_verdict_never_blocks_either_way(monkeypatch):
    for mode in ("local", "remote"):
        flow = _execute_with(monkeypatch, compat_status="unknown", plan_mode=mode)
        assert flow.failure is None or "different places" not in flow.failure.cause

