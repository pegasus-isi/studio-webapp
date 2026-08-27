"""User-configurable workflow discovery (#306).

Two settings extend — never replace — the deployment defaults: extra scan
roots merge into the recursive braindump scan, extra exclude fragments merge
into the builtin exclusion list. Covered here: the scanner's merge semantics
(dedupe, nested-root suppression, missing roots skipped), the settings
endpoints' validation, and the end-to-end path where a PUT changes what
GET /api/workflows finds.
"""

from __future__ import annotations

import os

from services.workflow_scanner import (
    _effective_scan_roots,
    discover_runs,
)


def _make_run(root, name="run0001", wf="demo") -> str:
    run_dir = os.path.join(root, "submit", name)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "braindump.yml"), "w") as f:
        f.write(f"wf_uuid: uuid-{wf}-{name}\npegasus_wf_name: {wf}\n")
    return run_dir


# --- scanner merge semantics ---------------------------------------------------


def test_extra_root_is_scanned(tmp_path):
    home = tmp_path / "home"
    volume = tmp_path / "volume"
    home.mkdir()
    _make_run(str(volume), wf="external")

    runs = discover_runs(str(home), extra_roots=[str(volume)])
    assert [r["name"] for r in runs] == ["external"]


def test_nested_extra_root_does_not_duplicate(tmp_path):
    """An extra root inside an already-scanned root would list every run
    twice via two globs — it is dropped, and run_dirs are deduped anyway."""
    home = tmp_path / "home"
    _make_run(str(home / "work"), wf="inner")

    runs = discover_runs(
        str(home), extra_roots=[str(home / "work"), str(home)]
    )
    assert len(runs) == 1


def test_missing_extra_root_is_skipped(tmp_path):
    """A root pointing at an unmounted volume means 'nothing from there right
    now', not a broken scan."""
    home = tmp_path / "home"
    _make_run(str(home), wf="local")

    runs = discover_runs(
        str(home), extra_roots=[str(tmp_path / "not-mounted")]
    )
    assert [r["name"] for r in runs] == ["local"]


def test_extra_exclude_fragment_hides_runs(tmp_path):
    home = tmp_path / "home"
    _make_run(str(home / "keep"), wf="keep")
    _make_run(str(home / "big-dataset"), wf="noise")

    runs = discover_runs(str(home), extra_excludes=["big-dataset"])
    assert [r["name"] for r in runs] == ["keep"]


def test_effective_roots_order_and_realpath(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    roots = _effective_scan_roots(str(a), [str(b), str(a), str(b / "..")])
    # Primary first, extras deduped (b/.. realpaths back to tmp_path which
    # contains a and b — but tmp_path was not already kept, so it is kept and
    # would shadow nothing scanned before it).
    assert roots[0] == os.path.realpath(str(a))
    assert os.path.realpath(str(b)) in roots


# --- settings endpoints --------------------------------------------------------


def test_workflow_scan_defaults_reported(client):
    data = client.get("/api/settings/workflow-scan").json()
    assert data["roots"] == []
    assert data["excludes"] == []
    assert data["defaults"]["scan_root"]
    assert data["defaults"]["workflows_dir"].endswith("workflows")
    assert "site-packages" in data["defaults"]["builtin_excludes"]


def test_workflow_scan_roundtrip_and_normalization(client, tmp_path):
    existing = tmp_path / "runs"
    existing.mkdir()
    res = client.put(
        "/api/settings/workflow-scan",
        json={
            "roots": [str(existing), f"  {existing}  ", "/no/such/volume"],
            "excludes": ["big-dataset", "", "  ", "big-dataset"],
        },
    )
    assert res.status_code == 200
    data = res.json()
    # Deduped, stripped, order preserved.
    assert data["roots"] == [str(existing), "/no/such/volume"]
    assert data["excludes"] == ["big-dataset"]
    # The nonexistent root is accepted but flagged, not rejected — a volume
    # may mount later.
    assert data["missing_roots"] == ["/no/such/volume"]

    # Persisted: a fresh GET sees the same config.
    again = client.get("/api/settings/workflow-scan").json()
    assert again["roots"] == data["roots"]
    assert again["excludes"] == data["excludes"]


def test_workflow_scan_rejects_relative_roots(client):
    res = client.put(
        "/api/settings/workflow-scan",
        json={"roots": ["relative/path"], "excludes": []},
    )
    assert res.status_code == 400
    assert "absolute" in res.json()["detail"]


def test_scan_uses_saved_extra_root_end_to_end(client, tmp_path):
    """The whole point: a run outside $HOME appears in GET /api/workflows once
    its volume is added as a scan root, and disappears when an exclude
    fragment matches it."""
    volume = tmp_path / "external-volume"
    _make_run(str(volume), wf="external")

    # Not found while the volume isn't a scan root ($HOME is a fresh tempdir).
    names = [w["name"] for w in client.get("/api/workflows").json()["workflows"]]
    assert "external" not in names

    client.put(
        "/api/settings/workflow-scan",
        json={"roots": [str(volume)], "excludes": []},
    )
    names = [w["name"] for w in client.get("/api/workflows").json()["workflows"]]
    assert "external" in names

    client.put(
        "/api/settings/workflow-scan",
        json={"roots": [str(volume)], "excludes": ["external-volume"]},
    )
    names = [w["name"] for w in client.get("/api/workflows").json()["workflows"]]
    assert "external" not in names
