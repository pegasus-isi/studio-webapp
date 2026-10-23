"""services/run_placement: a workflow's machines, in its pegasus.properties."""

from __future__ import annotations

import stat

import pytest

from services import run_placement as rp

KEY = "pegasus.catalog.site.sites.compute.profiles.condor.requirements"


def test_annex_is_written_without_a_username(tmp_path):
    (tmp_path / "pegasus.properties").write_text(
        "# mine\npegasus.data.configuration = condorio\n"
    )
    state = rp.set_project_placement(str(tmp_path), "compute", "annex")
    assert state == {"placement": "annex", "source": "workflow", "foreign": None}
    text = (tmp_path / "pegasus.properties").read_text()
    assert f"{KEY} = TARGET.AnnexName =?= MY.Owner" in text
    # Everything else kept, in order.
    assert text.startswith("# mine\npegasus.data.configuration = condorio\n")


def test_elsewhere_removes_only_our_line(tmp_path):
    f = tmp_path / "pegasus.properties"
    f.write_text("a = 1\n")
    rp.set_project_placement(str(tmp_path), "compute", "annex")
    state = rp.set_project_placement(str(tmp_path), "compute", "elsewhere")
    assert state == {"placement": "elsewhere", "source": "default", "foreign": None}
    assert f.read_text() == "a = 1\n"


def test_elsewhere_creates_no_file(tmp_path):
    rp.set_project_placement(str(tmp_path), "compute", "elsewhere")
    assert not (tmp_path / "pegasus.properties").exists()


def test_no_line_means_everywhere_else(tmp_path):
    assert rp.project_placement(str(tmp_path), "compute") == {
        "placement": "elsewhere", "source": "default", "foreign": None,
    }


def test_a_workflows_own_requirements_are_left_alone(tmp_path):
    f = tmp_path / "pegasus.properties"
    f.write_text(f"{KEY} = (TARGET.Arch == \"X86_64\")\n")
    state = rp.project_placement(str(tmp_path), "compute")
    assert state["foreign"] == '(TARGET.Arch == "X86_64")' and state["placement"] is None
    with pytest.raises(ValueError):
        rp.set_project_placement(str(tmp_path), "compute", "annex")
    assert f.read_text() == f"{KEY} = (TARGET.Arch == \"X86_64\")\n"


def test_the_572_named_pin_reads_as_annex(tmp_path):
    (tmp_path / "pegasus.properties").write_text(f'{KEY} = TARGET.AnnexName =?= "alice"\n')
    assert rp.project_placement(str(tmp_path), "compute")["placement"] == "annex"


def test_per_site(tmp_path):
    rp.set_project_placement(str(tmp_path), "condorpool", "annex")
    assert rp.project_placement(str(tmp_path), "condorpool")["placement"] == "annex"
    assert rp.project_placement(str(tmp_path), "compute")["source"] == "default"


@pytest.mark.parametrize("old, new", [("any", "elsewhere"), ("pool", "elsewhere"), ("annex", "annex")])
def test_older_clients_values(old, new):
    assert rp.normalise(old) == new


@pytest.mark.parametrize("site", ["", "a b", "x;y", "a/b"])
def test_odd_site_names_are_refused(site):
    with pytest.raises(ValueError):
        rp.requirements_key(site)


def test_default_goes_into_pegasusrc_once_and_never_over_a_users_line(tmp_path):
    rc = tmp_path / ".pegasusrc"
    rc.write_text(
        "pegasus.catalog.site.repo.file = x.yml\n"
        'pegasus.catalog.site.sites.condorpool.profiles.condor.requirements = TARGET.AnnexName =?= "alice"\n'
    )
    assert rp.ensure_default(rc) == ["compute"]
    assert rp.ensure_default(rc) == []
    text = rc.read_text()
    assert f"{KEY} = TARGET.AnnexName =?= undefined" in text
    assert 'condorpool.profiles.condor.requirements = TARGET.AnnexName =?= "alice"' in text
    assert text.startswith("pegasus.catalog.site.repo.file = x.yml\n")
    assert stat.S_IMODE(rc.stat().st_mode) == 0o600


def test_project_file_outranks_the_default_for_the_planner(tmp_path):
    # The planner reads both; the project's line is the one that applies.
    # Mirrors shared_fs's merge order: ~/.pegasusrc, then the project file.
    from services.shared_fs import _merged_properties

    rc = tmp_path / ".pegasusrc"
    rp.ensure_default(rc)
    proj = tmp_path / "wf"
    proj.mkdir()
    rp.set_project_placement(str(proj), "compute", "annex")
    assert _merged_properties(str(proj), None, rc)[KEY] == "TARGET.AnnexName =?= MY.Owner"


@pytest.fixture
def one_project(tmp_path, monkeypatch):
    import routers.workflows as wf

    async def projects():
        return [{"project_id": "wf", "project_dir": str(tmp_path)}]

    monkeypatch.setattr(wf, "discover_projects_async", projects)
    return tmp_path


def test_endpoints_read_and_write_the_workflow(client, one_project):
    url = "/api/workflows/projects/wf/placement"
    assert client.get(url, params={"site": "compute"}).json()["placement"] == "elsewhere"
    r = client.put(url, json={"site": "compute", "placement": "annex"})
    assert r.status_code == 200 and r.json()["placement"] == "annex"
    assert "MY.Owner" in (one_project / "pegasus.properties").read_text()
    assert client.get(url, params={"site": "compute"}).json()["source"] == "workflow"


def test_endpoint_refuses_to_override_the_workflows_own_line(client, one_project):
    (one_project / "pegasus.properties").write_text(f"{KEY} = TARGET.Memory > 1000\n")
    r = client.put("/api/workflows/projects/wf/placement", json={"site": "compute", "placement": "annex"})
    assert r.status_code == 409
    assert client.put("/api/workflows/projects/wf/placement", json={"site": "a b", "placement": "annex"}).status_code == 400
