"""Is this workspace's Pegasus compatible with the submit host's?

Studio splits a thing Pegasus assumes is one machine. Pegasus defines the
``local`` site as "the workflow submit node where Pegasus **and** the HTCondor
Schedd is installed", and reserves it for auxiliary jobs in the HTCondor
*local* universe — ``create_dir``, ``stage_in``, ``cleanup``, ``register``.
Here the planner runs in the user's pod and the schedd runs on a remote access
point, so those jobs are *submitted* with paths from the pod's Pegasus and
*executed* against the AP's.

When the two installs differ, every workflow dies on its first node with

    Failed to execute '…/site-packages/Pegasus/data/sh/pegasus-lite-local.sh'
    … (errno=2: 'No such file or directory')

and nothing in the failure names the real cause. Observed 2026-09-02: the pod
resolved that file under ``python3.12`` (Ubuntu noble) and the AP under
``python3.13`` (Debian trixie) — same Pegasus version, same apt channel,
different distro, so one Python minor version made every run fail 25 nodes
deep with an error about a missing file that was present on both machines.

This module turns that into a preflight. The AP advertises its Pegasus
identity as schedd ClassAd attributes, which ``condor_status`` reads in
milliseconds without submitting anything, so the check is cheap enough to run
before every flow.

The attributes are set on the AP (``studio-system``, salt-managed):

    PegasusShareDir = "/usr/lib/pegasus/venv/lib/python3.13/site-packages/Pegasus/data"
    PegasusVersion  = "6.0.0.dev0"
    SCHEDD_ATTRS = $(SCHEDD_ATTRS) PegasusShareDir PegasusVersion

An AP that has not been updated advertises neither, which must **not** block
runs — see `Compat.status == "unknown"`.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from typing import Any, Literal

import structlog

log = structlog.get_logger()

_CMD_TIMEOUT = float(os.environ.get("STUDIO_SUBMIT_HOST_TIMEOUT", "10"))

Status = Literal["ok", "mismatch", "unknown", "unavailable"]


@dataclass
class Compat:
    """The verdict, and everything needed to explain it to a human."""

    status: Status
    detail: str
    local_version: str | None = None
    local_share_dir: str | None = None
    remote_version: str | None = None
    remote_share_dir: str | None = None
    schedd: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "local_version": self.local_version,
            "local_share_dir": self.local_share_dir,
            "remote_version": self.remote_version,
            "remote_share_dir": self.remote_share_dir,
            "schedd": self.schedd,
        }

    @property
    def blocks_run(self) -> bool:
        """Only a positively-identified mismatch stops a run.

        `unknown` and `unavailable` must not: an AP that predates the ClassAd
        attributes, or a momentarily unreachable collector, would otherwise
        make Studio refuse to run anything at all — a worse failure than the
        one this guards against.
        """
        return self.status == "mismatch"


async def _run(cmd: list[str]) -> tuple[str | None, str | None]:
    """Run a command, returning (stdout, error)."""
    if not shutil.which(cmd[0]):
        return None, f"{cmd[0]} not found"
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_CMD_TIMEOUT
        )
    except asyncio.TimeoutError:
        return None, f"{cmd[0]} timed out"
    except OSError as e:
        return None, f"{cmd[0]} failed: {e}"
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        return None, err.splitlines()[0][:200] if err else f"{cmd[0]} failed"
    return stdout.decode("utf-8", errors="replace"), None


def _share_dir_from_sh_dump(dump: str) -> str | None:
    """Pull PEGASUS_SHARE_DIR out of `pegasus-config --sh-dump`.

    `--sh-dump` is the documented way to resolve Pegasus's own layout, which is
    why it is used rather than globbing for a `python3.*` directory: the glob
    would silently pick one of two if a machine ever had both.
    """
    for line in dump.splitlines():
        line = line.strip()
        if not line.startswith("PEGASUS_SHARE_DIR"):
            continue
        _, _, value = line.partition("=")
        return value.strip().strip(";").strip().strip('"').strip("'") or None
    return None


async def local_identity() -> tuple[str | None, str | None]:
    """This workspace's (version, share dir)."""
    version_out, _ = await _run(["pegasus-version"])
    dump, _ = await _run(["pegasus-config", "--sh-dump"])
    version = version_out.strip().splitlines()[0].strip() if version_out else None
    return version, _share_dir_from_sh_dump(dump) if dump else None


async def remote_identity() -> tuple[str | None, str | None, str | None, str | None]:
    """The submit host's (version, share dir, schedd name, error).

    Read from schedd ClassAds rather than by submitting a probe job: the whole
    point is to be cheap enough to run before every flow.
    """
    out, err = await _run(
        [
            "condor_status",
            "-schedd",
            "-json",
            "-attributes",
            "Name,PegasusVersion,PegasusShareDir",
        ]
    )
    if err:
        return None, None, None, err
    if not out or not out.strip():
        return None, None, None, "no schedd found"
    try:
        ads = json.loads(out)
    except json.JSONDecodeError as e:
        return None, None, None, f"unparseable condor_status output: {e}"
    if not isinstance(ads, list) or not ads:
        return None, None, None, "no schedd found"

    # Lower-cased: ClassAd attribute names are case-insensitive.
    ad = {str(k).lower(): v for k, v in ads[0].items()}
    name = ad.get("name")
    return (
        ad.get("pegasusversion"),
        ad.get("pegasussharedir"),
        str(name) if name else None,
        None,
    )


async def check() -> Compat:
    """Compare this workspace's Pegasus with the submit host's."""
    local_version, local_share = await local_identity()
    remote_version, remote_share, schedd, err = await remote_identity()

    common = {
        "local_version": local_version,
        "local_share_dir": local_share,
        "remote_version": remote_version,
        "remote_share_dir": remote_share,
        "schedd": schedd,
    }

    if err:
        return Compat(
            status="unavailable",
            detail=f"Couldn't reach the submit host to check it: {err}",
            **common,
        )

    if not remote_share:
        # An AP that predates the ClassAd attributes. Say so rather than
        # implying everything is fine, but do not block.
        return Compat(
            status="unknown",
            detail=(
                "The submit host isn't reporting which Pegasus it has, so this "
                "can't be checked. If runs fail immediately on their first "
                "step, this is the first thing to rule out."
            ),
            **common,
        )

    if local_share and remote_share and local_share != remote_share:
        return Compat(
            status="mismatch",
            detail=(
                "This workspace and the submit host have Pegasus installed in "
                "different places. Auxiliary jobs are submitted with this "
                "workspace's paths but run on the submit host, so every "
                "workflow would fail on its first step."
            ),
            **common,
        )

    if local_version and remote_version and local_version != remote_version:
        return Compat(
            status="mismatch",
            detail=(
                "This workspace and the submit host are running different "
                "Pegasus versions. Planning here and submitting there can "
                "fail in ways that don't name the version as the cause."
            ),
            **common,
        )

    return Compat(
        status="ok",
        detail="This workspace and the submit host agree on Pegasus.",
        **common,
    )
