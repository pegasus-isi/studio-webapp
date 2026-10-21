"""The shared-filesystem check before planning (#520)."""

from __future__ import annotations

import asyncio
import os

import pytest

from services import shared_fs

_POOL_SITES = """\
pegasus: "5.0"
sites:
- name: condorpool
  profiles:
    pegasus: {style: condor}
  directories:
  - type: sharedScratch
    path: /home/u/proj/scratch
    sharedFileSystem: true
    fileServers:
    - {url: "file:///home/u/proj/scratch", operation: all}
"""

_SLURM_SITES = """\
pegasus: "5.0"
sites:
- name: compute
  profiles:
    pegasus: {style: glite, data.configuration: nonsharedfs}
    condor: {grid_resource: batch slurm}
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("STUDIO_EXEC_SITE", raising=False)
    p = home / "workflows" / "af3"
    p.mkdir(parents=True)
    return p


def _problem(project, site="condorpool", **kw):
    return shared_fs.problem(
        str(project), site, environ={}, pegasusrc=project / "no-rc", **kw
    )


def test_a_portable_condorio_workflow_passes(project):
    (project / "sites.yml").write_text(_POOL_SITES)
    # Bypass under condorio is fine: HTCondor transfers the file from the
    # submit host. The sharedFileSystem scratch is ignored under condorio.
    (project / "pegasus.properties").write_text(
        "pegasus.data.configuration = condorio\n"
        "pegasus.transfer.bypass.input.staging = true\n"
    )
    assert _problem(project) is None


def test_sharedfs_on_a_pool_is_blocked(project):
    (project / "pegasus.properties").write_text("pegasus.data.configuration=sharedfs\n")
    msg = _problem(project)
    assert msg and "pegasus.data.configuration = sharedfs" in msg
    assert "Where it runs" in msg


def test_bypass_under_nonsharedfs_on_a_pool_is_blocked(project):
    (project / "pegasus.properties").write_text(
        "pegasus.data.configuration: nonsharedfs\n"
        "pegasus.transfer.bypass.input.staging True\n"
    )
    msg = _problem(project)
    assert msg and "bypass.input.staging" in msg


def test_shared_scratch_under_nonsharedfs_is_blocked(project):
    (project / "sites.yml").write_text(_POOL_SITES)
    msg = _problem(project, plan_properties={"pegasus.data.configuration": "nonsharedfs"})
    assert msg and "shared directory" in msg and "sharedScratch" not in msg  # path shown
    assert "/home/u/proj/scratch" in msg


def test_container_mounts_from_the_workspace_are_blocked_in_any_mode(project):
    home = os.environ["HOME"]
    (project / "workflow.yml").write_text(
        'pegasus: "5.0"\n'
        "transformationCatalog:\n"
        "  containers:\n"
        "  - name: af3\n"
        "    type: singularity\n"
        "    image: file:///x.sif\n"
        "    mounts:\n"
        f'    - "{home}/af3_db:/db:ro"\n'
        '    - "/cvmfs/data:/cvmfs:ro"\n'
        "  transformations: []\n"
        "jobs: []\n"
    )
    msg = _problem(project)
    assert msg and f"{home}/af3_db" in msg
    assert "/cvmfs" not in msg  # may exist on workers; not second-guessed


def test_a_cluster_site_is_never_blocked(project):
    (project / "sites.yml").write_text(_SLURM_SITES)
    (project / "pegasus.properties").write_text(
        "pegasus.data.configuration = sharedfs\n"
        "pegasus.transfer.bypass.input.staging = true\n"
    )
    assert _problem(project, site="compute") is None


def test_local_is_never_blocked(project):
    (project / "pegasus.properties").write_text("pegasus.data.configuration = sharedfs\n")
    assert _problem(project, site="local") is None


def test_the_site_profile_outranks_the_property(project):
    # Profiles set as properties have the lowest priority in Pegasus.
    (project / "sites.yml").write_text(
        _POOL_SITES.replace("{style: condor}", "{style: condor, data.configuration: condorio}")
    )
    (project / "pegasus.properties").write_text("pegasus.data.configuration = sharedfs\n")
    assert _problem(project) is None


def test_plan_overrides_outrank_the_project_properties(project):
    (project / "pegasus.properties").write_text("pegasus.data.configuration = sharedfs\n")
    assert _problem(project, plan_properties={"pegasus.data.configuration": "condorio"}) is None


def test_read_properties_handles_java_separators(tmp_path):
    f = tmp_path / "p"
    f.write_text("# c\n! c\na = 1\nb:2\nc 3\nd=\n  e  =  x y \n")
    assert shared_fs.read_properties(f) == {"a": "1", "b": "2", "c": "3", "d": "", "e": "x y"}


def test_run_flow_stops_before_plan(project, monkeypatch):
    from services import run_flow as rf

    gen = project / "workflow_generator.py"
    gen.write_text(
        "open('workflow.yml','w').write('pegasus: \"5.0\"\\njobs: []\\n')\n"
        "open('pegasus.properties','w').write('pegasus.data.configuration = sharedfs\\n')\n"
    )
    planned = []

    async def fake_plan_capture(*a, **k):
        planned.append(a)
        return 0, ""

    monkeypatch.setattr(rf, "_plan_capture", fake_plan_capture)

    async def scenario():
        flow = rf.RunFlowManager().start(
            project_id="af3", project_dir=str(project), generator_file=str(gen),
            has_container_def=False, generator_args=[], site="condorpool",
            output_site="local",
        )
        await asyncio.wait_for(flow._task, timeout=60)
        return flow

    flow = asyncio.run(scenario())
    snap = flow.snapshot()
    assert planned == []
    assert snap["failure"]["stage"] == "plan"
    assert "sharedfs" in snap["failure"]["cause"]
    assert [s["status"] for s in snap["stages"]] == ["done", "failed", "skipped"]
