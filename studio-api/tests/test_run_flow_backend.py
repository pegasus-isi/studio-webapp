"""The guided Run must execute where the active backend runs workflows.

run_flow's stages once spawned local subprocesses directly, which silently ran
generate/plan on this machine while a remote backend was active — baking local
paths into sites.yml and the plan (the exact failure the manual endpoints
already guard against). These tests pin every stage to the backend contract
with a recording fake; the local-subprocess path is booby-trapped so a
regression fails loudly instead of quietly running in the wrong place.
"""
from __future__ import annotations

import asyncio

import pytest

import services.backends as backends_mod
import services.run_flow as rf
from services.backends.base import ComputeBackend, ExecResult


class FakeRemoteBackend(ComputeBackend):
    """Records every backend call; never touches a real subprocess."""

    id = "fake-remote"
    label = "Fake remote"
    is_remote = True

    def __init__(self, *, returncode=0, stdout=b"", raise_exc=None):
        self.calls: list[tuple] = []
        self._returncode = returncode
        self._stdout = stdout
        self._raise = raise_exc

    async def exec(self, argv, *, cwd=None, timeout, env=None):
        self.calls.append(("exec", list(argv), cwd))
        self.last_env = env
        if self._raise is not None:
            raise self._raise
        return ExecResult(returncode=self._returncode, stdout=self._stdout, stderr=b"")

    def exec_sync(self, argv, *, cwd=None, timeout):
        self.calls.append(("exec_sync", list(argv), cwd))
        return ExecResult(returncode=self._returncode, stdout=self._stdout, stderr=b"")

    async def push_project(self, project_dir: str) -> None:
        self.calls.append(("push", project_dir))

    def to_remote(self, path: str) -> str:
        return "/remote" + path

    def scan_root(self) -> str:
        return "/tmp"

    def workspace_root(self) -> str:
        return "/tmp"


@pytest.fixture
def no_local_subprocess(monkeypatch):
    """Any local spawn during a flow stage is a routing regression."""

    async def _forbidden(*a, **k):
        raise AssertionError("stage spawned a local subprocess instead of using the backend")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _forbidden)


def _install(monkeypatch, backend: ComputeBackend) -> None:
    # Call sites import active_backend lazily from services.backends, so
    # patching the module attribute covers every stage.
    # Initialize router imports before substituting the registry function;
    # otherwise a lazy main import can retain this fake after the test ends.
    import main  # noqa: F401

    monkeypatch.setattr(backends_mod, "active_backend", lambda: backend)


def _flow(stages=("generate", "plan", "submit")) -> rf.RunFlow:
    return rf.RunFlow(
        flow_id="test",
        project_id="proj",
        stages=[rf.Stage(key=k, label=rf.STAGE_LABELS[k]) for k in stages],
    )


# ── generate ──────────────────────────────────────────────────────────────


def test_generate_pushes_then_execs_on_the_backend(
    tmp_path, monkeypatch, no_local_subprocess
):
    fake = FakeRemoteBackend()
    _install(monkeypatch, fake)
    flow = _flow()

    ok = asyncio.run(
        rf._run_generate(
            flow,
            project_dir=str(tmp_path),
            generator_file="workflow_generator.py",
            args=["--n", "1"],
        )
    )

    assert ok is True
    assert fake.calls[0] == ("push", str(tmp_path))
    kind, argv, cwd = fake.calls[1]
    assert kind == "exec"
    assert argv == ["python3", "workflow_generator.py", "--n", "1"]
    assert cwd == str(tmp_path)


# ── plan ──────────────────────────────────────────────────────────────────


def test_plan_pushes_first_and_carries_the_wfmonitor_flags(
    tmp_path, monkeypatch, no_local_subprocess
):
    monkeypatch.delenv("STUDIO_WFMONITOR_EVENTS", raising=False)
    (tmp_path / "workflow.yml").write_text('pegasus: "5.0"\njobs: []\n')
    fake = FakeRemoteBackend()
    _install(monkeypatch, fake)
    flow = _flow()

    ok = asyncio.run(
        rf._run_plan(
            flow, project_dir=str(tmp_path), site="condorpool", output_site="local"
        )
    )

    assert ok is True
    assert fake.calls[0][0] == "push"
    kind, argv, cwd = fake.calls[1]
    assert kind == "exec"
    assert argv[0] == "pegasus-plan"
    # Without the -D flags a guided run loses the wfmonitor event JSONL that
    # a manual Plan gets — parity is the point, not the specific flag.
    assert "-Dpegasus.monitord.plugins.wfmonitor.enabled=true" in argv
    assert argv[argv.index("-s") + 1] == "condorpool"
    assert cwd == str(tmp_path)


# ── build staleness ───────────────────────────────────────────────────────


def test_remote_staleness_is_checked_on_the_backend(monkeypatch):
    fake = FakeRemoteBackend(returncode=0)  # image present and fresh remotely
    _install(monkeypatch, fake)

    stale = asyncio.run(rf._image_stale_on_backend("/w/env.def", "/w/env.sif"))

    assert stale is False
    kind, argv, _ = fake.calls[0]
    assert (kind, argv[0], argv[1]) == ("exec", "sh", "-c")
    # The check must run against the backend's own paths, not the mirror's.
    assert "/remote/w/env.sif" in argv[2]


def test_remote_staleness_failure_reads_as_stale(monkeypatch):
    # Same bias as image_is_stale: build unnecessarily, never skip wrongly.
    fake = FakeRemoteBackend(raise_exc=asyncio.TimeoutError())
    _install(monkeypatch, fake)

    assert asyncio.run(rf._image_stale_on_backend("/w/env.def", "/w/env.sif")) is True


def test_local_staleness_still_uses_the_mtime_check(monkeypatch):
    fake = FakeRemoteBackend()
    fake.is_remote = False
    _install(monkeypatch, fake)
    seen = {}

    def fake_stale(definition, sif):
        seen["args"] = (definition, sif)
        return False

    monkeypatch.setattr(rf, "image_is_stale", fake_stale)

    stale = asyncio.run(rf._image_stale_on_backend("/w/env.def", "/w/env.sif"))

    assert stale is False
    assert seen["args"] == ("/w/env.def", "/w/env.sif")
    assert fake.calls == []  # no backend round-trip for a local image


# ── site defaults ─────────────────────────────────────────────────────────


def test_unset_site_resolves_from_the_backend_plan_defaults(
    client, monkeypatch, tmp_path
):
    import routers.workflows as wf_router

    fake = FakeRemoteBackend()
    fake.plan_defaults = lambda: {"site": "fabricpool", "output_site": "local"}

    async def fake_projects():
        return [
            {
                "project_id": "proj-x",
                "project_dir": str(tmp_path),
                "has_generator": False,
                "has_container_def": False,
            }
        ]

    captured = {}

    class _FakeFlow:
        def snapshot(self):
            return {"project_id": "proj-x", "state": "running", "active": True}

    def fake_start(**kwargs):
        captured.update(kwargs)
        return _FakeFlow()

    monkeypatch.setattr(wf_router, "discover_projects_async", fake_projects)
    monkeypatch.setattr(wf_router, "active_backend", lambda: fake)
    monkeypatch.setattr(rf.manager, "start", fake_start)

    res = client.post("/api/workflows/projects/proj-x/run-flow", json={"args": []})

    assert res.status_code == 200
    assert captured["site"] == "fabricpool"
    assert captured["output_site"] == "local"


def test_remote_generator_gets_only_workflow_secrets(client, monkeypatch, tmp_path):
    client.put("/api/settings/workflow-secrets", json={"name": "DATA_KEY", "value": "token"})
    monkeypatch.setenv("SERVER_ONLY_KEY", "must-stay-local")
    fake = FakeRemoteBackend()
    _install(monkeypatch, fake)

    assert asyncio.run(rf._run_generate(
        _flow(), project_dir=str(tmp_path), generator_file="workflow_generator.py", args=[]
    ))
    assert fake.last_env == {"DATA_KEY": "token"}


def test_manual_remote_generate_gets_workflow_secrets(client, monkeypatch, tmp_path):
    import routers.workflows as wf_router

    client.put("/api/settings/workflow-secrets", json={"name": "DATA_KEY", "value": "token"})
    fake = FakeRemoteBackend()
    monkeypatch.setattr(wf_router, "active_backend", lambda: fake)

    async def projects():
        return [{"project_id": "p", "project_dir": str(tmp_path),
                 "has_generator": True, "generator_file": "workflow_generator.py"}]

    monkeypatch.setattr(wf_router, "discover_projects_async", projects)
    response = client.post("/api/workflows/projects/p/generate", json={"args": []})
    assert response.status_code == 200, response.text
    assert fake.calls[0] == ("push", str(tmp_path))
    assert fake.last_env == {"DATA_KEY": "token"}


def test_ssh_flow_bypasses_local_condor_preflight_and_planning(
    monkeypatch, tmp_path, no_local_subprocess
):
    fake = FakeRemoteBackend()
    _install(monkeypatch, fake)
    (tmp_path / "workflow.yml").write_text('pegasus: "5.0"\njobs: []\n')

    async def forbidden(*args, **kwargs):
        raise AssertionError("SSH flow consulted the local Condor deployment")

    monkeypatch.setattr(rf, "_plan_mode_setting", forbidden)
    monkeypatch.setattr(rf.submit_host, "check", forbidden)
    monkeypatch.setattr(rf.remote_exec, "run_on_submit_host", forbidden)
    assert asyncio.run(rf._resolve_plan_mode()) == ("remote", "backend")

    async def submitted(flow, **kwargs):
        flow._succeed()
        return True

    monkeypatch.setattr(rf, "_run_submit", submitted)
    flow = _flow(stages=("plan", "submit"))
    asyncio.run(rf._execute(
        flow, project_dir=str(tmp_path), generator_file=None, generator_args=[],
        site="condorpool", output_site="local", force_build=False,
    ))
    assert flow.state == "succeeded", flow.failure
    assert fake.calls[1][1][0] == "pegasus-plan"


@pytest.mark.parametrize("remote_has_range", [True, False])
def test_remote_build_uses_remote_subuid_ranges(monkeypatch, remote_has_range):
    from services import image_builder as ib

    monkeypatch.delenv("STUDIO_BUILD_FAKEROOT", raising=False)
    # The laptop deliberately disagrees with the submit host.
    monkeypatch.setattr(ib, "subuid_range", lambda: None if remote_has_range else ("u", "g"))
    fake = FakeRemoteBackend(returncode=0 if remote_has_range else 1)
    command = asyncio.run(ib.build_command_for(fake, "apptainer", "i.sif", "i.def"))
    assert ("--fakeroot" in command) is remote_has_range
    assert "/etc/subuid" in fake.calls[0][1][2]
    assert "/etc/subgid" in fake.calls[0][1][2]
