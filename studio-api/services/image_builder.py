"""Container image build & push for workflow projects.

Workflows here ship an Apptainer definition file and reference the built ``.sif``
from the Transformation Catalog as ``file:///abs/path/X.sif`` with
``image_site="local"``, so no registry round-trip is required — Pegasus stages the
``.sif`` like any other input file. "Build Image" is therefore a first-class
lifecycle step between Generate and Plan, and its output is a file in the project.

Builds run ``apptainer build`` (``singularity`` accepted as the same CLI under an
older name). Output streams to the client as SSE lines with silence pings every
few seconds so proxies never see an idle response (see the Envoy timeout
incident).

Push is optional and only happens when a registry is configured. Apptainer pushes
a ``.sif`` to an OCI registry over ORAS, which is a different artifact from a
Docker image: it is the same file, stored in a registry, not an OCI image
manifest. Credentials are supplied via ``--password-stdin`` — never on argv.
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import shutil
import time
from typing import Any, AsyncGenerator

import structlog

log = structlog.get_logger()

# Same CLI, two names. Apptainer is the current project; singularity is the
# older name still present on some hosts.
_BUILDERS = ("apptainer", "singularity")
_BUILD_TIMEOUT_S = int(os.environ.get("STUDIO_BUILD_TIMEOUT", "1800"))
_PING_INTERVAL_S = 10.0

# %post runs as root inside the build sandbox. When the studio process is not
# root, that has to be emulated: --fakeroot uses subuid/subgid ranges where they
# exist and falls back to Apptainer's rootless path otherwise. Default on for a
# non-root euid, and overridable because a host with neither subuid ranges nor a
# setuid installation may need it off (and a privileged container running as root
# does not need it at all).
_FAKEROOT = os.environ.get("STUDIO_BUILD_FAKEROOT", "").lower()

# %post runs as root inside the build sandbox. When the studio process is not
# root that has to be emulated: --fakeroot uses subuid/subgid ranges where they
# exist and falls back to Apptainer's rootless path otherwise.
#
# --ignore-fakeroot-command goes with it. Without it Apptainer LD_PRELOADs the
# *host* libfakeroot.so into the container, which fails whenever their glibcs
# differ — an ubuntu:22.04 base under this ubuntu:24.04 image dies with
# "GLIBC_2.38 not found" before %post runs at all. The kernel user-namespace
# root mapping needs no such library.
#
# Note apt still cannot drop privileges to _apt under an emulated root
# ("setgroups: Operation not permitted"). That is fixed in the definition file,
# where /etc/apt/apt.conf.d exists — see the pegasus-apptainer skill. It cannot
# be fixed with --bind: Apptainer will not create a bind destination that is
# absent from the container, so binding an apt.conf failed every build with
# "destination doesn't exist in container".



# Apt cannot drop privileges to the _apt user under an emulated root, so every
# download fails with "setgroups: Operation not permitted" — and the build then
# limps on to a confusing "pip3: not found" because the packages never arrived.
#
# The setting has to be inside the container, where /etc/apt/apt.conf.d exists;
# a bind mount cannot supply it. Rather than require every definition to carry
# the line (existing ones do not, and the constraint is the builder's, not the
# author's), build from an augmented copy. The user's file is never modified,
# and the log says what was added.
_APT_FIX_LINE = (
    "printf 'APT::Sandbox::User \"root\";\\n' > /etc/apt/apt.conf.d/00-studio-no-sandbox"
)
_POST_RE = re.compile(r"^\s*%post\b.*$", re.M)


def needs_apt_fix(text: str) -> bool:
    """True if the definition installs with apt and does not already set this."""
    if "APT::Sandbox::User" in text:
        return False
    return bool(re.search(r"^\s*(apt-get|apt|aptitude)\s", text, re.M))


def with_apt_fix(text: str) -> str | None:
    """Return the definition with the apt setting as the first %post step."""
    m = _POST_RE.search(text)
    if not m:
        return None
    insert_at = m.end()
    return text[:insert_at] + "\n    " + _APT_FIX_LINE + text[insert_at:]


def detect_builder() -> str | None:
    """First available Apptainer-compatible builder on PATH."""
    for binary in _BUILDERS:
        if shutil.which(binary):
            return binary
    return None


def _use_fakeroot() -> bool:
    if _FAKEROOT in ("1", "true", "yes"):
        return True
    if _FAKEROOT in ("0", "false", "no"):
        return False
    return os.geteuid() != 0


def build_command(builder: str, sif: str, definition: str) -> list[str]:
    """Build argv for turning a definition file into a ``.sif``.

    ``--force`` overwrites a previous build rather than prompting, since this
    runs unattended behind an HTTP request.
    """
    cmd = [builder, "build", "--force"]
    if _use_fakeroot():
        cmd += ["--fakeroot", "--ignore-fakeroot-command"]
    cmd += [sif, definition]
    return cmd


def find_definition(project_dir: str) -> tuple[str, str] | None:
    """(definition_path, sif_path) for a project, or None.

    Convention in these workflows: ``<project>/Apptainer/<Name>.def``, with the
    ``.sif`` written beside it so the Transformation Catalog can reference it by
    absolute path. A definition at the project root also works. Where a project
    carries several, the first in sorted order wins, which keeps repeated builds
    deterministic.
    """
    for pattern in (
        os.path.join(project_dir, "Apptainer", "*.def"),
        os.path.join(project_dir, "*.def"),
    ):
        matches = sorted(glob.glob(pattern))
        if matches:
            definition = matches[0]
            stem = os.path.splitext(os.path.basename(definition))[0]
            return definition, os.path.join(os.path.dirname(definition), f"{stem}.sif")
    return None


def normalize_registry(registry: str) -> str:
    """Settings value -> registry prefix.

    Accepts the Pegasus container-catalog style ("docker:///kthare10/") as
    well as plain prefixes ("kthare10", "ghcr.io/foo"). A prefix without a
    registry host resolves to Docker Hub.
    """
    reg = (registry or "").strip()
    reg = re.sub(r"^(docker|oras):/*", "", reg).strip("/")
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
        # A builder that rejects the login and exits before reading stdin makes
        # write/drain raise BrokenPipeError; without this guard it would abort
        # the whole SSE generator instead of letting the normal exit-code path
        # report the login failure (BE-9).
        try:
            proc.stdin.write(stdin_data)
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass

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
    """Build (and optionally push) a project's container, yielding SSE strings.

    Events: ``{"phase": ...}``, ``{"line": ...}``, then a terminal
    ``{"status": "success"|"error", "image": <sif path>, "pushed": bool}`` with
    ``"ref"`` carrying the registry reference when a push happened.
    The caller appends the ``[DONE]`` terminator.
    """
    builder = detect_builder()
    if builder is None:
        yield _sse({
            "status": "error",
            "error": (
                "No Apptainer found (apptainer/singularity). "
                "Rebuild the studio image to include Apptainer."
            ),
        })
        return

    found = find_definition(project_dir)
    if found is None:
        yield _sse({
            "status": "error",
            "error": (
                "No Apptainer definition file in this project. Expected "
                "Apptainer/<Name>.def — ask the assistant for /pegasus-apptainer."
            ),
        })
        return
    definition, sif = found

    deadline = time.monotonic() + _BUILD_TIMEOUT_S

    # Build from an augmented copy when apt would fail under an emulated root.
    # Written beside the original so relative %files paths still resolve.
    build_def, temp_def = definition, None
    if _use_fakeroot():
        try:
            with open(definition) as fh:
                text = fh.read()
            if needs_apt_fix(text):
                patched = with_apt_fix(text)
                if patched:
                    temp_def = os.path.join(
                        os.path.dirname(definition),
                        f".studio-build-{os.path.basename(definition)}",
                    )
                    with open(temp_def, "w") as fh:
                        fh.write(patched)
                    build_def = temp_def
        except OSError as e:
            log.warning("apt_fix_skipped", definition=definition, error=str(e))

    cmd = build_command(builder, sif, build_def)
    yield _sse({"phase": "build", "image": sif, "builder": builder})
    if temp_def:
        yield _sse({"line": (
            "note: this build runs unprivileged, where apt cannot drop "
            "privileges to _apt. Added to %post for this build only "
            "(your .def is unchanged):\n"
            f"    {_APT_FIX_LINE}"
        )})
    yield _sse({"line": f"$ {' '.join(cmd)}"})

    rc = 1
    async for kind, value in _run_streaming(
        cmd, cwd=project_dir, deadline=deadline,
    ):
        if kind == "line":
            yield _sse({"line": value})
        elif kind == "ping":
            yield ": ping\n\n"
        else:
            rc = value
    if temp_def:
        try:
            os.unlink(temp_def)
        except OSError:
            pass

    if rc != 0:
        yield _sse({"status": "error", "image": sif,
                    "error": f"Build failed (exit {rc})."})
        return

    yield _sse({"line": (
        f"Reference it from the Transformation Catalog as "
        f'image="file://{sif}" with image_site="local".'
    )})

    if not push:
        yield _sse({"status": "success", "image": sif, "pushed": False})
        return

    prefix = normalize_registry(registry)
    if not prefix:
        # Not a failure: the local .sif is what the workflow actually references.
        yield _sse({"line": (
            "No container registry configured — the .sif stays local, which is "
            "all a workflow needs. Set a registry in Settings → Pegasus Options "
            "to also publish it."
        )})
        yield _sse({"status": "success", "image": sif, "pushed": False})
        return

    ref = image_ref(registry, project_id, tag)
    registry_host = prefix.split("/", 1)[0]
    if username and password:
        yield _sse({
            "phase": "login",
            "line": f"$ {builder} registry login oras://{registry_host}",
        })
        rc = 1
        async for kind, value in _run_streaming(
            [builder, "registry", "login", "--username", username,
             "--password-stdin", f"oras://{registry_host}"],
            stdin_data=password.encode(), deadline=deadline,
        ):
            if kind == "line":
                yield _sse({"line": value})
            elif kind == "ping":
                yield ": ping\n\n"
            else:
                rc = value
        if rc != 0:
            yield _sse({"status": "error", "image": sif,
                        "error": f"Registry login failed (exit {rc})."})
            return

    yield _sse({"phase": "push"})
    yield _sse({"line": f"$ {builder} push {sif} oras://{ref}"})
    rc = 1
    async for kind, value in _run_streaming(
        [builder, "push", sif, f"oras://{ref}"], deadline=deadline,
    ):
        if kind == "line":
            yield _sse({"line": value})
        elif kind == "ping":
            yield ": ping\n\n"
        else:
            rc = value
    if rc != 0:
        yield _sse({"status": "error", "image": sif, "ref": ref,
                    "error": f"Push failed (exit {rc}). Check registry credentials."})
        return

    log.info("image_pushed", project_id=project_id, sif=sif, ref=ref)
    yield _sse({"status": "success", "image": sif, "ref": ref, "pushed": True})
