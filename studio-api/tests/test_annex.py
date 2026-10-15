"""services/annex: the user's HTCondor annex, as htcondor annex status reads it."""

from __future__ import annotations

import asyncio

from services import annex


def _request(rid, status=1):
    return {"hpc_annex_name": "alice", "hpc_annex_request_id": rid, "JobStatus": status}


def _slot(rid, cpus=8, busy=(2, 1), start=1000, stop=5000):
    return {
        "Name": f"slot1@node-{rid}",
        "PartitionableSlot": True,
        "TotalSlotCpus": cpus,
        "ChildCpus": list(busy),
        "DaemonStartTime": start,
        "DaemonStopTime": stop,
        "hpc_annex_request_id": rid,
    }


def test_summarize_nothing_requested():
    s = annex.summarize("alice", [], [], [])
    assert s["established"] is False
    assert s["nodes"] == 0 and s["total_cpus"] == 0
    assert s["requests"] == {"total": 0, "pending": 0, "active": 0, "retired": 0}
    assert s["jobs"] == {"total": 0, "running": 0, "idle": 0, "held": 0}


def test_summarize_states_mirror_upstream():
    # r1: slots registered -> active; r2: granted (idle request job) but no
    # slots -> retired; r3: request job not idle -> pending.
    s = annex.summarize(
        "alice",
        [_request("r1"), _request("r2"), _request("r3", status=2)],
        [_slot("r1"), _slot("r1", start=900, stop=4000)],
        [{"JobStatus": 2}, {"JobStatus": 1}, {"JobStatus": 5}],
    )
    assert s["established"] is True
    assert s["nodes"] == 2
    assert s["total_cpus"] == 16
    assert s["busy_cpus"] == 6
    assert s["requests"] == {"total": 3, "pending": 1, "active": 1, "retired": 1}
    assert s["jobs"] == {"total": 3, "running": 1, "idle": 1, "held": 1}
    assert s["oldest_started_at"] == 900
    assert s["oldest_retires_at"] == 4000


def test_summarize_ignores_dynamic_slots_and_is_case_insensitive():
    dyn = {"name": "slot1_1@node", "partitionableslot": False, "totalslotcpus": 4,
           "hpc_annex_request_id": "r1"}
    s = annex.summarize("alice", [_request("r1")], [_slot("r1"), dyn], [])
    assert s["nodes"] == 1 and s["total_cpus"] == 8


def test_get_status_not_enabled(monkeypatch):
    annex._cache["data"] = None
    monkeypatch.setattr(annex, "_config", {"enabled": "", "collector": ""})
    monkeypatch.setattr(annex, "annex_name", lambda: "alice")
    s = asyncio.run(annex.get_status(force=True))
    assert s["available"] is False
    assert "not enabled" in s["error"]
    assert s["name"] == "alice"


def test_get_status_queries_and_caches(monkeypatch):
    annex._cache["data"] = None
    monkeypatch.setattr(annex, "_config", {"enabled": "true", "collector": "ap:9618?sock=ap_collector"})
    monkeypatch.setattr(annex, "annex_name", lambda: "alice")
    calls = []

    async def fake_run(cmd, timeout=0):
        calls.append(cmd)
        if cmd[0] == "condor_status":
            assert cmd[cmd.index("-pool") + 1] == "ap:9618?sock=ap_collector"
            assert 'AnnexName == "alice"' in cmd
            return [_slot("r1")], None
        if "hpc_annex_name ==" in " ".join(cmd):
            return [_request("r1")], None
        return [{"JobStatus": 2}], None

    monkeypatch.setattr(annex, "_run_json", fake_run)

    async def go():
        first = await annex.get_status(force=True)
        second = await annex.get_status()
        return first, second

    first, second = asyncio.run(go())
    assert first["available"] is True
    assert first["established"] is True and first["jobs"]["running"] == 1
    assert second is first
    assert len(calls) == 3


def test_get_status_collector_down_is_unknown_not_empty(monkeypatch):
    # The annex collector always runs on the access point, so failing to
    # reach it is an outage; an empty slot list would retire every request.
    annex._cache["data"] = None
    monkeypatch.setattr(annex, "_config", {"enabled": "true", "collector": "ap:9618"})
    monkeypatch.setattr(annex, "annex_name", lambda: "alice")

    async def fake_run(cmd, timeout=0):
        if cmd[0] == "condor_status":
            return None, "condor_status failed"
        return [_request("r1")], None

    monkeypatch.setattr(annex, "_run_json", fake_run)
    s = asyncio.run(annex.get_status(force=True))
    assert s["available"] is False
    assert s["error"] == "condor_status failed"
    assert "requests" not in s


def test_get_status_refuses_odd_name(monkeypatch):
    annex._cache["data"] = None
    monkeypatch.setattr(annex, "annex_name", lambda: 'x" || true')
    s = asyncio.run(annex.get_status(force=True))
    assert s["available"] is False and "unusable" in s["error"]


def test_deployment_detection(monkeypatch, tmp_path):
    from services import site_config

    for var in ("STUDIO_DEPLOYMENT", "STUDIO_SITE_CATALOG_FILE", "STUDIO_SITE_CATALOG"):
        monkeypatch.delenv(var, raising=False)
    assert site_config.deployment() == "hosted"
    monkeypatch.setenv("STUDIO_SITE_CATALOG", "unity.yml")
    assert site_config.deployment() == "ood"
    monkeypatch.delenv("STUDIO_SITE_CATALOG")
    cat = tmp_path / "site.yml"
    cat.write_text("sites: []\n")
    monkeypatch.setenv("STUDIO_SITE_CATALOG_FILE", str(cat))
    assert site_config.deployment() == "ood"
    monkeypatch.setenv("STUDIO_DEPLOYMENT", "HOSTED")
    assert site_config.deployment() == "hosted"
