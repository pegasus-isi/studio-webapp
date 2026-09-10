"""The submit-host Pegasus compatibility preflight.

Regression cover for the 2026-09-02 outage: every workflow failed on its first
node because the pod resolved `pegasus-lite-local.sh` under python3.12 (Ubuntu
noble) and the AP under python3.13 (Debian trixie). Same Pegasus version, same
apt channel, different distro — and the error named a missing file that existed
on both machines.
"""

from __future__ import annotations

import asyncio
import json

import services.submit_host as sh

_POD_SHARE = "/usr/lib/pegasus/venv/lib/python3.12/site-packages/Pegasus/data"
_AP_SHARE = "/usr/lib/pegasus/venv/lib/python3.13/site-packages/Pegasus/data"


def _stub(monkeypatch, *, local, remote):
    """Replace both identity probes; no subprocesses, no network."""

    async def fake_local():
        return local

    async def fake_remote():
        return remote

    monkeypatch.setattr(sh, "local_identity", fake_local)
    monkeypatch.setattr(sh, "remote_identity", fake_remote)


def test_a_python_version_path_difference_is_a_mismatch(monkeypatch):
    """The exact shape of the outage this preflight exists for."""
    _stub(
        monkeypatch,
        local=("6.0.0.dev0", _POD_SHARE),
        remote=("6.0.0.dev0", _AP_SHARE, "ap.pegasus-ai.org", None),
    )
    c = asyncio.run(sh.check())
    assert c.status == "mismatch"
    assert c.blocks_run is True
    # The versions are identical, so the message must not blame the version.
    assert "different places" in c.detail
    assert c.schedd == "ap.pegasus-ai.org"


def test_matching_installs_are_ok(monkeypatch):
    _stub(
        monkeypatch,
        local=("6.0.0.dev0", _AP_SHARE),
        remote=("6.0.0.dev0", _AP_SHARE, "ap.pegasus-ai.org", None),
    )
    c = asyncio.run(sh.check())
    assert c.status == "ok"
    assert c.blocks_run is False


def test_same_path_different_version_is_also_a_mismatch(monkeypatch):
    _stub(
        monkeypatch,
        local=("6.0.0.dev0", _AP_SHARE),
        remote=("5.0.9", _AP_SHARE, "ap.pegasus-ai.org", None),
    )
    c = asyncio.run(sh.check())
    assert c.status == "mismatch"
    assert "versions" in c.detail


def test_an_ap_without_the_classads_does_not_block(monkeypatch):
    """The AP advertises these attributes only once salt has been applied.

    Blocking on a missing attribute would mean Studio refuses to run anything
    on every deployment that has not been updated yet — a worse failure than
    the one being guarded against.
    """
    _stub(
        monkeypatch,
        local=("6.0.0.dev0", _POD_SHARE),
        remote=(None, None, "ap.pegasus-ai.org", None),
    )
    c = asyncio.run(sh.check())
    assert c.status == "unknown"
    assert c.blocks_run is False
    assert "isn't reporting" in c.detail


def test_an_unreachable_collector_does_not_block(monkeypatch):
    _stub(
        monkeypatch,
        local=("6.0.0.dev0", _POD_SHARE),
        remote=(None, None, None, "condor_status timed out"),
    )
    c = asyncio.run(sh.check())
    assert c.status == "unavailable"
    assert c.blocks_run is False
    assert "timed out" in c.detail


def test_missing_local_pegasus_does_not_block(monkeypatch):
    """No Pegasus here means nothing to compare, not a mismatch."""
    _stub(
        monkeypatch,
        local=(None, None),
        remote=("6.0.0.dev0", _AP_SHARE, "ap.pegasus-ai.org", None),
    )
    c = asyncio.run(sh.check())
    assert c.blocks_run is False


def test_share_dir_is_read_from_the_documented_sh_dump():
    """`pegasus-config --sh-dump`, not a glob for python3.*.

    A glob would silently pick one of two if a machine ever had both.
    """
    dump = (
        'PEGASUS_BIN_DIR="/usr/lib/pegasus/venv/bin";\n'
        "export PEGASUS_BIN_DIR\n"
        f'PEGASUS_SHARE_DIR="{_POD_SHARE}"\n'
        "export PEGASUS_SHARE_DIR\n"
    )
    assert sh._share_dir_from_sh_dump(dump) == _POD_SHARE
    assert sh._share_dir_from_sh_dump("nothing here") is None


def test_classad_attribute_names_are_matched_case_insensitively(monkeypatch):
    """HTCondor ClassAd names are case-insensitive; ours must not care."""

    async def fake_run(cmd):
        return (
            json.dumps(
                [
                    {
                        "Name": "ap.pegasus-ai.org",
                        # Deliberately not the casing we asked for.
                        "PEGASUSSHAREDIR": _AP_SHARE,
                        "pegasusversion": "6.0.0.dev0",
                    }
                ]
            ),
            None,
        )

    monkeypatch.setattr(sh, "_run", fake_run)
    version, share, name, err = asyncio.run(sh.remote_identity())
    assert err is None
    assert share == _AP_SHARE
    assert version == "6.0.0.dev0"
    assert name == "ap.pegasus-ai.org"


def test_endpoint_reports_the_verdict(client, monkeypatch):
    _stub(
        monkeypatch,
        local=("6.0.0.dev0", _POD_SHARE),
        remote=("6.0.0.dev0", _AP_SHARE, "ap.pegasus-ai.org", None),
    )
    body = client.get("/api/health/submit-host").json()
    assert body["status"] == "mismatch"
    assert body["local_share_dir"] == _POD_SHARE
    assert body["remote_share_dir"] == _AP_SHARE
