"""Keeping the execution site in sync with gromacs-template generators (#466)."""

from __future__ import annotations

import os

import yaml

import services.run_flow as rf
from services import execution_sites as xs
from services import job_files, run_outputs

# What introspect_argparse reports for the template's -e option.
_E = {"dest": "execution_site_name", "flag": "--execution-site-name",
      "flags": ["-e", "--execution-site-name"]}
_OTHER = {"dest": "regions", "flag": "--regions", "flags": ["--regions"]}


def test_the_chosen_site_replaces_any_generator_site_value():
    params = [_E, _OTHER]
    assert xs.sync_site_arg(["--regions", "ca"], params, "condorpool") == [
        "--regions", "ca", "--execution-site-name", "condorpool"]
    # A hand-set value, in either spelling, is replaced, never duplicated.
    assert xs.sync_site_arg(["-e", "compute", "--regions", "ca"], params, "condorpool") == [
        "--regions", "ca", "--execution-site-name", "condorpool"]
    assert xs.sync_site_arg(["--execution-site-name=compute"], params, "x") == [
        "--execution-site-name", "x"]
    # A generator without the option is left alone.
    assert xs.sync_site_arg(["--regions", "ca"], [_OTHER], "condorpool") == ["--regions", "ca"]


def test_the_site_option_is_not_offered_as_a_form_field():
    assert xs.hide_managed_params([_E, _OTHER]) == [_OTHER]


def test_output_dir_only_when_no_catalog_says_where_results_go(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    assert xs.plan_output_dir(str(proj)) == str(proj / "output")
    (proj / "sites.yml").write_text(yaml.safe_dump({"sites": [{
        "name": "local",
        "directories": [{"type": "localStorage", "path": str(tmp_path / "elsewhere")}],
    }]}))
    assert xs.plan_output_dir(str(proj)) is None


def test_template_project_defaults_to_the_builtin_pool_and_flags_undefined_sites(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    rc = tmp_path / "pegasusrc"  # empty: no hosted catalog
    rc.write_text("")
    sites = xs.execution_sites(str(proj), environ={}, pegasusrc=rc)
    assert sites["default"] == "condorpool"
    assert xs.site_problem(str(proj), "condorpool", environ={}, pegasusrc=rc) is None
    problem = xs.site_problem(str(proj), "compute", environ={}, pegasusrc=rc)
    assert problem and "compute" in problem


def test_a_hosted_catalog_in_project_properties_defines_compute(tmp_path):
    """The template's -s writes the hosted catalog into the project's
    pegasus.properties; that has to count, not only ~/.pegasusrc."""
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "pegasus.properties").write_text(
        "pegasus.catalog.site.repo.file = unity.yml\n")
    rc = tmp_path / "pegasusrc"
    rc.write_text("")
    sites = xs.execution_sites(str(proj), environ={}, pegasusrc=rc)
    assert sites["default"] == "compute"
    assert xs.site_problem(str(proj), "compute", environ={}, pegasusrc=rc) is None


def _template_run(tmp_path, output_dir_arg=True):
    """A run planned the template's way: no sites.yml, the planner's merged
    catalog in catalogs/, built-in local scratch under wf-scratch/LOCAL."""
    proj = tmp_path / "workflows" / "eq"
    run = proj / "submit" / "u" / "pegasus" / "eq" / "run0001"
    (run / "catalogs").mkdir(parents=True)
    scratch_root = proj / "wf-scratch" / "LOCAL"
    scratch = scratch_root / "u" / "pegasus" / "eq" / "run0001"
    scratch.mkdir(parents=True)
    out = proj / "output"
    out.mkdir()
    (run / "catalogs" / "sites-combined.yml").write_text(yaml.safe_dump({"sites": [
        {"name": "condorpool", "directories": []},
        {"name": "local", "directories": [
            {"type": "sharedStorage", "path": str(proj / "wf-output")},
            {"type": "sharedScratch", "path": str(scratch_root)},
        ]},
    ]}))
    args = f"--dir submit -s condorpool -o local {'--output-dir ' + str(out) + ' ' if output_dir_arg else ''}workflow.yml"
    (run / "braindump.yml").write_text(
        f'basedir: {proj / "submit"}\nsubmit_dir: {run}\nplanner_arguments: "\\"{args} \\""\n')
    return proj, run, scratch, out


def test_lookups_follow_what_the_planner_used(tmp_path):
    proj, run, scratch, out = _template_run(tmp_path)
    roots = [str(tmp_path / "workflows")]
    # The run's --output-dir beats the merged catalog's wf-output.
    assert run_outputs.planned_output_dir(str(run)) == str(out)
    assert run_outputs.resolve_output_dir(str(run), roots) == (os.path.realpath(out), "catalog")
    # Scratch is only recorded in catalogs/sites-combined.yml.
    assert job_files.scratch_run_dir(str(run), str(proj)) == str(scratch)


def test_without_output_dir_the_merged_catalog_names_storage(tmp_path):
    proj, run, scratch, out = _template_run(tmp_path, output_dir_arg=False)
    found = run_outputs.resolve_output_dir(str(run), [str(tmp_path / "workflows")])
    assert found == (os.path.realpath(proj / "wf-output"), "catalog")


# ── the one-button Run, end to end ──

_TEMPLATE_GENERATOR = """
import argparse
p = argparse.ArgumentParser()
p.add_argument('-e', '--execution-site-name', default='compute')
a = p.parse_args()
open('site.txt', 'w').write(a.execution_site_name)
open('workflow.yml', 'w').write('pegasus: "5.0"\\njobs: []\\n')
"""


def _project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "workflow_generator.py").write_text(_TEMPLATE_GENERATOR)
    return str(proj)


def test_run_binds_the_generator_to_the_planned_site(tmp_path, monkeypatch):
    from tests.test_run_flow import _drive

    seen: list[list[str]] = []

    async def fake_plan_capture(flow, argv, *, project_dir, mode=None):
        seen.append(list(argv))
        return 1, "stubbed planner"

    monkeypatch.setattr(rf, "_plan_capture", fake_plan_capture)
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.pegasusrc
    proj = _project(tmp_path)
    _drive(proj, site="condorpool")

    # The generator got the site the plan uses, not its own default.
    assert open(os.path.join(proj, "site.txt")).read() == "condorpool"
    argv = seen[0]
    i = argv.index("--output-dir")
    assert argv[i + 1] == os.path.join(proj, "output")
    assert argv[-1].endswith("workflow.yml")


def test_run_stops_before_generating_for_an_undefined_site(tmp_path, monkeypatch):
    from tests.test_run_flow import _drive

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("STUDIO_EXEC_SITE", raising=False)
    proj = _project(tmp_path)
    flow = _drive(proj, site="compute")

    assert flow.state == "failed"
    assert flow.failure.stage == "plan" and "compute" in flow.failure.cause
    assert not os.path.exists(os.path.join(proj, "site.txt"))  # never generated
