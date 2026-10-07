"""Tests for the Run form's execution-site choices.

The form used to offer a fixed local/condorpool pair, which plans against a
site nothing defines on a Slurm or hosted-catalog install. The risks are
offering a site planning cannot resolve, preselecting the wrong one, and a
deployment default silently overriding what the user's generator accepts.
"""

from __future__ import annotations

import yaml

from services import execution_sites as es


def _catalog(path, sites):
    with open(path, "w") as fh:
        yaml.safe_dump({"pegasus": "5.0", "sites": sites}, fh)


CONDOR = {"pegasus": {"style": "condor"}, "condor": {"universe": "vanilla"}}
SLURM = {"pegasus": {"style": "glite"}, "condor": {"grid_resource": "batch slurm"}}


def _rc(tmp_path, hosted=None):
    rc = tmp_path / "pegasusrc"
    rc.write_text(
        f"pegasus.catalog.site.repo.file = {hosted}\n" if hosted else "# none\n"
    )
    return rc


def _names(result):
    return [s["name"] for s in result["sites"]]


def test_plain_workspace_offers_local_and_default_condor_site(tmp_path):
    result = es.execution_sites(
        str(tmp_path), environ={}, pegasusrc=_rc(tmp_path)
    )
    assert _names(result) == ["local", "condorpool"]
    assert result["default"] == "condorpool"


def test_project_sites_are_offered_with_their_style(tmp_path):
    _catalog(
        tmp_path / "sites.yml",
        [
            {"name": "local"},
            {"name": "condorpool", "profiles": CONDOR},
            {"name": "compute", "profiles": SLURM},
        ],
    )
    result = es.execution_sites(
        str(tmp_path), environ={}, pegasusrc=_rc(tmp_path)
    )
    by_name = {s["name"]: s for s in result["sites"]}
    assert _names(result)[0] == "local"
    assert by_name["compute"]["style"] == "slurm"
    assert by_name["compute"]["source"] == "project"
    assert by_name["condorpool"]["style"] == "condor"
    assert result["default"] == "condorpool"


def test_slurm_only_project_defaults_to_its_own_site(tmp_path):
    # No hosted catalog, no deployment setting: the project's sites.yml is
    # the only source, so condorpool must be neither offered nor preselected.
    _catalog(
        tmp_path / "sites.yml",
        [{"name": "local"}, {"name": "compute", "profiles": SLURM}],
    )
    result = es.execution_sites(
        str(tmp_path), environ={}, pegasusrc=_rc(tmp_path)
    )
    assert _names(result) == ["local", "compute"]
    assert result["default"] == "compute"


def test_hosted_catalog_before_first_plan_offers_compute_not_condorpool(tmp_path):
    # No local copy of the hosted file yet: hosted catalogs call their
    # site "compute", and condorpool would plan against nothing.
    result = es.execution_sites(
        str(tmp_path), environ={}, pegasusrc=_rc(tmp_path, "unity.yml")
    )
    assert _names(result) == ["local", "compute"]
    assert result["default"] == "compute"


def test_overlay_without_style_takes_it_from_hosted_copy(tmp_path):
    _catalog(tmp_path / "unity.yml", [{"name": "compute", "profiles": SLURM}])
    _catalog(
        tmp_path / "sites.yml",
        [{"name": "compute", "profiles": {"pegasus": {"project": "lab"}}}],
    )
    result = es.execution_sites(
        str(tmp_path), environ={}, pegasusrc=_rc(tmp_path, "unity.yml")
    )
    compute = next(s for s in result["sites"] if s["name"] == "compute")
    assert compute["style"] == "slurm"
    assert compute["source"] == "project"
    assert "condorpool" not in _names(result)


def test_deployment_site_is_offered_and_preselected(tmp_path):
    env = {"STUDIO_EXEC_SITE": "hpc", "STUDIO_SITE_STYLE": "slurm"}
    result = es.execution_sites(str(tmp_path), environ=env, pegasusrc=_rc(tmp_path))
    hpc = next(s for s in result["sites"] if s["name"] == "hpc")
    assert hpc == {"name": "hpc", "style": "slurm", "source": "deployment"}
    assert result["default"] == "hpc"


def test_unreadable_catalog_offers_defaults(tmp_path):
    (tmp_path / "sites.yml").write_text(": not yaml : [")
    result = es.execution_sites(
        str(tmp_path), environ={}, pegasusrc=_rc(tmp_path)
    )
    assert _names(result) == ["local", "condorpool"]


def test_missing_pegasusrc_is_not_a_hosted_catalog(tmp_path):
    result = es.execution_sites(
        str(tmp_path), environ={}, pegasusrc=tmp_path / "absent"
    )
    assert result["default"] == "condorpool"


def test_generator_defaults_fill_only_existing_options():
    params = [
        {"dest": "site_style", "default": "auto",
         "choices": ["auto", "condor", "slurm", "none"]},
        {"dest": "queue", "default": None, "choices": None},
        {"dest": "num_samples", "default": 3, "choices": None},
    ]
    env = {"STUDIO_SITE_STYLE": "slurm", "STUDIO_SITE_QUEUE": "cpu",
           "STUDIO_SITE_PROJECT": "lab"}
    out = es.apply_generator_site_defaults(params, environ=env)
    assert [p["default"] for p in out] == ["slurm", "cpu", 3]
    # No --project option in this generator: nothing is invented.
    assert all(p["dest"] != "project" for p in out)
    # The input list is not mutated.
    assert params[0]["default"] == "auto"


def test_generator_default_outside_choices_is_ignored():
    params = [{"dest": "site_style", "default": "auto",
               "choices": ["auto", "condor", "none"]}]
    out = es.apply_generator_site_defaults(
        params, environ={"STUDIO_SITE_STYLE": "slurm"}
    )
    assert out[0]["default"] == "auto"
