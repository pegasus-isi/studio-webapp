"""Container image build & push for workflow projects.

Most Pegasus workflows here ship a Dockerfile and reference the built image
from a registry (workers pull it via Apptainer at run time), so "Build Image"
is a first-class lifecycle step between Generate and Plan.

The studio container has no Docker daemon; builds use a daemonless OCI
builder — podman preferred, buildah/docker accepted when present. Output
streams to the client as SSE lines with silence pings every few seconds so
proxies never see an idle response (see the Envoy timeout incident).

Credentials are supplied via ``--password-stdin`` — never on argv.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from typing import Any, AsyncGenerator

import structlog

log = structlog.get_logger()

_BUILDERS = ("podman", "buildah", "docker")
_BUILD_TIMEOUT_S = int(os.environ.get("STUDIO_BUILD_TIMEOUT", "1800"))
_PING_INTERVAL_S = 10.0


def detect_builder() -> str | None:
    """First available daemonless-capable image builder on PATH."""
    for binary in _BUILDERS:
        if shutil.which(binary):
            return binary
    return None


def find_dockerfile(project_dir: str) -> tuple[str, str] | None:
    """(dockerfile_path, build_context_dir) for a project, or None.

    Convention in these workflows: ``<project>/Docker/Dockerfile`` with the
    Docker/ directory as context; plain ``<project>/Dockerfile`` also works.
    """
    for rel in (os.path.join("Docker", "Dockerfile"), "Dockerfile"):
        path = os.path.join(project_dir, rel)
        if os.path.isfile(path):
            return path, os.path.dirname(path)
    return None


def normalize_registry(registry: str) -> str:
    """Settings value -> OCI registry prefix.

    Accepts the Pegasus container-catalog style ("docker:///kthare10/") as
    well as plain prefixes ("kthare10", "ghcr.io/foo"). A prefix without a
    registry host resolves to Docker Hub.
    """
    reg = (registry or "").strip()
    reg = re.sub(r"^docker:/*", "", reg).strip("/")
    if not reg:
        return ""
    host = reg.split("/", 1)[0]
    # A dot, a port, or "localhost" marks a real registry host; a bare user/
    # org name (e.g. "kthare10") means Docker Hub.
    if "." not in host and ":" not in host and host != "localhost":
        reg = f"docker.io/{reg}"
    return reg


def sanitize_repo_name(name: str) -> str:
    """Project id -> valid OCI repository component."""
    repo = re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("-.")
    return repo or "workflow"


def image_ref(registry: str, project_id: str, tag: str) -> str:
    repo = sanitize_repo_name(project_id)
    tag = re.sub(r"[^a-zA-Z0-9._-]+", "-", tag or "latest") or "latest"
    prefix = normalize_registry(registry)
    return f"{prefix}/{repo}:{tag}" if prefix else f"{repo}:{tag}"


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def _run_streaming(
    cmd: list[str],
    *,
    cwd: str | None = None,
    stdin_data: bytes | None = None,
    deadline: float,
) -> AsyncGenerator[tuple[str, Any], None]:
    """Run a command, yielding ("line", text) as output arrives, ("ping",
    None) on silence, and finally ("rc", returncode)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
        cwd=cwd,
    )
    if stdin_data is not None and proc.stdin is not None:
        proc.stdin.write(stdin_data)
        await proc.stdin.drain()
        proc.stdin.close()

    pending: asyncio.Task | None = None
    try:
        while True:
            if time.monotonic() > deadline:
                proc.kill()
                yield ("line", "[build timed out]")
                yield ("rc", 124)
                return
            if pending is None:
                pending = asyncio.create_task(proc.stdout.readline())
            done, _ = await asyncio.wait({pending}, timeout=_PING_INTERVAL_S)
            if not done:
                yield ("ping", None)
                continue
            raw = pending.result()
            pending = None
            if not raw:
                break
            yield ("line", raw.decode("utf-8", errors="replace").rstrip())
        yield ("rc", await proc.wait())
    finally:
        if pending is not None:
            pending.cancel()
        if proc.returncode is None:
            proc.kill()


async def stream_build(
    *,
    project_id: str,
    project_dir: str,
    registry: str,
    username: str,
    password: str,
    tag: str,
    push: bool,
) -> AsyncGenerator[str, None]:
    """Build (and optionally push) a project image, yielding SSE strings.

    Events: ``{"phase": ...}``, ``{"line": ...}``, then a terminal
    ``{"status": "success"|"error", "image": ..., "pushed": bool}``.
    The caller appends the ``[DONE]`` terminator.
    """
    builder = detect_builder()
    if builder is None:
        yield _sse({
            "status": "error",
            "error": (
                "No image builder found (podman/buildah/docker). "
                "Rebuild the studio image to include podman."
            ),
        })
        return

    found = find_dockerfile(project_dir)
    if found is None:
        yield _sse({"status": "error", "error": "No Dockerfile in this project."})
        return
    dockerfile, context = found

    ref = image_ref(registry, project_id, tag)
    prefix = normalize_registry(registry)
    deadline = time.monotonic() + _BUILD_TIMEOUT_S

    yield _sse({"phase": "build", "image": ref, "builder": builder})
    yield _sse({"line": f"$ {builder} build -t {ref} -f {dockerfile} {context}"})

    rc = 1
    async for kind, value in _run_streaming(
        [builder, "build", "-t", ref, "-f", dockerfile, context],
        cwd=project_dir, deadline=deadline,
    ):
        if kind == "line":
            yield _sse({"line": value})
        elif kind == "ping":
            yield ": ping\n\n"
        else:
            rc = value
    if rc != 0:
        yield _sse({"status": "error", "image": ref,
                    "error": f"Build failed (exit {rc})."})
        return

    if not push:
        yield _sse({"status": "success", "image": ref, "pushed": False})
        return
    if not prefix:
        yield _sse({"line": (
            "No container registry configured — image built locally only. "
            "Set a registry in Settings → Pegasus Options to enable push."
        )})
        yield _sse({"status": "success", "image": ref, "pushed": False})
        return

    registry_host = prefix.split("/", 1)[0]
    if username and password:
        yield _sse({"phase": "login", "line": f"$ {builder} login {registry_host}"})
        rc = 1
        async for kind, value in _run_streaming(
            [builder, "login", registry_host, "-u", username, "--password-stdin"],
            stdin_data=password.encode(), deadline=deadline,
        ):
            if kind == "line":
                yield _sse({"line": value})
            elif kind == "ping":
                yield ": ping\n\n"
            else:
                rc = value
        if rc != 0:
            yield _sse({"status": "error", "image": ref,
                        "error": f"Registry login failed (exit {rc})."})
            return

    yield _sse({"phase": "push"})
    yield _sse({"line": f"$ {builder} push {ref}"})
    rc = 1
    async for kind, value in _run_streaming(
        [builder, "push", ref], deadline=deadline,
    ):
        if kind == "line":
            yield _sse({"line": value})
        elif kind == "ping":
            yield ": ping\n\n"
        else:
            rc = value
    if rc != 0:
        yield _sse({"status": "error", "image": ref,
                    "error": f"Push failed (exit {rc}). Check registry credentials."})
        return

    log.info("image_pushed", project_id=project_id, image=ref)
    yield _sse({"status": "success", "image": ref, "pushed": True})
