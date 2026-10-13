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
        wrapper="/w.sh",
        out="/o",
        err="/e",
        logf="/l",
        home="/home/u",
        user="u",
        initialdir="/home/u/proj/.studio/remote",
    )
    assert "getenv = false" in sub
    assert "universe = local" in sub
    assert "PEGASUS_PYTHON" not in sub
    assert "PYTHONPATH" not in sub
    # Shared storage: HTCondor must not stage anything.
    assert "should_transfer_files = NO" in sub


def test_initialdir_is_always_set_to_shared_storage():
    """Without it HTCondor inherits the submitting process's cwd.

    studio-api runs from /opt/studio-api, which exists only in the pod, so
    every job was held with "Cannot access initial working directory" (#341).
    """
    sub = rx._submit_text(
        wrapper="/w.sh",
        out="/o",
        err="/e",
        logf="/l",
        home="/home/u",
        user="u",
        initialdir="/home/u/proj/.studio/remote",
    )
    assert "initialdir = /home/u/proj/.studio/remote" in sub


def test_a_held_job_fails_fast_with_its_reason(tmp_path, monkeypatch):
    """condor_wait returns on termination but not on hold.

    A held job never terminates, so waiting on it burned the full 30-minute
    timeout with no explanation — which is how #341 presented.
    """

    async def fake_run(cmd, timeout):
        if cmd[0] == "condor_submit":
            stamp = next(p for p in tmp_path.glob("remote-*.sub")).stem
            (tmp_path / f"{stamp}.log").write_text(
                "000 (55.000.000) Job submitted.\n012 (55.000.000) Job was held.\n"
            )
            return 0, "1 job(s) submitted to cluster 55.", ""
        if cmd[0] == "condor_q":
            return 0, 'Cannot access initial working directory "/opt/studio-api"\n', ""
        return 0, "", ""

    monkeypatch.setattr(rx, "_run", fake_run)
    monkeypatch.setattr(rx, "available", lambda: True)
    rc, out = asyncio.run(
        rx.run_on_submit_host(
            ["pegasus-plan"], cwd=str(tmp_path), scratch_dir=str(tmp_path), timeout=30
        )
    )
    assert rc == 126
    assert "refused to run" in out
    # The schedd's own explanation is usually the whole answer.
    assert "initial working directory" in out


def test_a_missing_exit_code_file_is_a_failure_not_a_success(tmp_path, monkeypatch):
    """A held or evicted job leaves no rc file. Empty must not read as 0."""

    async def fake_run(cmd, timeout):
        if cmd[0] == "condor_submit":
            # The job ran and terminated, but the wrapper never reached its
            # last line — so there is no rc file.
            stamp = next(p for p in tmp_path.glob("remote-*.sub")).stem
            (tmp_path / f"{stamp}.log").write_text("005 (42.000.000) Job terminated.\n")
            return 0, "1 job(s) submitted to cluster 42.", ""
        return 0, "", ""

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
            (scratch / f"{stamp}.log").write_text("005 (7.000.000) Job terminated.\n")
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


def _mode(monkeypatch, *, setting=None, remote, condor=True, stored="auto"):
    """Resolve the plan mode with everything it consults faked out.

    `stored` is the user's setting, `setting` the deployment env var.
    """
    if setting is None:
        monkeypatch.delenv("STUDIO_PLAN_ON_SUBMIT_HOST", raising=False)
    else:
        monkeypatch.setenv("STUDIO_PLAN_ON_SUBMIT_HOST", setting)

    async def fake_is_remote():
        return remote

    async def fake_stored():
        return stored

    monkeypatch.setattr(rf.submit_host, "is_remote", fake_is_remote)
    monkeypatch.setattr(rf.remote_exec, "available", lambda: condor)
    monkeypatch.setattr(rf, "_plan_mode_setting", fake_stored)
    return asyncio.run(rf._plan_mode())


def _mode_with_source(monkeypatch, **kw):
    _mode(monkeypatch, **kw)  # applies the same patches
    return asyncio.run(rf._resolve_plan_mode())


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


# --- the user's own setting (Settings > Pegasus Options) ------------------


def test_the_user_setting_can_disable_remote_planning(monkeypatch):
    """The point of the switch: try local planning against a remote schedd.

    Detection says remote and would be right by default; the user asking for
    local is asking a question detection cannot answer.
    """
    assert _mode(monkeypatch, stored="local", remote=True) == "local"


def test_the_user_setting_can_force_remote_planning(monkeypatch):
    assert _mode(monkeypatch, stored="remote", remote=False) == "remote"


def test_the_user_setting_beats_the_deployment_env_var(monkeypatch):
    """A single-user workspace: the person in it gets the last word.

    An operator default of "always" must not make the switch a no-op, or
    turning it off would silently do nothing.
    """
    assert _mode(monkeypatch, stored="local", setting="always", remote=True) == "local"
    assert _mode(monkeypatch, stored="remote", setting="never", remote=True) == "remote"


def test_auto_stored_falls_through_to_detection(monkeypatch):
    assert _mode(monkeypatch, stored="auto", remote=True) == "remote"


def test_the_resolver_reports_where_the_answer_came_from(monkeypatch):
    """The source is what lets a failure name the right cause."""
    assert _mode_with_source(monkeypatch, stored="local", remote=True) == (
        "local",
        "setting",
    )
    assert _mode_with_source(monkeypatch, setting="always", remote=False) == (
        "remote",
        "env",
    )
    assert _mode_with_source(monkeypatch, remote=True) == ("remote", "detected")
    assert _mode_with_source(monkeypatch, remote=True, condor=False) == (
        "local",
        "no-condor",
    )


def test_an_unreadable_setting_does_not_stop_a_run(monkeypatch):
    """A settings read is not worth failing a run over — fall back to auto."""

    class Boom:
        async def get_setting(self, *a, **k):
            raise RuntimeError("database is locked")

    import sys
    import types

    fake_main = types.ModuleType("main")
    fake_main.db = Boom()
    monkeypatch.setitem(sys.modules, "main", fake_main)
    assert asyncio.run(rf._plan_mode_setting()) == "auto"


def test_a_nonsense_stored_value_is_ignored(monkeypatch):
    """A hand-edited DB must not resolve to a mode the resolver can't mean."""

    class Weird:
        async def get_setting(self, *a, **k):
            return "sideways"

    import sys
    import types

    fake_main = types.ModuleType("main")
    fake_main.db = Weird()
    monkeypatch.setitem(sys.modules, "main", fake_main)
    assert asyncio.run(rf._plan_mode_setting()) == "auto"


# --- the preflight must not block what remote planning has fixed ----------


def _execute_with(
    monkeypatch, *, compat_status, plan_mode, plan_mode_source="detected"
):
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
        return plan_mode, plan_mode_source

    async def fake_plan(*a, **k):
        return False  # stop right after the preflight

    monkeypatch.setattr(rf.submit_host, "check", fake_check)
    monkeypatch.setattr(rf, "_resolve_plan_mode", fake_mode)
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
    # Nobody chose this, so the advice is the operator one.
    assert "administrator" in flow.failure.action


def test_a_block_the_user_caused_points_at_their_own_setting(monkeypatch):
    """Whose decision it was changes the only useful sentence in the failure.

    Someone who switched planning to this workspace to test it can undo that
    in Settings. Sending them to find an administrator would strand them in a
    state they chose and can leave.
    """
    flow = _execute_with(
        monkeypatch,
        compat_status="mismatch",
        plan_mode="local",
        plan_mode_source="setting",
    )
    assert flow.state == "failed"
    assert flow.failure is not None
    assert "administrator" not in flow.failure.action
    assert "Pegasus Options" in flow.failure.action
    assert "Automatic" in flow.failure.action


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



def test_the_job_sees_studios_home_from_java_too():
    """#489: the JVM takes user.home from passwd, not $HOME, so pegasus-plan on
    the submit host read the user's real ~/.pegasusrc and ignored Studio's."""
    sub = rx._submit_text(
        wrapper="/w.sh",
        out="/o",
        err="/e",
        logf="/l",
        home="/home/u/pegasus-studio",
        user="u",
        initialdir="/home/u/pegasus-studio/proj/.studio/remote",
    )
    env = next(line for line in sub.splitlines() if line.startswith("environment"))
    assert "HOME=/home/u/pegasus-studio " in env
    assert "JAVA_TOOL_OPTIONS=-Duser.home=/home/u/pegasus-studio" in env


def _finishing_job(scratch, calls):
    async def fake_run(cmd, timeout):
        calls.append(cmd[0])
        if cmd[0] == "condor_submit":
            stamp = next(p for p in scratch.glob("remote-*.sub")).stem
            (scratch / f"{stamp}.rc").write_text("0\n")
            (scratch / f"{stamp}.log").write_text("005 (9.000.000) Job terminated.\n")
            return 0, "1 job(s) submitted to cluster 9.", ""
        return 0, "", ""

    return fake_run


def test_the_schedd_is_asked_to_start_the_job_at_once(tmp_path, monkeypatch):
    """#488/#494: local-universe jobs otherwise wait for SCHEDD_INTERVAL."""
    calls: list[str] = []
    monkeypatch.setattr(rx, "_run", _finishing_job(tmp_path, calls))
    monkeypatch.setattr(rx, "available", lambda: True)
    monkeypatch.setattr(rx.schedd, "nudge", lambda *a, **k: calls.append("nudge"))
    rc, _ = asyncio.run(
        rx.run_on_submit_host(["true"], cwd=str(tmp_path), scratch_dir=str(tmp_path))
    )
    assert rc == 0
    assert calls.index("nudge") == calls.index("condor_submit") + 1
