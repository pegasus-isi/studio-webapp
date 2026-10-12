"""Choosing a hosted site catalog and entering an allocation (#468)."""

from __future__ import annotations

import asyncio
import os
import stat

import pytest

from services import site_config as sc


def test_write_keeps_other_properties_and_round_trips(tmp_path):
    rc = tmp_path / ".pegasusrc"
    rc.write_text(
        "# my notes\n"
        "pegasus.mode = development\n"
        "pegasus.catalog.site.repo.file = old.yml\n"
        "env.RESOURCE_USERNAME = olduser\n"
    )
    got = sc.write_config(
        "unity.yml", {"RESOURCE_USERNAME": "kt", "RESOURCE_PROJECT": "pi_lab"}, rc
    )
    assert got == {
        "catalog": "unity.yml",
        "env": {"RESOURCE_USERNAME": "kt", "RESOURCE_PROJECT": "pi_lab"},
        "studio_pool": False,
    }
    text = rc.read_text()
    assert "# my notes" in text and "pegasus.mode = development" in text
    assert text.count("repo.file") == 1  # replaced in place, not duplicated
    assert stat.S_IMODE(os.stat(rc).st_mode) == 0o600


def test_clearing_the_catalog_and_a_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_SITE_REPO", str(tmp_path / "absent"))
    rc = tmp_path / ".pegasusrc"
    sc.write_config("unity.yml", {"RESOURCE_PROJECT": "pi_lab"}, rc)
    got = sc.write_config(None, {"RESOURCE_PROJECT": ""}, rc)
    assert got == {"catalog": None, "env": {}, "studio_pool": False}


@pytest.mark.parametrize(
    "catalog,env",
    [
        ("../../etc/passwd", {}),
        ("unity.yml\nevil = 1", {}),
        ("unity.yml", {"PATH": "/x"}),
    ],
)
def test_unsafe_names_are_refused(tmp_path, catalog, env):
    with pytest.raises(ValueError):
        sc.write_config(catalog, env, tmp_path / ".pegasusrc")


def test_values_cannot_inject_a_second_property(tmp_path):
    rc = tmp_path / ".pegasusrc"
    got = sc.write_config("unity.yml", {"RESOURCE_PROJECT": "lab\npegasus.x = y"}, rc)
    assert got["env"]["RESOURCE_PROJECT"] == "lab pegasus.x = y"
    assert "pegasus.x" not in [line.split(" =")[0] for line in rc.read_text().splitlines()]


def test_series_and_needed_vars():
    assert sc.series("6.0.0dev") == "6.0"
    assert sc.series("5.1.2") == "5.1"
    assert sc.series(None) is None
    text = "path: /pscratch/${RESOURCE_SCRATCH_DIR}/x\nacct: ${RESOURCE_PROJECT}\n${RESOURCE_PROJECT} ${HOME}"
    assert sc.needed_vars(text) == ["RESOURCE_SCRATCH_DIR", "RESOURCE_PROJECT"]


def test_endpoint_reads_writes_and_offers_compute(client, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    async def catalogs(ref):
        return [{"name": "unity.yml", "label": "Unity (UMass)", "needs": ["RESOURCE_PROJECT"]}]

    async def accounts():
        return ["pi_lab", "pi_other"]

    async def version(cmd):
        return "6.0.0dev"

    monkeypatch.setattr(sc, "list_catalogs", catalogs)
    monkeypatch.setattr(sc, "slurm_accounts", accounts)
    monkeypatch.setattr("routers.health._run_version_cmd", version)

    body = client.get("/api/settings/compute-site").json()
    assert body["catalog"] is None and body["site"] == "condorpool"
    assert body["pegasus_series"] == "6.0" and body["slurm_accounts"] == ["pi_lab", "pi_other"]

    saved = client.put(
        "/api/settings/compute-site",
        json={"catalog": "unity.yml", "env": {"RESOURCE_PROJECT": "pi_lab"}},
    ).json()
    assert saved["catalog"] == "unity.yml" and saved["site"] == "compute"
    assert (tmp_path / ".pegasusrc").read_text().startswith(
        "pegasus.catalog.site.repo.file = unity.yml")

    # "Where it runs" now offers and defaults to the catalog's site.
    from services.execution_sites import execution_sites

    proj = tmp_path / "proj"
    proj.mkdir()
    assert execution_sites(str(proj), environ={})["default"] == "compute"

    bad = client.put("/api/settings/compute-site", json={"catalog": "../x.yml"})
    assert bad.status_code == 400


def test_slurm_accounts_empty_without_sacctmgr(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which", lambda name: None)
    assert asyncio.run(sc.slurm_accounts()) == []


# ── Studio's own pool as a hosted-style catalog (#474) ──


def _studio_repo(tmp_path, monkeypatch):
    repo = tmp_path / "site-catalogs"
    (repo / "6.0" / "conf").mkdir(parents=True)
    (repo / "6.0" / "conf" / "pegasus-studio.yml").write_text("sites: []\n")
    monkeypatch.setenv("STUDIO_SITE_REPO", str(repo))
    return repo


def test_no_cluster_means_studio_pool_and_back(tmp_path, monkeypatch):
    repo = _studio_repo(tmp_path, monkeypatch)
    rc = tmp_path / ".pegasusrc"

    got = sc.write_config(None, {}, rc)
    assert got == {"catalog": None, "env": {}, "studio_pool": True}
    text = rc.read_text()
    assert f"pegasus.catalog.site.repo = file://{repo}" in text
    assert "pegasus.catalog.site.repo.file = pegasus-studio.yml" in text
    assert "pegasus.catalog.site.repo.interval = 0" in text

    # A published cluster drops the local base, so Pegasus fetches from GitHub.
    got = sc.write_config("unity.yml", {}, rc)
    assert got["catalog"] == "unity.yml" and got["studio_pool"] is False
    assert "pegasus.catalog.site.repo =" not in rc.read_text()
    assert "repo.interval" not in rc.read_text()

    assert sc.write_config(None, {}, rc)["studio_pool"] is True


def test_without_the_image_catalog_no_cluster_clears_everything(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_SITE_REPO", str(tmp_path / "absent"))
    rc = tmp_path / ".pegasusrc"
    sc.write_config("unity.yml", {}, rc)
    assert sc.write_config(None, {}, rc) == {"catalog": None, "env": {}, "studio_pool": False}


def test_studio_pool_reports_compute_and_where_it_runs_follows(client, monkeypatch, tmp_path):
    _studio_repo(tmp_path, monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))

    async def catalogs(ref):
        return [{"name": "pegasus-studio.yml", "label": "x", "needs": []},
                {"name": "unity.yml", "label": "Unity", "needs": []}]

    async def accounts():
        return []

    async def version(cmd):
        return "6.0.0dev"

    monkeypatch.setattr(sc, "list_catalogs", catalogs)
    monkeypatch.setattr(sc, "slurm_accounts", accounts)
    monkeypatch.setattr("routers.health._run_version_cmd", version)

    body = client.put("/api/settings/compute-site", json={"catalog": None, "env": {}}).json()
    assert body["studio_pool"] is True and body["site"] == "compute" and body["catalog"] is None
    # Studio's own catalog is not offered as a published cluster.
    assert [c["name"] for c in body["catalogs"]] == ["unity.yml"]

    from services.execution_sites import execution_sites, site_problem

    proj = tmp_path / "proj"
    proj.mkdir()
    assert execution_sites(str(proj), environ={})["default"] == "compute"
    assert site_problem(str(proj), "compute", environ={}) is None


def test_the_shipped_catalog_defines_compute_and_condorpool():
    import pathlib

    import yaml

    shipped = pathlib.Path(__file__).resolve().parents[2] / "docker" / "pegasus" / "site-catalogs" / "pegasus-studio.yml"
    doc = yaml.safe_load(shipped.read_text())
    sites = {s["name"]: s for s in doc["sites"]}
    assert set(sites) == {"compute", "condorpool"}
    for s in sites.values():
        assert s["profiles"]["pegasus"]["style"] == "condor"
        assert s["profiles"]["condor"]["universe"] == "vanilla"
