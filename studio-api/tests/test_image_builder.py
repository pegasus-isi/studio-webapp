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
    # No forced isolation by default; no apt mount when the conf is absent.
    monkeypatch.setattr(ib, "_BUILD_ISOLATION", "")
    monkeypatch.setattr(ib, "_APT_CONF_PATH", "/nonexistent/apt.conf")
    assert ib.build_command("podman", "img:latest", "Dockerfile", ".") == [
        "podman", "build", "-t", "img:latest", "-f", "Dockerfile", ".",
    ]


def test_build_command_isolation_configurable(monkeypatch):
    monkeypatch.setattr(ib, "_BUILD_ISOLATION", "chroot")
    monkeypatch.setattr(ib, "_APT_CONF_PATH", "/nonexistent/apt.conf")
    assert "--isolation=chroot" in ib.build_command(
        "podman", "img:latest", "Dockerfile", "."
    )
    # docker never gets --isolation.
    assert "--isolation" not in " ".join(
        ib.build_command("docker", "img:latest", "Dockerfile", ".")
    )


def test_build_command_apt_mount(monkeypatch, tmp_path):
    # When the apt no-sandbox conf exists it is mounted for podman/buildah
    # (fixes apt's _apt privilege drop under single-UID mapping), not docker.
    conf = tmp_path / "apt-no-sandbox.conf"
    conf.write_text('APT::Sandbox::User "root";\n')
    monkeypatch.setattr(ib, "_BUILD_ISOLATION", "")
    monkeypatch.setattr(ib, "_APT_FIX", True)
    monkeypatch.setattr(ib, "_APT_CONF_PATH", str(conf))

    cmd = ib.build_command("podman", "img:latest", "Dockerfile", ".")
    assert "-v" in cmd
    assert f"{conf}:/etc/apt/apt.conf.d/00-studio-no-sandbox.conf:ro" in cmd
    # Disabling the fix drops the mount.
    monkeypatch.setattr(ib, "_APT_FIX", False)
    assert "-v" not in ib.build_command("podman", "img:latest", "Dockerfile", ".")
    # docker never gets the mount.
    monkeypatch.setattr(ib, "_APT_FIX", True)
    assert "-v" not in ib.build_command("docker", "img:latest", "Dockerfile", ".")


def test_find_dockerfile_prefers_docker_dir(tmp_path):
    proj = tmp_path / "proj"
    (proj / "Docker").mkdir(parents=True)
    (proj / "Docker" / "Dockerfile").write_text("FROM scratch\n")
    (proj / "Dockerfile").write_text("FROM scratch\n")
    dockerfile, context = ib.find_dockerfile(str(proj))
    assert dockerfile.endswith(os.path.join("Docker", "Dockerfile"))
    assert context.endswith("Docker")

    (proj / "Docker" / "Dockerfile").unlink()
    dockerfile, context = ib.find_dockerfile(str(proj))
    assert dockerfile.endswith("Dockerfile")
    assert context == str(proj)

    assert ib.find_dockerfile(str(tmp_path)) is None


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
    (proj / "Docker").mkdir(parents=True, exist_ok=True)
    (proj / "Docker" / "Dockerfile").write_text("FROM scratch\n")
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
    assert "podman" in events[-1]["error"]


def test_stream_build_success_local(monkeypatch, tmp_path):
    """Uses /bin/echo as the 'builder' so the whole pipeline runs for real."""
    monkeypatch.setattr(ib, "detect_builder", lambda: "echo")
    events = _collect_events(ib.stream_build(**_build_kwargs(tmp_path)))

    phases = [e["phase"] for e in events if "phase" in e]
    assert phases == ["build"]
    final = events[-1]
    assert final["status"] == "success"
    assert final["image"] == "docker.io/kthare10/proj:latest"
    assert final["pushed"] is False
    # echo printed the build args back as a "log line"
    assert any("build -t docker.io/kthare10/proj:latest" in e.get("line", "")
               for e in events)


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
    # Password must never appear in any streamed line (echo prints argv).
    assert not any("s3cret" in json.dumps(e) for e in events)
    assert any("--password-stdin" in e.get("line", "") for e in events)


# --- endpoints ---


def test_build_endpoint_streams(client, monkeypatch, tmp_path):
    from routers import workflows as wf

    proj_dir = tmp_path / "csv"
    (proj_dir / "Docker").mkdir(parents=True)
    (proj_dir / "Docker" / "Dockerfile").write_text("FROM scratch\n")

    async def _projects(ws=None):
        return [{
            "project_id": "csv", "project_dir": str(proj_dir),
            "has_dockerfile": True,
        }]

    monkeypatch.setattr(wf, "discover_projects_async", _projects)
    monkeypatch.setattr(ib, "detect_builder", lambda: "echo")

    res = client.post("/api/workflows/projects/csv/build", json={"push": False})
    assert res.status_code == 200
    assert '"status": "success"' in res.text
    assert "[DONE]" in res.text


def test_build_endpoint_requires_dockerfile(client, monkeypatch):
    from routers import workflows as wf

    async def _projects(ws=None):
        return [{
            "project_id": "csv", "project_dir": "/tmp/x", "has_dockerfile": False,
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
