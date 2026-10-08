"""Tests for run outputs discovery and listing (#241, Phase 4).

Studio had no concept of a run's results before this, so the risks are
discovery being silently wrong (pointing at the wrong directory, or reporting
"no results" when there are some) and the path parameter being a way out of the
output directory.
"""

from __future__ import annotations

import os

import pytest

from services import run_outputs as ro


def _workspace(tmp_path):
    """A workflows root with one project and one run dir inside it."""
    root = tmp_path / "workflows"
    project = root / "my-analysis"
    run_dir = project / "run0001"
    run_dir.mkdir(parents=True)
    return str(root), str(project), str(run_dir)


def _site_catalog(path, storage_dir, site="local"):
    import yaml

    with open(path, "w") as fh:
        yaml.safe_dump(
            {
                "pegasus": "5.0",
                "sites": [
                    {
                        "name": site,
                        "directories": [
                            {"type": "sharedScratch", "path": "/tmp/scratch"},
                            {"type": "localStorage", "path": str(storage_dir)},
                        ],
                    }
                ],
            },
            fh,
        )


# ── project association ───────────────────────────────────────────────────


def test_project_dir_is_the_first_component_under_the_root(tmp_path):
    root, project, run_dir = _workspace(tmp_path)
    assert ro.project_dir_for_run(run_dir, root) == project


def test_run_outside_the_workflows_root_has_no_project(tmp_path):
    root, _, _ = _workspace(tmp_path)
    stray = tmp_path / "elsewhere" / "run0001"
    stray.mkdir(parents=True)
    assert ro.project_dir_for_run(str(stray), root) is None


# ── output directory discovery ────────────────────────────────────────────


def test_site_catalog_is_authoritative(tmp_path):
    root, project, run_dir = _workspace(tmp_path)
    storage = tmp_path / "declared-elsewhere"
    storage.mkdir()
    # A conventional dir also exists — the catalog must still win, because it
    # is what Pegasus actually staged to.
    os.makedirs(os.path.join(project, "output"))
    _site_catalog(os.path.join(project, "sites.yml"), storage)

    found, via = ro.resolve_output_dir(run_dir, root)
    assert found == os.path.realpath(str(storage))
    assert via == "catalog"


def test_submit_dir_catalog_wins_over_the_project_one(tmp_path):
    # pegasus-plan copies the catalog into the submit dir. If the project has
    # since been regenerated, the run's own copy is the one that describes
    # where *this* run's files went.
    root, project, run_dir = _workspace(tmp_path)
    old = tmp_path / "run-storage"
    new = tmp_path / "regenerated-storage"
    old.mkdir()
    new.mkdir()
    _site_catalog(os.path.join(run_dir, "sites.yml"), old)
    _site_catalog(os.path.join(project, "sites.yml"), new)

    found, via = ro.resolve_output_dir(run_dir, root)
    assert found == os.path.realpath(str(old))
    assert via == "catalog"


def test_falls_back_to_the_conventional_output_dir(tmp_path):
    root, project, run_dir = _workspace(tmp_path)
    (tmp_path / "workflows" / "my-analysis" / "output").mkdir()

    found, via = ro.resolve_output_dir(run_dir, root)
    assert found == os.path.realpath(os.path.join(project, "output"))
    assert via == "convention"


def test_reports_none_when_nothing_is_found(tmp_path):
    root, _, run_dir = _workspace(tmp_path)
    found, via = ro.resolve_output_dir(run_dir, root)
    assert found is None
    # "none" vs "convention" is what lets the UI distinguish "the workflow says
    # results go here and there are none yet" from "we guessed and found none".
    assert via == "none"


def test_unreadable_catalog_degrades_to_convention(tmp_path):
    root, project, run_dir = _workspace(tmp_path)
    with open(os.path.join(project, "sites.yml"), "w") as fh:
        fh.write("{{{ not yaml")
    (tmp_path / "workflows" / "my-analysis" / "output").mkdir()

    found, via = ro.resolve_output_dir(run_dir, root)
    assert via == "convention"
    assert found is not None


# ── listing ───────────────────────────────────────────────────────────────


def test_listing_returns_files_newest_first(tmp_path):
    root, project, run_dir = _workspace(tmp_path)
    output = tmp_path / "workflows" / "my-analysis" / "output"
    output.mkdir()
    for i, name in enumerate(["old.txt", "middle.csv", "newest.png"]):
        f = output / name
        f.write_text("x")
        os.utime(f, (1_700_000_000 + i * 100,) * 2)

    result = ro.listing(run_dir, root)
    assert result["exists"] is True
    assert [f["name"] for f in result["files"]] == [
        "newest.png", "middle.csv", "old.txt"
    ]
    assert result["total"] == 3


def test_listing_classifies_and_flags_previewability(tmp_path):
    root, _, run_dir = _workspace(tmp_path)
    output = tmp_path / "workflows" / "my-analysis" / "output"
    output.mkdir()
    (output / "plot.png").write_bytes(b"\x89PNG")
    (output / "table.csv").write_text("a,b\n1,2\n")
    (output / "model.bin").write_bytes(b"\x00" * 10)
    huge = output / "huge.txt"
    huge.write_text("x" * (ro.PREVIEW_MAX_BYTES + 1))

    by_name = {f["name"]: f for f in ro.listing(run_dir, root)["files"]}
    assert by_name["plot.png"]["kind"] == "image"
    assert by_name["table.csv"]["kind"] == "table"
    assert by_name["model.bin"]["kind"] == "binary"
    assert by_name["model.bin"]["previewable"] is False
    # Right kind, too big — a gigabyte of text must not be offered as a preview.
    assert by_name["huge.txt"]["kind"] == "text"
    assert by_name["huge.txt"]["previewable"] is False


def test_listing_paginates(tmp_path):
    root, _, run_dir = _workspace(tmp_path)
    output = tmp_path / "workflows" / "my-analysis" / "output"
    output.mkdir()
    for i in range(10):
        (output / f"f{i:02d}.txt").write_text("x")

    page = ro.listing(run_dir, root, offset=4, limit=3)
    assert page["total"] == 10
    assert len(page["files"]) == 3
    assert page["offset"] == 4


def test_listing_finds_files_in_subdirectories(tmp_path):
    root, _, run_dir = _workspace(tmp_path)
    output = tmp_path / "workflows" / "my-analysis" / "output"
    (output / "figures").mkdir(parents=True)
    (output / "figures" / "fig1.png").write_bytes(b"\x89PNG")

    files = ro.listing(run_dir, root)["files"]
    assert [f["path"] for f in files] == [os.path.join("figures", "fig1.png")]


def test_listing_skips_pegasus_bookkeeping(tmp_path):
    root, _, run_dir = _workspace(tmp_path)
    output = tmp_path / "workflows" / "my-analysis" / "output"
    output.mkdir()
    (output / "result.csv").write_text("a\n")
    (output / "braindump.yml").write_text("x: 1\n")
    (output / ".hidden").write_text("x")

    names = [f["name"] for f in ro.listing(run_dir, root)["files"]]
    assert names == ["result.csv"]


def test_missing_output_dir_is_reported_not_raised(tmp_path):
    root, _, run_dir = _workspace(tmp_path)
    result = ro.listing(run_dir, root)
    assert result["exists"] is False
    assert result["files"] == []
    assert result["total"] == 0


# ── path safety ───────────────────────────────────────────────────────────


def test_safe_output_path_allows_a_file_inside(tmp_path):
    output = tmp_path / "out"
    (output / "sub").mkdir(parents=True)
    target = output / "sub" / "a.txt"
    target.write_text("x")
    assert ro.safe_output_path(str(output), "sub/a.txt") == os.path.realpath(
        str(target)
    )


@pytest.mark.parametrize(
    "attempt",
    ["../secret.txt", "../../etc/passwd", "sub/../../secret.txt", "/etc/passwd"],
)
def test_safe_output_path_rejects_escapes(tmp_path, attempt):
    output = tmp_path / "out"
    (output / "sub").mkdir(parents=True)
    (tmp_path / "secret.txt").write_text("secret")

    with pytest.raises(ValueError):
        ro.safe_output_path(str(output), attempt)


def test_safe_output_path_rejects_a_symlink_out(tmp_path):
    # String checks for ".." miss this one entirely, which is why the
    # implementation resolves first and compares after.
    output = tmp_path / "out"
    output.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    os.symlink(secret, output / "link.txt")

    with pytest.raises(ValueError):
        ro.safe_output_path(str(output), "link.txt")


# ── endpoints ─────────────────────────────────────────────────────────────


def test_outputs_endpoint_404s_for_an_unknown_run(client):
    assert client.get("/api/workflows/no-such-run/outputs").status_code == 404


def test_output_file_endpoint_404s_for_an_unknown_run(client):
    res = client.get("/api/workflows/no-such-run/outputs/file?path=a.txt")
    assert res.status_code == 404


def test_hidden_directories_are_not_listed(tmp_path):
    """Opening a result in JupyterLab writes a checkpoint copy under
    `.ipynb_checkpoints/`; it showed up as a second result of the run
    (earthquake-workflow: california_catalog-checkpoint.csv)."""
    (tmp_path / "california_catalog.csv").write_text("a,b\n")
    ckpt = tmp_path / ".ipynb_checkpoints"
    ckpt.mkdir()
    (ckpt / "california_catalog-checkpoint.csv").write_text("a,b\n")
    nested = tmp_path / "plots" / ".hidden"
    nested.mkdir(parents=True)
    (nested / "x.png").write_bytes(b"")
    (tmp_path / "plots" / "map.png").write_bytes(b"")

    files, _ = ro.collect(str(tmp_path))
    assert sorted(f["path"] for f in files) == [
        "california_catalog.csv",
        os.path.join("plots", "map.png"),
    ]
