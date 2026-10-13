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


def test_a_series_difference_is_warned_about_by_name():
    """#490: a 5.1 planner on the submit host cannot load Studio's 6.0 sites,
    and nothing said so before the plan failed."""
    compat = sh.Compat(
        status="mismatch",
        detail="",
        local_version="6.0.0.dev0",
        remote_version="5.1.3",
    )
    warning = sh.series_warning(compat)
    assert warning is not None
    assert "5.1.3" in warning and "6.0.0.dev0" in warning
    assert "Pegasus 6.0 only" in warning
    assert "not loaded" in warning


def test_an_unknown_submit_host_version_is_warned_about():
    """An AP that does not publish PegasusVersion is the case that hid #490."""
    compat = sh.Compat(status="unknown", detail="", local_version="6.0.0.dev0")
    warning = sh.series_warning(compat)
    assert warning is not None
    assert "PegasusVersion" in warning and "SCHEDD_ATTRS" in warning


def test_the_same_series_needs_no_warning():
    """A patch-level difference does not change the catalog directory."""
    compat = sh.Compat(
        status="mismatch", detail="", local_version="6.0.1", remote_version="6.0.0.dev0"
    )
    assert sh.series_warning(compat) is None
    ok = sh.Compat(
        status="ok", detail="", local_version="6.0.0.dev0", remote_version="6.0.0.dev0"
    )
    assert sh.series_warning(ok) is None
