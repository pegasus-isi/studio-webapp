"""Tests for the example-workflow marketplace."""

from __future__ import annotations

import os


def _seed_example(knowledge_root: str) -> None:
    ex_dir = os.path.join(knowledge_root, "examples")
    os.makedirs(ex_dir, exist_ok=True)
    with open(os.path.join(ex_dir, "workflow_generator_demo.py"), "w") as f:
        f.write('"""\nDemo workflow generator.\n\nMore details here.\n"""\n')
    # A non-matching file should be ignored.
    with open(os.path.join(ex_dir, "wrapper_python_example.py"), "w") as f:
        f.write("print('hi')\n")


def test_list_examples(client, monkeypatch):
    import routers.examples as ex

    _seed_example(os.environ["KNOWLEDGE_ROOT"])
    monkeypatch.setattr(
        ex, "EXAMPLES_DIR", os.path.join(os.environ["KNOWLEDGE_ROOT"], "examples")
    )

    body = client.get("/api/examples").json()
    ids = [e["id"] for e in body["examples"]]
    assert ids == ["demo"]  # wrapper_* ignored
    demo = body["examples"][0]
    assert demo["name"] == "Demo"
    assert "Demo workflow generator." in demo["description"]
    assert "More details" not in demo["description"]  # first paragraph only


def test_add_example_creates_project(client, monkeypatch, tmp_path):
    import routers.examples as ex

    _seed_example(os.environ["KNOWLEDGE_ROOT"])
    workflows = tmp_path / "workflows"
    monkeypatch.setattr(
        ex, "EXAMPLES_DIR", os.path.join(os.environ["KNOWLEDGE_ROOT"], "examples")
    )
    monkeypatch.setattr(ex, "WORKFLOWS_DIR", str(workflows))

    res = client.post("/api/examples/demo/add")
    assert res.status_code == 200
    body = res.json()
    assert body["project_id"] == "demo"
    assert body["overwrote"] is False
    created = workflows / "demo" / "workflow_generator.py"
    assert created.is_file()

    # Adding again overwrites and reports it.
    res2 = client.post("/api/examples/demo/add")
    assert res2.json()["overwrote"] is True


def test_add_example_unknown_404(client, monkeypatch, tmp_path):
    import routers.examples as ex

    _seed_example(os.environ["KNOWLEDGE_ROOT"])
    monkeypatch.setattr(
        ex, "EXAMPLES_DIR", os.path.join(os.environ["KNOWLEDGE_ROOT"], "examples")
    )
    monkeypatch.setattr(ex, "WORKFLOWS_DIR", str(tmp_path / "workflows"))
    assert client.post("/api/examples/nope/add").status_code == 404


def test_add_example_rejects_traversal(client):
    # Path-ish ids are rejected before any filesystem access.
    assert client.post("/api/examples/..%2f..%2fetc/add").status_code in (400, 404)
