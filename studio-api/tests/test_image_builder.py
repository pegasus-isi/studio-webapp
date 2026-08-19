"""Tests for the container image builder service and endpoints."""

from __future__ import annotations

import asyncio
import json
import os

import services.image_builder as ib


# --- pure helpers ---


def test_normalize_registry_variants():
    assert ib.normalize_registry("docker:///kthare10/") == "docker.io/kthare10"
    assert ib.normalize_registry("kthare10") == "docker.io/kthare10"
    assert ib.normalize_registry("ghcr.io/pegasus-isi") == "ghcr.io/pegasus-isi"
    assert ib.normalize_registry("localhost:5000/dev") == "localhost:5000/dev"
    assert ib.normalize_registry("") == ""
    assert ib.normalize_registry("docker://docker.io/foo/") == "docker.io/foo"


def test_image_ref():
    assert (
        ib.image_ref("docker:///kthare10/", "Mag-Workflow", "latest")
        == "docker.io/kthare10/mag-workflow:latest"
    )
    assert ib.image_ref("", "soil", "v2") == "soil:v2"
    assert ib.image_ref("kthare10", "x", "") == "docker.io/kthare10/x:latest"


def test_build_command_default(monkeypatch):
    monkeypatch.setattr(ib, "_FAKEROOT", "0")
    assert ib.build_command("apptainer", "img.sif", "img.def") == [
        "apptainer", "build", "--force", "img.sif", "img.def",
    ]


def test_build_command_fakeroot_follows_euid(monkeypatch):
    """%post needs root; when the process is not root that has to be emulated."""
    monkeypatch.setattr(ib, "_FAKEROOT", "")

    monkeypatch.setattr(ib.os, "geteuid", lambda: 1000)
    assert "--fakeroot" in ib.build_command("apptainer", "i.sif", "i.def")

    monkeypatch.setattr(ib.os, "geteuid", lambda: 0)
    assert "--fakeroot" not in ib.build_command("apptainer", "i.sif", "i.def")

    # An explicit setting wins either way.
    monkeypatch.setattr(ib, "_FAKEROOT", "0")
    monkeypatch.setattr(ib.os, "geteuid", lambda: 1000)
    assert "--fakeroot" not in ib.build_command("apptainer", "i.sif", "i.def")


def test_fakeroot_always_bypasses_the_fakeroot_command(monkeypatch):
    """Apptainer otherwise LD_PRELOADs the host libfakeroot.so into the
    container, which dies whenever the two glibcs differ — an ubuntu:22.04 base
    under a 24.04 host fails with "GLIBC_2.38 not found" before %post runs."""
    monkeypatch.setattr(ib, "_FAKEROOT", "1")
    cmd = ib.build_command("apptainer", "i.sif", "i.def")
    assert "--fakeroot" in cmd and "--ignore-fakeroot-command" in cmd


def test_build_command_never_binds_an_apt_conf(monkeypatch):
    """Apptainer will not create a bind destination absent from the container,
    so binding an apt.conf failed every build with "destination doesn't exist"."""
    monkeypatch.setattr(ib, "_FAKEROOT", "1")
    assert "--bind" not in ib.build_command("apptainer", "i.sif", "i.def")


def test_find_definition_prefers_apptainer_dir(tmp_path):
    proj = tmp_path / "proj"
    (proj / "Apptainer").mkdir(parents=True)
    (proj / "Apptainer" / "Tools.def").write_text("Bootstrap: docker\n")
    (proj / "Root.def").write_text("Bootstrap: docker\n")

    definition, sif = ib.find_definition(str(proj))
    assert definition.endswith(os.path.join("Apptainer", "Tools.def"))
    # The .sif lands beside the .def so the workflow can reference it by path.
    assert sif == os.path.join(str(proj), "Apptainer", "Tools.sif")

    (proj / "Apptainer" / "Tools.def").unlink()
    definition, sif = ib.find_definition(str(proj))
    assert definition.endswith("Root.def")
    assert sif == os.path.join(str(proj), "Root.sif")


def test_find_definition_missing(tmp_path):
    assert ib.find_definition(str(tmp_path)) is None


def test_find_definition_is_deterministic(tmp_path):
    """Several definitions: the first in sorted order, so rebuilds agree."""
    proj = tmp_path / "proj"
    (proj / "Apptainer").mkdir(parents=True)
    for name in ("zebra.def", "alpha.def", "middle.def"):
        (proj / "Apptainer" / name).write_text("Bootstrap: docker\n")
    definition, _ = ib.find_definition(str(proj))
    assert definition.endswith("alpha.def")


# --- stream_build ---


def _collect_events(gen):
    async def go():
        out = []
        async for chunk in gen:
            if chunk.startswith("data:"):
                out.append(json.loads(chunk[5:]))
        return out

    return asyncio.run(go())


def _build_kwargs(tmp_path, **overrides):
    proj = tmp_path / "proj"
    (proj / "Apptainer").mkdir(parents=True, exist_ok=True)
    (proj / "Apptainer" / "Tools.def").write_text("Bootstrap: docker\nFrom: ubuntu:22.04\n")
    kw = dict(
        project_id="proj",
        project_dir=str(proj),
        registry="docker:///kthare10/",
        username="",
        password="",
        tag="latest",
        push=False,
    )
    kw.update(overrides)
    return kw


def test_stream_build_no_builder(monkeypatch, tmp_path):
    monkeypatch.setattr(ib, "detect_builder", lambda: None)
    events = _collect_events(ib.stream_build(**_build_kwargs(tmp_path)))
    assert events[-1]["status"] == "error"
    assert "apptainer" in events[-1]["error"].lower()


def test_stream_build_success_local(monkeypatch, tmp_path):
    """Uses /bin/echo as the 'builder' so the whole pipeline runs for real."""
    monkeypatch.setattr(ib, "detect_builder", lambda: "echo")
    events = _collect_events(ib.stream_build(**_build_kwargs(tmp_path)))

    phases = [e["phase"] for e in events if "phase" in e]
    assert phases == ["build"]
    final = events[-1]
    assert final["status"] == "success"
    # The artifact is a file in the project, not a registry reference.
    assert final["image"].endswith(os.path.join("Apptainer", "Tools.sif"))
    assert final["pushed"] is False
    # echo printed the build args back as a "log line"
    assert any("build --force" in e.get("line", "") for e in events)
    # The user is told how to reference it from the Transformation Catalog.
    assert any('image_site="local"' in e.get("line", "") for e in events)


def test_stream_build_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(ib, "detect_builder", lambda: "false")
    events = _collect_events(ib.stream_build(**_build_kwargs(tmp_path)))
    final = events[-1]
    assert final["status"] == "error"
    assert "Build failed" in final["error"]


def test_stream_build_push_without_registry(monkeypatch, tmp_path):
    monkeypatch.setattr(ib, "detect_builder", lambda: "echo")
    events = _collect_events(
        ib.stream_build(**_build_kwargs(tmp_path, registry="", push=True))
    )
    final = events[-1]
    assert final["status"] == "success"
    assert final["pushed"] is False
    assert any("No container registry configured" in e.get("line", "")
               for e in events)
    # A local .sif is a complete result, not a degraded one.
    assert final["image"].endswith(".sif")


def test_stream_build_push_with_login(monkeypatch, tmp_path):
    """Login gets credentials via stdin (never argv) and push runs."""
    monkeypatch.setattr(ib, "detect_builder", lambda: "echo")
    events = _collect_events(ib.stream_build(**_build_kwargs(
        tmp_path, push=True, username="bob", password="s3cret",
    )))
    phases = [e["phase"] for e in events if "phase" in e]
    assert phases == ["build", "login", "push"]
    final = events[-1]
    assert final["status"] == "success"
    assert final["pushed"] is True
    assert final["ref"] == "docker.io/kthare10/proj:latest"
    # Apptainer publishes the .sif over ORAS, not as an OCI image manifest.
    assert any("oras://docker.io/kthare10/proj:latest" in e.get("line", "")
               for e in events)
    # Password must never appear in any streamed line (echo prints argv).
    assert not any("s3cret" in json.dumps(e) for e in events)
    assert any("--password-stdin" in e.get("line", "") for e in events)


# --- endpoints ---


def test_build_endpoint_streams(client, monkeypatch, tmp_path):
    from routers import workflows as wf

    proj_dir = tmp_path / "csv"
    (proj_dir / "Apptainer").mkdir(parents=True)
    (proj_dir / "Apptainer" / "Tools.def").write_text("Bootstrap: docker\n")

    async def _projects(ws=None):
        return [{
            "project_id": "csv", "project_dir": str(proj_dir),
            "has_container_def": True,
        }]

    monkeypatch.setattr(wf, "discover_projects_async", _projects)
    monkeypatch.setattr(ib, "detect_builder", lambda: "echo")

    res = client.post("/api/workflows/projects/csv/build", json={"push": False})
    assert res.status_code == 200
    assert '"status": "success"' in res.text
    assert "[DONE]" in res.text


def test_build_endpoint_requires_definition(client, monkeypatch):
    from routers import workflows as wf

    async def _projects(ws=None):
        return [{
            "project_id": "csv", "project_dir": "/tmp/x", "has_container_def": False,
        }]

    monkeypatch.setattr(wf, "discover_projects_async", _projects)
    res = client.post("/api/workflows/projects/csv/build", json={})
    assert res.status_code == 400


def test_registry_settings_roundtrip(client):
    # Empty by default.
    body = client.get("/api/settings/registry").json()
    assert body == {"registry": "", "username": "", "has_password": False}

    # Save with password; password is never echoed back.
    body = client.put("/api/settings/registry", json={
        "registry": "docker:///kthare10/", "username": "kthare10",
        "password": "tok123",
    }).json()
    assert body["registry"] == "docker:///kthare10/"
    assert body["username"] == "kthare10"
    assert body["has_password"] is True
    assert "tok123" not in json.dumps(body)

    # Update without password keeps the stored one.
    body = client.put("/api/settings/registry", json={
        "registry": "ghcr.io/pegasus-isi", "username": "kthare10",
    }).json()
    assert body["has_password"] is True

    # Explicit empty string clears it.
    body = client.put("/api/settings/registry", json={
        "registry": "ghcr.io/pegasus-isi", "username": "", "password": "",
    }).json()
    assert body["has_password"] is False
