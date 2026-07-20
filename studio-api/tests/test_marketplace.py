"""Tests for the workflow Marketplace (PegasusHub registry + Pegasus-ISI, GitHub)."""

from __future__ import annotations

import routers.marketplace as mkt


def _reset():
    mkt._cache["data"] = None
    mkt._cache["ts"] = 0.0


def test_catalog_merges_registry_and_curated_deduped(client, monkeypatch):
    # _fetch_registry returns already-normalized entries (repo_url, source, …).
    async def fake_registry():
        return [
            mkt._normalize(
                {
                    "id": "variant-calling",
                    "name": "variant-calling",
                    # Duplicate of a curated repo (different case + .git) -> deduped.
                    "repo_url": "https://GitHub.com/pegasus-isi/variant-calling-pegasus-workflow.git",
                    "topic": "bioinformatics",
                },
                "PegasusHub",
            ),
            mkt._normalize(
                {
                    "id": "orcasound",
                    "name": "orcasound-workflow",
                    "github_repo": "https://github.com/pegasus-isi/orcasound-workflow",
                    "topic": "machine learning",
                },
                "PegasusHub",
            ),
        ]

    monkeypatch.setattr(mkt, "_fetch_registry", fake_registry)
    _reset()

    body = client.get("/api/marketplace").json()
    wfs = {w["id"]: w for w in body["workflows"]}

    # All 6 curated + 1 unique registry = 7; the dup collapses onto curated.
    assert body["count"] == 7
    assert "orcasound" in wfs and wfs["orcasound"]["source"] == "PegasusHub"
    # variant-calling appears once, as the curated Pegasus-ISI entry.
    vc = [w for w in body["workflows"] if "variant-calling" in w["repo_url"].lower()]
    assert len(vc) == 1
    assert vc[0]["source"] == "Pegasus-ISI"
    # The specific repos the user asked for are all present.
    ids = set(wfs)
    for expected in ("soilmoisture", "crophealth", "earthquake", "airquality",
                     "quickstart", "variant-calling-pegasus"):
        assert expected in ids


def test_catalog_leads_with_featured_examples(client, monkeypatch):
    """The catalog opens with the gentle-onramp trio, in order, ahead of the
    other curated/registry entries."""
    async def fake_registry():
        return [
            mkt._normalize(
                {
                    "id": "orcasound",
                    "name": "orcasound-workflow",
                    "github_repo": "https://github.com/pegasus-isi/orcasound-workflow",
                },
                "PegasusHub",
            ),
        ]

    monkeypatch.setattr(mkt, "_fetch_registry", fake_registry)
    _reset()

    ids = [w["id"] for w in client.get("/api/marketplace").json()["workflows"]]
    assert ids[:3] == ["quickstart", "soilmoisture", "variant-calling-pegasus"]
    # Non-featured entries still follow (alphabetical within their group).
    assert ids[3:6] == ["airquality", "crophealth", "earthquake"]


def test_registry_failure_falls_back_to_curated(client, monkeypatch):
    async def empty_registry():
        return []

    monkeypatch.setattr(mkt, "_fetch_registry", empty_registry)
    _reset()

    body = client.get("/api/marketplace").json()
    assert body["count"] == len(mkt._CURATED)
    assert all(w["source"] == "Pegasus-ISI" for w in body["workflows"])


def test_add_clones_selected_repo(client, monkeypatch, tmp_path):
    async def fake_registry():
        return []

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(mkt, "_fetch_registry", fake_registry)
    monkeypatch.setattr(mkt, "WORKFLOWS_DIR", str(tmp_path / "workflows"))
    monkeypatch.setattr(mkt.asyncio, "create_subprocess_exec", fake_exec)
    _reset()

    res = client.post("/api/marketplace/soilmoisture/add")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "added"
    assert data["project_id"] == "soilmoisture"
    assert "soilmoisture-workflow" in data["repo_url"]


def test_add_rejects_bad_id(client):
    assert client.post("/api/marketplace/..%2Fetc/add").status_code in (400, 404)
    assert client.post("/api/marketplace/bad id/add").status_code in (400, 404)


def test_add_unknown_id(client, monkeypatch):
    async def fake_registry():
        return []

    monkeypatch.setattr(mkt, "_fetch_registry", fake_registry)
    _reset()
    assert client.post("/api/marketplace/nonexistent/add").status_code == 404


def test_add_conflict_when_exists(client, monkeypatch, tmp_path):
    async def fake_registry():
        return []

    wdir = tmp_path / "workflows"
    (wdir / "quickstart").mkdir(parents=True)
    monkeypatch.setattr(mkt, "_fetch_registry", fake_registry)
    monkeypatch.setattr(mkt, "WORKFLOWS_DIR", str(wdir))
    _reset()

    assert client.post("/api/marketplace/quickstart/add").status_code == 409
