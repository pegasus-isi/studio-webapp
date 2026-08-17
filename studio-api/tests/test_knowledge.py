"""Tests for knowledge layer endpoints."""

from __future__ import annotations

import json
import os


def test_list_skills_empty(client):
    resp = client.get("/api/knowledge/skills")
    assert resp.status_code == 200
    data = resp.json()
    assert "skills" in data
    assert isinstance(data["skills"], list)


def test_list_skills_with_data(client, monkeypatch):
    from knowledge import layout

    knowledge_root = layout.knowledge_root()

    # Create a test skill
    skill_dir = os.path.join(knowledge_root, "skills", "scaffold")
    os.makedirs(skill_dir, exist_ok=True)
    with open(os.path.join(skill_dir, "metadata.json"), "w") as f:
        json.dump(
            {"name": "scaffold", "description": "Scaffold a workflow", "slash_command": "/scaffold"},
            f,
        )
    with open(os.path.join(skill_dir, "canonical.md"), "w") as f:
        f.write("# Scaffold Skill\nCreate a new Pegasus workflow.")

    resp = client.get("/api/knowledge/skills")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["skills"]) >= 1
    assert data["skills"][0]["name"] == "scaffold"


def test_get_skill(client):
    from knowledge import layout

    knowledge_root = layout.knowledge_root()

    skill_dir = os.path.join(knowledge_root, "skills", "test-skill")
    os.makedirs(skill_dir, exist_ok=True)
    with open(os.path.join(skill_dir, "canonical.md"), "w") as f:
        f.write("# Test Skill Content")
    with open(os.path.join(skill_dir, "metadata.json"), "w") as f:
        json.dump({"name": "test-skill", "description": "A test skill"}, f)

    resp = client.get("/api/knowledge/skills/test-skill")
    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] == "# Test Skill Content"


def test_get_skill_not_found(client):
    resp = client.get("/api/knowledge/skills/nonexistent")
    assert resp.status_code == 404


def test_list_agents(client):
    resp = client.get("/api/knowledge/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert "agents" in data
    # Should always have the "general" agent
    ids = [a["id"] for a in data["agents"]]
    assert "general" in ids


def test_list_templates_empty(client):
    resp = client.get("/api/knowledge/templates")
    assert resp.status_code == 200
    data = resp.json()
    assert "templates" in data


def test_list_examples_empty(client):
    resp = client.get("/api/knowledge/examples")
    assert resp.status_code == 200
    data = resp.json()
    assert "examples" in data


def test_skill_metadata_comes_from_frontmatter(client):
    """A skill in the plugin layout is described by its own frontmatter.

    metadata.json no longer exists upstream, so the description has to come from
    the document itself -- which also means it cannot drift from it.
    """
    from knowledge import layout

    skill_dir = os.path.join(layout.knowledge_root(), "skills", "pegasus-scaffold")
    os.makedirs(skill_dir, exist_ok=True)
    with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
        f.write(
            "---\n"
            "name: pegasus-scaffold\n"
            "description: Create a complete Pegasus workflow project\n"
            "order: 1\n"
            "allowed-tools:\n"
            "  - Read\n"
            "---\n\n# Scaffold\n"
        )

    resp = client.get("/api/knowledge/skills/pegasus-scaffold")
    assert resp.status_code == 200
    meta = resp.json()["metadata"]
    assert meta["description"] == "Create a complete Pegasus workflow project"
    assert meta["slash_command"] == "/pegasus-scaffold"

    listing = client.get("/api/knowledge/skills").json()["skills"]
    # order: 1 sorts it ahead of anything unordered.
    assert listing[0]["slash_command"] == "/pegasus-scaffold"


def test_folded_description_is_joined(tmp_path):
    """kiso's description is a YAML folded scalar spanning several lines."""
    from knowledge import layout

    doc = tmp_path / "SKILL.md"
    doc.write_text(
        "---\n"
        "name: kiso\n"
        "description: >\n"
        "  Generate a Kiso experiment.yml configuration file\n"
        "  for running Pegasus workflows.\n"
        "allowed-tools:\n"
        "  - Read\n"
        "---\n\nbody\n"
    )
    front = layout.parse_frontmatter(str(doc))
    assert front["name"] == "kiso"
    assert front["description"] == (
        "Generate a Kiso experiment.yml configuration file "
        "for running Pegasus workflows."
    )


def test_knowledge_root_resolution_order(monkeypatch, tmp_path):
    """Env wins over the installed package, which wins over the legacy path.

    Resolved per call, not cached at import: tests and the container set this
    differently, and an import-time constant would freeze whichever ran first.
    """
    from knowledge import layout

    monkeypatch.setenv("KNOWLEDGE_ROOT", str(tmp_path))
    assert layout.knowledge_root() == str(tmp_path)

    monkeypatch.delenv("KNOWLEDGE_ROOT", raising=False)
    monkeypatch.setenv("KNOWLEDGE_MARKETPLACE", str(tmp_path / "absent"))
    assert layout.knowledge_root() == "/opt/pegasus-ai/knowledge"

    # The image sets KNOWLEDGE_ROOT, but a checkout is found without it too.
    checkout = tmp_path / "marketplace"
    (checkout / "plugins" / "pegasus-ai").mkdir(parents=True)
    monkeypatch.setenv("KNOWLEDGE_MARKETPLACE", str(checkout))
    assert layout.knowledge_root() == str(checkout / "plugins" / "pegasus-ai")


def test_templates_prefers_plugin_assets_layout(monkeypatch, tmp_path):
    """assets/templates (plugin layout) wins over templates/ (old Studio layout)."""
    from knowledge import layout

    monkeypatch.setenv("KNOWLEDGE_ROOT", str(tmp_path))
    (tmp_path / "templates").mkdir()
    assert layout.templates_dir() == str(tmp_path / "templates")

    (tmp_path / "assets" / "templates").mkdir(parents=True)
    assert layout.templates_dir() == str(tmp_path / "assets" / "templates")
