"""Tests for the condor pool inventory / demand / pressure service."""

from __future__ import annotations

import asyncio

from services import condor_pool


def _slot(**kw):
    ad = {
        "SlotType": "Static",
        "State": "Unclaimed",
        "Machine": "node1",
        "Cpus": 4,
        "Memory": 8192,
    }
    ad.update(kw)
    return ad


def _job(**kw):
    ad = {"JobStatus": 1, "RequestCpus": 1, "RequestMemory": 1024, "QDate": 0}
    ad.update(kw)
    return ad


# --- summarize_slots ---


def test_slots_static_claimed_vs_unclaimed():
    pool = condor_pool.summarize_slots([
        _slot(Machine="a", Cpus=8),
        _slot(Machine="b", Cpus=8, State="Claimed"),
    ])
    assert pool["total_cpus"] == 16
    assert pool["idle_cpus"] == 8
    assert pool["machines"] == 2
    assert pool["slots"] == 2


def test_slots_partitionable_counts_capacity_once():
    # Partitionable slot with 12 remaining of 16; its dynamic child (claimed 4)
    # must not double count.
    pool = condor_pool.summarize_slots([
        _slot(
            SlotType="Partitionable", Cpus=12, TotalSlotCpus=16,
            GPUs=1, TotalSlotGPUs=2, Memory=24576, TotalSlotMemory=32768,
        ),
        _slot(SlotType="Dynamic", State="Claimed", Cpus=4, TotalSlotCpus=4),
    ])
    assert pool["total_cpus"] == 16
    assert pool["idle_cpus"] == 12
    assert pool["total_gpus"] == 2
    assert pool["idle_gpus"] == 1
    assert pool["total_memory_mb"] == 32768
    assert pool["idle_memory_mb"] == 24576
    assert pool["slots"] == 1  # dynamic child skipped


def test_slots_grouping_annex_glidein_base():
    pool = condor_pool.summarize_slots([
        _slot(Machine="base1"),
        _slot(Machine="annex1", AnnexName="my-annex"),
        _slot(Machine="osp1", GLIDEIN_Site="OSG_US_FNAL"),
    ])
    assert set(pool["pools"]) == {"base", "annex", "glidein"}
    assert pool["pools"]["annex"]["label"] == "Provisioned (Annex)"
    assert pool["pools"]["annex"]["total_cpus"] == 4
    assert pool["pools"]["base"]["machines"] == 1


def test_slots_case_insensitive_attrs():
    pool = condor_pool.summarize_slots([
        {"slottype": "static", "state": "unclaimed", "cpus": 2, "machine": "x"}
    ])
    assert pool["total_cpus"] == 2
    assert pool["idle_cpus"] == 2


# --- summarize_queue ---


def test_queue_counts_and_requests():
    now = 1_000_000.0
    demand = condor_pool.summarize_queue(
        [
            _job(QDate=999_400, RequestCpus=4, RequestGPUs=1),
            _job(JobStatus=2),
            _job(JobStatus=5, HoldReason="No matching resources"),
        ],
        now=now,
    )
    assert demand["total_jobs"] == 3
    assert demand["idle_jobs"] == 1
    assert demand["running_jobs"] == 1
    assert demand["held_jobs"] == 1
    assert demand["idle_cpus_requested"] == 4
    assert demand["idle_gpus_requested"] == 1
    assert demand["oldest_idle_seconds"] == 600
    assert demand["held_reasons"] == ["No matching resources"]


def test_queue_groups_by_pegasus_workflow():
    demand = condor_pool.summarize_queue(
        [
            _job(pegasus_wf_uuid="wf-1", pegasus_wf_name="csv_summary"),
            _job(pegasus_wf_uuid="wf-1", JobStatus=2),
            _job(JobStatus=2),  # non-pegasus job: counted globally only
        ],
        now=100.0,
    )
    assert demand["running_jobs"] == 2
    wf = demand["by_workflow"]["wf-1"]
    assert wf["name"] == "csv_summary"
    assert wf["idle_jobs"] == 1
    assert wf["running_jobs"] == 1


# --- pressure ---


def _pool(idle_cpus=0, idle_gpus=0):
    return {
        "available": True,
        "total_cpus": 16,
        "idle_cpus": idle_cpus,
        "total_gpus": 2,
        "idle_gpus": idle_gpus,
        "total_memory_mb": 0,
        "idle_memory_mb": 0,
    }


def test_evaluate_pressure_cpu_and_gpu_gap():
    demand = condor_pool.summarize_queue(
        [_job(RequestCpus=8, RequestGPUs=1)], now=100.0
    )
    findings = condor_pool.evaluate_pressure(_pool(idle_cpus=2), demand)
    kinds = [f["kind"] for f in findings]
    assert "cpu_shortage" in kinds
    assert "gpu_shortage" in kinds
    cpu = next(f for f in findings if f["kind"] == "cpu_shortage")
    assert cpu["delta"] == {"cpus": 6}


def test_evaluate_pressure_quiet_when_capacity_fits():
    demand = condor_pool.summarize_queue([_job(RequestCpus=2)], now=100.0)
    assert condor_pool.evaluate_pressure(_pool(idle_cpus=4), demand) == []


def test_run_pressure_priority_gpu_over_cpu():
    demand = condor_pool.summarize_queue(
        [_job(pegasus_wf_uuid="wf-1", RequestCpus=8, RequestGPUs=1)],
        now=100.0,
    )
    finding = condor_pool.run_pressure(
        demand["by_workflow"]["wf-1"], _pool(idle_cpus=0, idle_gpus=0)
    )
    assert finding["kind"] == "gpu_shortage"
    assert finding["delta"] == {"gpus": 1}


def test_run_pressure_long_wait(monkeypatch):
    monkeypatch.setattr(condor_pool, "WAIT_THRESHOLD", 300)
    demand = condor_pool.summarize_queue(
        [_job(pegasus_wf_uuid="wf-1", QDate=100)], now=1_000.0
    )
    finding = condor_pool.run_pressure(
        demand["by_workflow"]["wf-1"], _pool(idle_cpus=4)
    )
    assert finding["kind"] == "long_wait"


def test_run_pressure_none_without_demand():
    assert condor_pool.run_pressure(None, _pool()) is None


# --- cached snapshot + endpoint ---


def test_get_status_unavailable(monkeypatch):
    async def fake_run(cmd, timeout=20):
        return None, f"{cmd[0]} not found"

    monkeypatch.setattr(condor_pool, "_run_json", fake_run)
    monkeypatch.setattr(condor_pool, "OSPOOL_CM", "")  # isolate from OSPool
    condor_pool._cache["data"] = None
    condor_pool._ospool_cache["data"] = None

    status = asyncio.run(condor_pool.get_status(force=True))
    assert status["available"] is False
    assert status["pool"]["error"] == "condor_status not found"
    assert status["pressure"] == []


def test_get_status_caches(monkeypatch):
    calls = {"n": 0}

    async def fake_run(cmd, timeout=20):
        calls["n"] += 1
        return [], None

    monkeypatch.setattr(condor_pool, "_run_json", fake_run)
    monkeypatch.setattr(condor_pool, "OSPOOL_CM", "")  # isolate from OSPool
    condor_pool._cache["data"] = None
    condor_pool._ospool_cache["data"] = None

    async def go():
        first = await condor_pool.get_status(force=True)
        second = await condor_pool.get_status()
        return first, second

    first, second = asyncio.run(go())
    assert first["available"] is True
    assert second is first
    assert calls["n"] == 2  # one condor_status + one condor_q, no refetch


def test_ospool_status_uses_attribute_projection(monkeypatch):
    # OSPool has ~38k slots; the full -json query times out. get_ospool_status
    # must project to only the attributes summarize_slots reads.
    captured: dict = {}

    async def fake_run(cmd, timeout=20):
        captured["cmd"] = cmd
        return [
            {"SlotType": "Partitionable", "State": "Unclaimed",
             "Cpus": 2, "TotalSlotCpus": 2, "Memory": 8000},
        ], None

    monkeypatch.setattr(condor_pool, "_run_json", fake_run)
    monkeypatch.setattr(condor_pool, "OSPOOL_CM", "cm.example")
    condor_pool._ospool_cache["data"] = None

    status = asyncio.run(condor_pool.get_ospool_status(force=True))
    assert status["available"] is True
    assert status["cm"] == "cm.example"
    cmd = captured["cmd"]
    assert "-attributes" in cmd
    attrs = cmd[cmd.index("-attributes") + 1]
    for a in ("SlotType", "State", "Cpus", "GPUs", "TotalSlotCpus", "GLIDEIN_Site"):
        assert a in attrs


def test_ospool_status_disabled(monkeypatch):
    monkeypatch.setattr(condor_pool, "OSPOOL_CM", "")
    status = asyncio.run(condor_pool.get_ospool_status(force=True))
    assert status["available"] is False


def test_provisioning_endpoint(client, monkeypatch):
    async def fake_status(force=False):
        return {"available": False, "pool": {}, "demand": {}, "pressure": []}

    monkeypatch.setattr(condor_pool, "get_status", fake_status)
    res = client.get("/api/provisioning/status")
    assert res.status_code == 200
    assert res.json()["available"] is False


# --- OSPool ---


def test_ospool_status_summarizes(monkeypatch):
    async def fake_run(cmd, timeout=20):
        # Only the OSPool query (has -pool) returns slots here.
        if "-pool" in cmd:
            return [_slot(Machine="osg1", Cpus=16, State="Unclaimed")], None
        return [], None

    monkeypatch.setattr(condor_pool, "_run_json", fake_run)
    monkeypatch.setattr(condor_pool, "OSPOOL_CM", "cm-1.ospool.osg-htc.org")
    condor_pool._ospool_cache["data"] = None

    data = asyncio.run(condor_pool.get_ospool_status(force=True))
    assert data["available"] is True
    assert data["total_cpus"] == 16
    assert data["cm"] == "cm-1.ospool.osg-htc.org"


def test_ospool_disabled(monkeypatch):
    monkeypatch.setattr(condor_pool, "OSPOOL_CM", "")
    condor_pool._ospool_cache["data"] = None
    data = asyncio.run(condor_pool.get_ospool_status(force=True))
    assert data["available"] is False


def test_ospool_uses_pool_flag_and_timeout(monkeypatch):
    seen = {}

    async def fake_run(cmd, timeout=20):
        seen["cmd"] = cmd
        seen["timeout"] = timeout
        return [], None

    monkeypatch.setattr(condor_pool, "_run_json", fake_run)
    monkeypatch.setattr(condor_pool, "OSPOOL_CM", "cm-1.ospool.osg-htc.org")
    monkeypatch.setattr(condor_pool, "_OSPOOL_CMD_TIMEOUT", 45)
    condor_pool._ospool_cache["data"] = None

    asyncio.run(condor_pool.get_ospool_status(force=True))
    assert seen["cmd"][:4] == [
        "condor_status", "-pool", "cm-1.ospool.osg-htc.org", "-json"
    ]
    # Projected to the needed attributes — the full query times out on ~38k slots.
    assert "-attributes" in seen["cmd"]
    assert seen["timeout"] == 45


def test_get_status_includes_ospool(monkeypatch):
    async def fake_run(cmd, timeout=20):
        return [], None

    monkeypatch.setattr(condor_pool, "_run_json", fake_run)
    monkeypatch.setattr(condor_pool, "OSPOOL_CM", "cm-1.ospool.osg-htc.org")
    condor_pool._cache["data"] = None
    condor_pool._ospool_cache["data"] = None

    status = asyncio.run(condor_pool.get_status(force=True))
    assert "ospool" in status
    assert status["ospool"]["available"] is True
