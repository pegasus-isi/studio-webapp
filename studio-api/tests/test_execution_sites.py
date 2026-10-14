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


def _studio_repo(tmp_path, monkeypatch, profiles):
    """Studio's own catalog as the image installs it, under a series dir."""
    root = tmp_path / "site-catalogs"
    conf = root / "5.1" / "conf"
    conf.mkdir(parents=True)
    _catalog(conf / "pegasus-studio.yml", [{"name": "compute", "profiles": profiles}])
    monkeypatch.setenv("STUDIO_SITE_REPO", str(root))
    monkeypatch.setenv("STUDIO_SITE_REPO_IMAGE", str(tmp_path / "no-image"))
    monkeypatch.delenv("STUDIO_SITE_CATALOG_FILE", raising=False)
    return root


def test_studio_pool_is_labelled_and_a_condor_pool_before_first_plan(
    tmp_path, monkeypatch
):
    # #517: the hosted "compute" used to be "HPC cluster" until the planner
    # left a copy of the catalog in the project, then became a pool. The
    # catalog itself is readable without a plan, so the answer is the same
    # on the first visit as after the first run.
    _studio_repo(tmp_path, monkeypatch, CONDOR)
    rc = _rc(tmp_path, "pegasus-studio.yml")
    result = es.execution_sites(str(tmp_path), environ={}, pegasusrc=rc)
    compute = next(s for s in result["sites"] if s["name"] == "compute")
    assert compute["style"] == "condor"
    assert compute["label"] == "Studio’s own pool"
    assert compute["source"] == "hosted"
    assert result["default"] == "compute"

    # After a plan the local copy says the same.
    _catalog(tmp_path / "pegasus-studio.yml", [{"name": "compute", "profiles": CONDOR}])
    again = es.execution_sites(str(tmp_path), environ={}, pegasusrc=rc)
    assert [s for s in again["sites"] if s["name"] == "compute"] == [compute]


def test_published_catalog_is_labelled_by_cluster_and_styled_from_cache(
    tmp_path, monkeypatch
):
    from services import site_config as sc

    monkeypatch.setattr(sc, "_cache", {})
    rc = _rc(tmp_path, "unity.yml")
    cold = es.execution_sites(str(tmp_path), environ={}, pegasusrc=rc)
    compute = next(s for s in cold["sites"] if s["name"] == "compute")
    # Nothing fetched yet: the cluster is still named, its scheduler unknown.
    assert compute == {
        "name": "compute", "style": None, "source": "hosted",
        "label": "Unity (UMass)", "advanced": False, "configured": True,
        "missing": [],
    }

    import time

    import yaml

    text = yaml.safe_dump(
        {"pegasus": "5.0", "sites": [{"name": "compute", "profiles": SLURM}]}
    )
    sc._cache["text:5.1:unity.yml"] = (time.monotonic(), text)
    warm = es.execution_sites(str(tmp_path), environ={}, pegasusrc=rc)
    compute = next(s for s in warm["sites"] if s["name"] == "compute")
    assert compute["style"] == "slurm"
    assert compute["label"] == "Unity (UMass)"


def test_overlay_on_hosted_catalog_keeps_the_cluster_label(tmp_path, monkeypatch):
    _studio_repo(tmp_path, monkeypatch, CONDOR)
    _catalog(
        tmp_path / "sites.yml",
        [{"name": "compute", "profiles": {"pegasus": {"project": "lab"}}}],
    )
    result = es.execution_sites(
        str(tmp_path), environ={}, pegasusrc=_rc(tmp_path, "pegasus-studio.yml")
    )
    compute = next(s for s in result["sites"] if s["name"] == "compute")
    assert compute["source"] == "project"
    assert compute["label"] == "Studio’s own pool"
    assert compute["style"] == "condor"


def _advanced(result):
    return {s["name"]: s["advanced"] for s in result["sites"]}


def test_generator_condorpool_is_demoted_once_a_cluster_is_chosen(
    tmp_path, monkeypatch
):
    # #518: the quickstart was generated before the user chose a cluster, so
    # its sites.yml carries condorpool; after choosing one, the form showed
    # both as equals. The chosen cluster is the choice; the leftover pool is
    # kept behind "more places".
    _studio_repo(tmp_path, monkeypatch, CONDOR)
    _catalog(
        tmp_path / "sites.yml",
        [{"name": "local"}, {"name": "condorpool", "profiles": CONDOR}],
    )
    result = es.execution_sites(
        str(tmp_path), environ={}, pegasusrc=_rc(tmp_path, "pegasus-studio.yml")
    )
    assert _names(result) == ["local", "condorpool", "compute"]
    assert _advanced(result) == {"local": True, "condorpool": True, "compute": False}
    assert result["default"] == "compute"


def test_generator_condorpool_is_demoted_under_a_deployment_site(tmp_path):
    _catalog(tmp_path / "sites.yml", [{"name": "condorpool", "profiles": CONDOR}])
    env = {"STUDIO_EXEC_SITE": "hpc", "STUDIO_SITE_STYLE": "slurm"}
    result = es.execution_sites(str(tmp_path), environ=env, pegasusrc=_rc(tmp_path))
    assert _advanced(result)["condorpool"] is True
    assert result["default"] == "hpc"


def test_condorpool_stays_primary_when_nothing_else_is_chosen(tmp_path):
    _catalog(tmp_path / "sites.yml", [{"name": "condorpool", "profiles": CONDOR}])
    result = es.execution_sites(str(tmp_path), environ={}, pegasusrc=_rc(tmp_path))
    assert _advanced(result) == {"local": True, "condorpool": False}
    # And the fallback the portable generators add is never demoted either.
    bare = es.execution_sites(str(tmp_path / "x"), environ={}, pegasusrc=_rc(tmp_path))
    assert _advanced(bare)["condorpool"] is False


def test_a_project_condor_site_under_another_name_is_not_demoted(tmp_path, monkeypatch):
    # Only the generator's default pool is a leftover; a pool the project
    # deliberately defines is a real choice.
    _studio_repo(tmp_path, monkeypatch, CONDOR)
    _catalog(tmp_path / "sites.yml", [{"name": "gpu-pool", "profiles": CONDOR}])
    result = es.execution_sites(
        str(tmp_path), environ={}, pegasusrc=_rc(tmp_path, "pegasus-studio.yml")
    )
    assert _advanced(result)["gpu-pool"] is False


def test_sites_without_a_hosted_catalog_carry_no_label(tmp_path):
    result = es.execution_sites(
        str(tmp_path), environ={"STUDIO_EXEC_SITE": "hpc"}, pegasusrc=_rc(tmp_path)
    )
    assert all(s["label"] is None for s in result["sites"])


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
    assert hpc == {
        "name": "hpc", "style": "slurm", "source": "deployment", "label": None,
        "advanced": False, "configured": True, "missing": [],
    }
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


# ── "This machine", and whether a cluster is ready (#516) ──


def test_local_is_the_main_choice_only_when_nothing_else_is(tmp_path):
    # A pool exists, so the submit host is a "more places" option.
    result = es.execution_sites(str(tmp_path), environ={}, pegasusrc=_rc(tmp_path))
    local = result["sites"][0]
    assert local["name"] == "local" and local["advanced"] is True


def test_local_is_not_offered_where_the_deployment_is_a_batch_cluster(tmp_path):
    # Open OnDemand: "this machine" is the login node.
    env = {"STUDIO_EXEC_SITE": "hpc", "STUDIO_SITE_STYLE": "slurm"}
    result = es.execution_sites(str(tmp_path), environ=env, pegasusrc=_rc(tmp_path))
    assert _names(result) == ["hpc"]
    assert result["default"] == "hpc"
    # A deployment pool is not a batch cluster: the pod is still usable.
    env = {"STUDIO_EXEC_SITE": "pool", "STUDIO_SITE_STYLE": "condor"}
    result = es.execution_sites(str(tmp_path), environ=env, pegasusrc=_rc(tmp_path))
    assert _names(result) == ["local", "pool"]


def test_local_is_not_offered_with_an_administrators_slurm_catalog(
    tmp_path, monkeypatch
):
    _studio_repo(tmp_path, monkeypatch, SLURM)
    admin = tmp_path / "cluster.yml"
    admin.write_text("# the admin's catalog, already installed in the repo\n")
    monkeypatch.setenv("STUDIO_SITE_CATALOG_FILE", str(admin))
    rc = _rc(tmp_path, "pegasus-studio.yml")
    result = es.execution_sites(str(tmp_path), environ={}, pegasusrc=rc)
    assert _names(result) == ["compute"]


def test_local_stays_for_a_cluster_the_user_chose(tmp_path, monkeypatch):
    # Unity chosen on the K8s deployment: the pod is still "this machine",
    # fine for a small test, so it stays — behind "more places".
    from services import site_config as sc

    import time

    import yaml

    monkeypatch.setattr(sc, "_cache", {})
    sc._cache["text:5.1:unity.yml"] = (
        time.monotonic(),
        yaml.safe_dump({"pegasus": "5.0", "sites": [{"name": "compute", "profiles": SLURM}]}),
    )
    result = es.execution_sites(
        str(tmp_path), environ={}, pegasusrc=_rc(tmp_path, "unity.yml")
    )
    assert _names(result) == ["local", "compute"]
    assert _advanced(result) == {"local": True, "compute": False}


def _cached_catalog(monkeypatch, name, text):
    from services import site_config as sc

    import time

    monkeypatch.setattr(sc, "_cache", {f"text:5.1:{name}": (time.monotonic(), text)})


PERLMUTTER = """
pegasus: "5.0"
sites:
- name: compute
  profiles:
    pegasus: {style: glite, project: "${RESOURCE_PROJECT}"}
    condor: {grid_resource: batch slurm}
    env: {SCRATCH: "${RESOURCE_SCRATCH_DIR}"}
"""


def test_a_cluster_whose_allocation_is_not_set_is_offered_but_not_ready(
    tmp_path, monkeypatch
):
    _cached_catalog(monkeypatch, "nersc-perlmutter.yml", PERLMUTTER)
    rc = _rc(tmp_path, "nersc-perlmutter.yml")
    result = es.execution_sites(str(tmp_path), environ={}, pegasusrc=rc)
    compute = next(s for s in result["sites"] if s["name"] == "compute")
    assert compute["configured"] is False
    assert compute["missing"] == [
        "Project / allocation to charge", "Scratch directory on the cluster",
    ]
    # Still the default: the form shows it, disabled, with what it needs.
    assert result["default"] == "compute"

    problem = es.site_problem(str(tmp_path), "compute", environ={}, pegasusrc=rc)
    assert problem is not None
    assert "NERSC Perlmutter still needs Project / allocation to charge" in problem
    assert "Resources page" in problem


def test_a_cluster_with_its_allocation_set_is_ready(tmp_path, monkeypatch):
    _cached_catalog(monkeypatch, "nersc-perlmutter.yml", PERLMUTTER)
    rc = tmp_path / "pegasusrc"
    rc.write_text(
        "pegasus.catalog.site.repo.file = nersc-perlmutter.yml\n"
        "env.RESOURCE_PROJECT = m1234\n"
        "env.RESOURCE_SCRATCH_DIR = /pscratch/sd/k/kt\n"
    )
    result = es.execution_sites(str(tmp_path), environ={}, pegasusrc=rc)
    compute = next(s for s in result["sites"] if s["name"] == "compute")
    assert compute["configured"] is True and compute["missing"] == []
    assert es.site_problem(str(tmp_path), "compute", environ={}, pegasusrc=rc) is None


def test_a_catalog_not_fetched_yet_is_taken_as_ready(tmp_path, monkeypatch):
    # Nothing is known about what it needs; refusing on that would block a
    # cluster for a GitHub hiccup.
    from services import site_config as sc

    monkeypatch.setattr(sc, "_cache", {})
    result = es.execution_sites(
        str(tmp_path), environ={}, pegasusrc=_rc(tmp_path, "nersc-perlmutter.yml")
    )
    compute = next(s for s in result["sites"] if s["name"] == "compute")
    assert compute["configured"] is True
