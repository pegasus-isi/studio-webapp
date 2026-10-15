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
    monkeypatch.setattr(annex, "_config", {"enabled": "true", "collector": "ap:9618?sock=ap_collector", "require_job": "true"})
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
    assert first["requires_jobs"] is True
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


# --- bundle and actions (#534) ---


def _fake_create(tmp_path, monkeypatch, rc=0):
    calls = []

    async def run(cmd, timeout, cwd=None):
        calls.append((cmd, cwd))
        if cmd[:3] == ["htcondor", "annex", "create"] and rc == 0:
            (__import__("pathlib").Path(cwd) / f"annex-{cmd[3]}.tar").write_bytes(b"tar!")
            return 0, "Please copy the file annex-alice.tar to the HPC system"
        if cmd[:3] == ["htcondor", "annex", "shutdown"]:
            return 0, "done"
        return rc, "annex create failed: no token"

    monkeypatch.setattr(annex, "_run_text", run)
    monkeypatch.setattr(annex, "annex_name", lambda: "alice")
    monkeypatch.setenv("STUDIO_ANNEX_DIR", str(tmp_path / "annex"))
    monkeypatch.setenv("STUDIO_DEPLOYMENT", "hosted")
    return calls


def test_create_bundle_keeps_newest_only(tmp_path, monkeypatch):
    calls = _fake_create(tmp_path, monkeypatch)
    first = asyncio.run(annex.create_bundle())
    assert first["ok"] and first["bundle"]["filename"] == "annex-alice.tar"
    # A second create replaces the first (each holds a live token), even
    # within the same second: directories are unique, not timestamps.
    second = asyncio.run(annex.create_bundle())
    assert second["bundle"]["path"] != first["bundle"]["path"]
    assert len(list((tmp_path / "annex").iterdir())) == 1
    assert annex.current_bundle()["path"] == second["bundle"]["path"]
    # Nothing is left staging, and staging is never "current".
    (tmp_path / "annex" / ".staging-x").mkdir()
    (tmp_path / "annex" / ".staging-x" / "annex-alice.tar").write_bytes(b"half")
    assert annex.current_bundle()["path"] == second["bundle"]["path"]
    assert all(cmd[:3] == ["htcondor", "annex", "create"] for cmd, _ in calls)


def test_create_bundle_failure_leaves_nothing(tmp_path, monkeypatch):
    _fake_create(tmp_path, monkeypatch, rc=1)
    res = asyncio.run(annex.create_bundle())
    assert res["ok"] is False and "no token" in res["error"]
    assert annex.current_bundle() is None


def test_create_bundle_with_empty_queue_is_said_plainly(tmp_path, monkeypatch):
    _fake_create(tmp_path, monkeypatch)

    async def run(cmd, timeout, cwd=None):
        return 1, "No jobs for 'alice' are in the queue. Use 'htcondor job submit --annex-name' to add them first."

    monkeypatch.setattr(annex, "_run_text", run)
    res = asyncio.run(annex.create_bundle())
    assert res["ok"] is False
    assert res["error"].startswith("Nothing is queued for your annex yet")
    assert "htcondor job submit" not in res["error"]


def test_bundle_endpoints(client, tmp_path, monkeypatch):
    _fake_create(tmp_path, monkeypatch)
    assert client.get("/api/provisioning/annex/bundle").status_code == 404
    res = client.post("/api/provisioning/annex/bundle")
    assert res.status_code == 200 and res.json()["bundle"]["filename"] == "annex-alice.tar"
    dl = client.get("/api/provisioning/annex/bundle")
    assert dl.status_code == 200 and dl.content == b"tar!"
    assert "attachment" in dl.headers["content-disposition"]
    assert "annex-alice.tar" in dl.headers["content-disposition"]
    res = client.post("/api/provisioning/annex/shutdown")
    assert res.status_code == 200 and res.json()["ok"] is True


def test_annex_actions_refused_on_ood(client, tmp_path, monkeypatch):
    _fake_create(tmp_path, monkeypatch)
    monkeypatch.setenv("STUDIO_DEPLOYMENT", "ood")
    assert client.post("/api/provisioning/annex/bundle").status_code == 404
    assert client.post("/api/provisioning/annex/shutdown").status_code == 404
    assert client.get("/api/provisioning/annex/bundle").status_code == 404


def test_status_reports_bundle(tmp_path, monkeypatch):
    _fake_create(tmp_path, monkeypatch)
    annex._cache["data"] = None
    monkeypatch.setattr(annex, "_config", {"enabled": "", "collector": ""})
    asyncio.run(annex.create_bundle())
    s = asyncio.run(annex.get_status(force=True))
    assert s["bundle"]["filename"] == "annex-alice.tar"

