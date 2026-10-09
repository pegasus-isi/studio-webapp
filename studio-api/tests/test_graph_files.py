"""File nodes in the workflow graph ("Show files", #458)."""

from __future__ import annotations

import yaml

_WF = {
    "name": "streamflow",
    "jobs": [
        {"id": "f1", "name": "fetch", "uses": [
            {"lfn": "data/gauge_01646500_daily.csv", "type": "output", "stageOut": False}]},
        {"id": "f2", "name": "fetch", "uses": [
            {"lfn": "data/gauge_06934500_daily.csv", "type": "output", "stageOut": False}]},
        {"id": "m", "name": "merge", "uses": [
            {"lfn": "data/gauge_01646500_daily.csv", "type": "input"},
            {"lfn": "data/gauge_06934500_daily.csv", "type": "input"},
            {"lfn": "gauges.txt", "type": "input"},
            {"lfn": "summary.csv", "type": "output", "stageOut": True}]},
        {"id": "p", "name": "plot", "uses": []},
    ],
    "jobDependencies": [
        {"id": "f1", "children": ["m"]},
        {"id": "f2", "children": ["m"]},
        {"id": "m", "children": ["p"]},  # no file behind it: must survive
    ],
}


def _graph(tmp_path, include_files):
    from routers.workflows import _parse_workflow_graph

    path = tmp_path / "workflow.yml"
    path.write_text(yaml.safe_dump(_WF))
    return _parse_workflow_graph(str(path), include_files=include_files)


def test_files_are_off_unless_asked(tmp_path):
    g = _graph(tmp_path, False)
    assert all(not n.get("file") for n in g["nodes"])
    assert {"source": "f1", "target": "m"} in g["edges"]


def test_file_nodes_edges_and_grouping_family(tmp_path):
    g = _graph(tmp_path, True)
    files = {n["lfn"]: n for n in g["nodes"] if n.get("file")}
    assert set(files) == {
        "data/gauge_01646500_daily.csv", "data/gauge_06934500_daily.csv",
        "gauges.txt", "summary.csv",
    }
    # Siblings share a family, so the viewer groups them like their steps.
    assert files["data/gauge_01646500_daily.csv"]["transformation"] == "gauge_#_daily.csv"
    assert files["data/gauge_06934500_daily.csv"]["transformation"] == "gauge_#_daily.csv"
    assert files["data/gauge_01646500_daily.csv"]["label"] == "gauge_01646500_daily.csv"
    # Only staged-out outputs are results; an input nobody writes is not.
    assert files["summary.csv"]["result"] is True
    assert files["data/gauge_01646500_daily.csv"]["result"] is False
    assert files["gauges.txt"]["result"] is False
    assert files["summary.csv"]["kind"] == "table"

    edges = {(e["source"], e["target"]) for e in g["edges"]}
    assert ("f1", "file:data/gauge_01646500_daily.csv") in edges
    assert ("file:data/gauge_01646500_daily.csv", "m") in edges
    assert ("file:gauges.txt", "m") in edges
    assert ("m", "file:summary.csv") in edges
    # A dependency now carried by a file isn't drawn twice...
    assert ("f1", "m") not in edges
    # ...but one with no file behind it stays.
    assert ("m", "p") in edges


def test_run_status_ignores_file_nodes():
    from routers.workflows import _derive_workflow_status

    nodes = [
        {"id": "a", "status": "succeeded"},
        {"id": "file:x", "status": "file", "file": True},
    ]
    assert _derive_workflow_status(nodes) == "succeeded"


def test_files_info_endpoint(client, monkeypatch, tmp_path):
    import routers.workflows as wf_router

    run = tmp_path / "run"
    run.mkdir()
    (run / "workflow.yml").write_text(yaml.safe_dump(_WF))
    out = tmp_path / "out"
    out.mkdir()
    (out / "summary.csv").write_text("a,b\n")

    async def _resolve(run_id):
        return str(run)

    async def _bases(run_dir, output_site):
        return {"result": str(out)}

    monkeypatch.setattr(wf_router, "_resolve_run_dir", _resolve)
    monkeypatch.setattr(wf_router, "_run_bases", _bases)

    body = client.get(
        "/api/workflows/r1/files/info",
        params=[("lfn", "summary.csv"), ("lfn", "gauges.txt")],
    ).json()
    by = {f["lfn"]: f for f in body["files"]}
    assert by["summary.csv"]["link"] == "output"
    assert by["summary.csv"]["location"] == "result"
    assert by["gauges.txt"]["link"] == "input"
    assert by["gauges.txt"]["location"] is None
