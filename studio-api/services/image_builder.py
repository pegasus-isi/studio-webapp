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
import tempfile
import time
from typing import Any, AsyncGenerator

import structlog

log = structlog.get_logger()

# Same CLI, two names. Apptainer is the current project; singularity is the
# older name still present on some hosts.
_BUILDERS = ("apptainer", "singularity")
_BUILD_TIMEOUT_S = int(os.environ.get("STUDIO_BUILD_TIMEOUT", "1800"))
_PING_INTERVAL_S = 10.0

# --ignore-fakeroot-command, and deliberately NOT --fakeroot. The two are easy to
# treat as a pair; they do opposite things here. Measured on real definitions in
# the same container:
#
#   --fakeroot --ignore-fakeroot-command : 37,461 bytes, filesystem partition
#                                         40960-40960 — an EMPTY container,
#                                         reported as "Build complete", exit 0
#   (no flags)                           : FATAL in %post — /bin/sh:
#                                         libc.so.6: version `GLIBC_2.38' not
#                                         found (required by libfakeroot.so)
#   --ignore-fakeroot-command            : 312 MB, inspects clean, imports work
#
# The middle case is why "no flags" is wrong: with the fakeroot *command*
# available, Apptainer preloads the HOST's libfakeroot.so into the container, so
# any base older than the host fails before %post runs. ubuntu:22.04 (glibc 2.35)
# under this 24.04 image (2.39) is the common case, not an edge one.
# --ignore-fakeroot-command declines that path; the kernel's root-mapped
# namespace needs no such library.
#
# --fakeroot is the separate, harmful one. A trivial definition (alpine, no
# %post) survives it, so it only breaks once %post writes real content — which is
# how it shipped, and why it took a worker-node squashfs error to find.
#
# Any change here has to be checked against verify_image() below and against a
# definition whose base is older than the host. Exit 0 proves nothing.



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


def _unprivileged_build() -> bool:
    """Whether %post will run under an emulated root.

    Decides only whether the apt setting is injected — dropping --fakeroot did
    not fix apt, which still cannot drop privileges to _apt here (verified: the
    265 MB build above logged the same setgroups errors and succeeded only
    because its base image already carried python and pip).
    """
    override = os.environ.get("STUDIO_BUILD_APT_FIX", "").lower()
    if override in ("1", "true", "yes"):
        return True
    if override in ("0", "false", "no"):
        return False
    return os.geteuid() != 0


# The SIF assembly step — everything after "Creating SIF file..." — has its own
# failure mode, unrelated to the definition, and two of Apptainer's defaults for
# it are wrong inside a pod.
#
# 1. proot. On an unprivileged build Apptainer does not run mksquashfs directly:
#    it wraps it in the bundled /usr/libexec/apptainer/bin/proot ("-S /") so that
#    ownership umoci recorded in rootlesscontainers xattrs survives into the
#    image (apptainer/apptainer#2830). proot ptraces every syscall mksquashfs
#    makes. When its tracee dies without a normal exit, proot returns the -1 its
#    exit status is initialised to, which is reported as 255 — and Apptainer,
#    which formats the error with the mksquashfs path regardless of which process
#    it actually ran, prints:
#
#        while creating squashfs: /usr/libexec/apptainer/bin/mksquashfs command
#        failed: exit status 255:
#
#    Empty stderr, no cause, and it names a program that does not exit 255. That
#    is the entire error message. APPTAINER_IGNORE_PROOT removes the wrapper;
#    Apptainer then passes mksquashfs -all-root instead, which is what these
#    images want anyway — a workflow container built from a Debian base owns
#    everything as root. What is given up is non-root ownership carried by the
#    base image; what is gained is that mksquashfs's own stderr becomes the error
#    message when something goes wrong.
#
# 2. Memory and processor count. mksquashfs sizes its cache at 25% of physical
#    RAM and runs one thread per CPU, both read from the *host* rather than from
#    the pod's cgroup. On a large node it asks for far more of both than the pod
#    will ever get, and gets killed for it — which is precisely the death proot
#    cannot report. Capping them makes a build behave the same on every node it
#    happens to land on, at the cost of some wall time.
_MKSQUASHFS_MEM = os.environ.get("STUDIO_BUILD_MKSQUASHFS_MEM", "2G")
_MKSQUASHFS_PROCS = os.environ.get("STUDIO_BUILD_MKSQUASHFS_PROCS", "4")


def _flag_enabled(name: str, default: bool = True) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if value in ("1", "true", "yes"):
        return True
    if value in ("0", "false", "no"):
        return False
    return default


def build_command(builder: str, sif: str, definition: str) -> list[str]:
    """Build argv for turning a definition file into a ``.sif``.

    ``--force`` overwrites a previous build rather than prompting, since this
    runs unattended behind an HTTP request. ``--ignore-fakeroot-command`` keeps
    the host's libfakeroot.so out of the container; ``--fakeroot`` is left off
    because it silently produces an empty image. See the note above — the two
    flags look like a pair and are not one.

    ``--mksquashfs-args`` bounds the packing step to something a pod can
    actually supply (see the note above); Apptainer passes them straight through
    to mksquashfs.
    """
    cmd = [builder, "build", "--force", "--ignore-fakeroot-command"]
    extra = []
    if _MKSQUASHFS_MEM:
        extra += ["-mem", _MKSQUASHFS_MEM]
    if _MKSQUASHFS_PROCS:
        extra += ["-processors", _MKSQUASHFS_PROCS]
    if extra:
        cmd += ["--mksquashfs-args", " ".join(extra)]
    return cmd + [sif, definition]


def build_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for the build subprocess.

    ``APPTAINER_IGNORE_PROOT`` is the one that matters — see the note above.
    Apptainer treats any non-empty value as set, so the variable is added or
    left out rather than set to "0".
    """
    env = dict(os.environ if base is None else base)
    if _flag_enabled("STUDIO_BUILD_IGNORE_PROOT", True):
        env["APPTAINER_IGNORE_PROOT"] = "1"
    tmpdir = os.environ.get("STUDIO_BUILD_TMPDIR")
    if tmpdir:
        # Apptainer holds the extracted rootfs *and* the squashfs it packs from
        # it in one directory, so the build needs roughly twice the image size
        # wherever this points.
        env["APPTAINER_TMPDIR"] = tmpdir
    return env


def build_tmpdir(env: dict[str, str] | None = None) -> str:
    """Directory Apptainer will build in, matching its own resolution order."""
    env = os.environ if env is None else env
    for key in ("APPTAINER_TMPDIR", "SINGULARITY_TMPDIR", "TMPDIR"):
        value = env.get(key)
        if value:
            return value
    return tempfile.gettempdir()


def free_space_gb(path: str) -> float | None:
    """Free space in GiB on the filesystem holding *path*, or None."""
    try:
        stat = os.statvfs(path)
    except OSError:
        return None
    return stat.f_bavail * stat.f_frsize / (1 << 30)


# A SIF's descriptor table is written before its filesystem payload, so a build
# that produced no filesystem still leaves a parseable file: `apptainer sif list`
# reads it happily and `ls` shows a plausible size. The failure then surfaces one
# staging hop and several minutes later, on a worker, as "not a valid squashfs
# image" — naming the symptom in the last place anyone would look for the cause.
_SQUASHFS_MAGIC = b"hsqs"


def verify_image(sif: str) -> str | None:
    """Return an error message if *sif* is not a usable image, else None.

    ``apptainer build`` exiting 0 is not evidence that it built anything, so the
    payload is checked rather than the exit code.
    """
    try:
        size = os.path.getsize(sif)
    except OSError as e:
        return f"the build reported success but the image is unreadable: {e}"
    if size < 1024:
        return f"the build reported success but the image is only {size} bytes"

    # Scan the descriptor area and the start of the payload for a squashfs
    # superblock. Apptainer aligns the filesystem partition early (offset 40960
    # in observed builds), so 2 MB is a generous window.
    try:
        with open(sif, "rb") as fh:
            head = fh.read(2 << 20)
    except OSError as e:
        return f"the build reported success but the image is unreadable: {e}"

    if _SQUASHFS_MAGIC in head:
        return None
    # An image of real size in another format (ext3, an encrypted payload) is not
    # something to reject on the strength of one missing magic number.
    if size > 5 * 1024 * 1024:
        return None
    return (
        f"the build reported success but produced no filesystem ({size} bytes, "
        "no squashfs payload). This image would fail on a worker node with "
        '"not a valid squashfs image"'
    )


def squashfs_failure_hint(output: list[str], tmpdir: str) -> list[str]:
    """Lines to append when a build died packing the image, else nothing.

    A failure here means %post already ran to completion, so the definition is
    not the suspect and telling someone to check it wastes their next twenty
    minutes. The resources it consumed, and where, are the useful part.
    """
    if not any("squashfs" in line.lower() for line in output):
        return []
    free = free_space_gb(tmpdir)
    space = "unknown" if free is None else f"{free:.1f} GiB free"
    return [
        "",
        "This failed while packing the built root filesystem into the .sif, "
        "after %post had finished — the definition file itself completed.",
        f"That step works in {tmpdir} ({space}) and needs room for the root "
        "filesystem and the squashfs together, so roughly twice the image size.",
        "If space is not the problem, the packing step was most likely killed "
        f"for memory; it is capped at -mem {_MKSQUASHFS_MEM} / -processors "
        f"{_MKSQUASHFS_PROCS} (STUDIO_BUILD_MKSQUASHFS_MEM, "
        "STUDIO_BUILD_MKSQUASHFS_PROCS), and STUDIO_BUILD_TMPDIR moves the work "
        "somewhere with more space.",
    ]


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
    env: dict[str, str] | None = None,
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
        env=env,
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
    if _unprivileged_build():
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
    env = build_env()
    yield _sse({"phase": "build", "image": sif, "builder": builder})

    # The packing step needs room for the extracted rootfs and the squashfs
    # built from it, side by side in the same directory. Say where that is and
    # how much is left, because when it runs out the error names squashfs and
    # not the disk.
    tmpdir = build_tmpdir(env)
    free = free_space_gb(tmpdir)
    if free is not None:
        yield _sse({"line": f"build tmpdir: {tmpdir} ({free:.1f} GiB free)"})

    if temp_def:
        yield _sse({"line": (
            "note: this build runs unprivileged, where apt cannot drop "
            "privileges to _apt. Added to %post for this build only "
            "(your .def is unchanged):\n"
            f"    {_APT_FIX_LINE}"
        )})
    yield _sse({"line": f"$ {' '.join(cmd)}"})

    rc = 1
    output: list[str] = []
    async for kind, value in _run_streaming(
        cmd, cwd=project_dir, env=env, deadline=deadline,
    ):
        if kind == "line":
            output.append(value)
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
        for line in squashfs_failure_hint(output, tmpdir):
            yield _sse({"line": line})
        yield _sse({"status": "error", "image": sif,
                    "error": f"Build failed (exit {rc})."})
        return

    # Exit 0 is not enough. Apptainer has been observed reporting "Build
    # complete" for an image with a zero-length filesystem, which then fails on a
    # worker node minutes later with an error naming squashfs rather than the
    # build. Fail here, where the build log is still on screen.
    problem = verify_image(sif)
    if problem:
        yield _sse({"status": "error", "image": sif, "error": problem})
        log.warning("image_build_produced_no_filesystem", sif=sif, detail=problem)
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
