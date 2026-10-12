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
        "overrides": [],
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
    assert got == {"catalog": None, "env": {}, "studio_pool": False, "overrides": []}


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
    assert got == {"catalog": None, "env": {}, "studio_pool": True, "overrides": []}
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
    assert sc.write_config(None, {}, rc) == {"catalog": None, "env": {}, "studio_pool": False, "overrides": []}


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


def test_studio_pool_points_at_the_shared_home_copy_not_the_image(tmp_path, monkeypatch):
    """The planner usually runs on the submit host, which shares the home
    directory but not /opt in the container (#476)."""
    image = tmp_path / "opt" / "site-catalogs"
    (image / "6.0" / "conf").mkdir(parents=True)
    (image / "6.0" / "conf" / "pegasus-studio.yml").write_text("sites: []\n")
    home_copy = tmp_path / "home" / ".studio" / "site-catalogs"
    monkeypatch.setenv("STUDIO_SITE_REPO_IMAGE", str(image))
    monkeypatch.setenv("STUDIO_SITE_REPO", str(home_copy))
    rc = tmp_path / "home" / ".pegasusrc"

    got = sc.write_config(None, {}, rc)
    assert got["studio_pool"] is True
    assert (home_copy / "6.0" / "conf" / "pegasus-studio.yml").is_file()
    text = rc.read_text()
    assert f"pegasus.catalog.site.repo = file://{home_copy}" in text
    assert str(image) not in text


# ── Site overrides (#478) ──


def test_overrides_round_trip_and_keep_other_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_SITE_REPO", str(tmp_path / "absent"))
    monkeypatch.setenv("STUDIO_SITE_REPO_IMAGE", str(tmp_path / "absent"))
    rc = tmp_path / ".pegasusrc"
    rc.write_text("pegasus.mode = development\n")
    rows = [
        {"site": "compute", "namespace": "condor", "key": "requirements", "value": "TestPool =!= True"},
        {"site": "compute", "namespace": "condor", "key": "+ProjectName", "value": '"my_lab"'},
        {"site": "local", "namespace": "pegasus", "key": "SSH_PRIVATE_KEY", "value": "/home/u/.ssh/nersc"},
    ]
    got = sc.write_config("unity.yml", {}, rc, overrides=rows)
    assert sorted((o["site"], o["key"]) for o in got["overrides"]) == [
        ("compute", "+ProjectName"), ("compute", "requirements"), ("local", "SSH_PRIVATE_KEY")]
    text = rc.read_text()
    assert "pegasus.catalog.site.sites.compute.profiles.condor.requirements = TestPool =!= True" in text
    assert "pegasus.catalog.site.sites.compute.profiles.condor.+ProjectName = \"my_lab\"" in text
    assert "pegasus.mode = development" in text

    # A new set replaces the old one entirely; None leaves it alone.
    got = sc.write_config("unity.yml", {}, rc, overrides=rows[:1])
    assert [o["key"] for o in got["overrides"]] == ["requirements"]
    got = sc.write_config("unity.yml", {}, rc, overrides=None)
    assert [o["key"] for o in got["overrides"]] == ["requirements"]


@pytest.mark.parametrize("row", [
    {"site": "comp ute", "namespace": "condor", "key": "x", "value": "1"},
    {"site": "compute", "namespace": "shell", "key": "x", "value": "1"},
    {"site": "compute", "namespace": "condor", "key": "a b", "value": "1"},
    {"site": "compute", "namespace": "condor", "key": "x", "value": "   "},
])
def test_bad_overrides_are_refused(row):
    with pytest.raises(ValueError):
        sc.validate_overrides([row])


def test_override_values_stay_on_one_line(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_SITE_REPO", str(tmp_path / "absent"))
    monkeypatch.setenv("STUDIO_SITE_REPO_IMAGE", str(tmp_path / "absent"))
    rc = tmp_path / ".pegasusrc"
    sc.write_config("unity.yml", {}, rc, overrides=[
        {"site": "compute", "namespace": "env", "key": "X", "value": "a\npegasus.evil = 1"}])
    assert all(not line.startswith("pegasus.evil") for line in rc.read_text().splitlines())


def test_effective_site_applies_overrides():
    import yaml

    text = yaml.safe_dump({"sites": [{"name": "compute", "profiles": {
        "condor": {"universe": "vanilla"}, "pegasus": {"style": "condor"}}}]})
    out = yaml.safe_load(sc.effective_site(text, "compute", [
        {"site": "compute", "namespace": "condor", "key": "requirements", "value": "TestPool =!= True"},
        {"site": "compute", "namespace": "pegasus", "key": "style", "value": "glite"},
        {"site": "local", "namespace": "env", "key": "X", "value": "1"},
    ]))
    assert out["profiles"]["condor"] == {"universe": "vanilla", "requirements": "TestPool =!= True"}
    assert out["profiles"]["pegasus"]["style"] == "glite"  # override wins
    # A site the catalog does not describe (Pegasus' own local) still shows.
    assert yaml.safe_load(sc.effective_site(text, "local", [
        {"site": "local", "namespace": "env", "key": "X", "value": "1"}]))["profiles"] == {"env": {"X": "1"}}


def test_endpoint_keeps_overrides_per_catalog_and_imports_hand_added(client, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("STUDIO_SITE_REPO", str(tmp_path / "absent"))
    monkeypatch.setenv("STUDIO_SITE_REPO_IMAGE", str(tmp_path / "absent"))
    (tmp_path / ".pegasusrc").write_text(
        "pegasus.catalog.site.repo.file = purdue-anvil.yml\n"
        "pegasus.catalog.site.sites.compute.profiles.pegasus.queue = wholenode\n")

    async def catalogs(ref):
        return [{"name": "purdue-anvil.yml", "label": "Anvil", "needs": []},
                {"name": "unity.yml", "label": "Unity", "needs": []}]

    async def accounts():
        return []

    async def version(cmd):
        return "6.0.0dev"

    async def text(name, ref):
        return "sites:\n- name: compute\n  profiles:\n    pegasus: {style: glite}\n"

    monkeypatch.setattr(sc, "list_catalogs", catalogs)
    monkeypatch.setattr(sc, "slurm_accounts", accounts)
    monkeypatch.setattr(sc, "catalog_text", text)
    monkeypatch.setattr("routers.health._run_version_cmd", version)

    # The hand-added override is adopted for Anvil and shown in the effective site.
    body = client.get("/api/settings/compute-site").json()
    assert body["saved_overrides"]["purdue-anvil.yml"][0]["key"] == "queue"
    assert "queue: wholenode" in body["effective_site"]

    # Switching to Unity (older client: no overrides sent) does not carry it over…
    body = client.put("/api/settings/compute-site", json={"catalog": "unity.yml", "env": {}}).json()
    assert body["overrides"] == []
    assert "wholenode" not in (tmp_path / ".pegasusrc").read_text()

    # …and switching back restores Anvil's set.
    body = client.put("/api/settings/compute-site", json={"catalog": "purdue-anvil.yml", "env": {}}).json()
    assert [o["value"] for o in body["overrides"]] == ["wholenode"]

    bad = client.put("/api/settings/compute-site", json={
        "catalog": "unity.yml", "env": {},
        "overrides": [{"site": "compute", "namespace": "shell", "key": "x", "value": "1"}]})
    assert bad.status_code == 400


def test_switching_cluster_stashes_settings_hand_added_after_last_get(client, monkeypatch, tmp_path):
    """A slot already in the DB must not shadow lines added to the file later:
    switching away and back keeps them (Codex review of #479)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("STUDIO_SITE_REPO", str(tmp_path / "absent"))
    monkeypatch.setenv("STUDIO_SITE_REPO_IMAGE", str(tmp_path / "absent"))
    rc = tmp_path / ".pegasusrc"
    rc.write_text("pegasus.catalog.site.repo.file = purdue-anvil.yml\n")

    async def catalogs(ref):
        return [{"name": "purdue-anvil.yml", "label": "Anvil", "needs": []},
                {"name": "unity.yml", "label": "Unity", "needs": []}]

    async def none(*a):
        return []

    async def version(cmd):
        return "6.0.0dev"

    async def text(name, ref):
        return None

    monkeypatch.setattr(sc, "list_catalogs", catalogs)
    monkeypatch.setattr(sc, "slurm_accounts", none)
    monkeypatch.setattr(sc, "catalog_text", text)
    monkeypatch.setattr("routers.health._run_version_cmd", version)

    # Anvil gets an (empty) slot in the DB.
    client.put("/api/settings/compute-site", json={"catalog": "purdue-anvil.yml", "env": {}, "overrides": []})
    # The user then hand-adds a line, and switches cluster without reloading.
    rc.write_text(rc.read_text() + "pegasus.catalog.site.sites.compute.profiles.pegasus.queue = wholenode\n")
    client.put("/api/settings/compute-site", json={"catalog": "unity.yml", "env": {}, "overrides": []})
    assert "wholenode" not in rc.read_text()

    body = client.put("/api/settings/compute-site", json={"catalog": "purdue-anvil.yml", "env": {}}).json()
    assert [o["value"] for o in body["overrides"]] == ["wholenode"]
