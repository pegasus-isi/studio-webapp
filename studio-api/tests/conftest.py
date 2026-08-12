"""Test fixtures for studio-api."""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

# Freeze the scan root to a throwaway dir at collection time — BEFORE any test
# imports workflow_scanner (whose module-level SCAN_ROOT is captured on first
# import). Without this, a test that imports the scanner before the `client`
# fixture patches HOME would freeze SCAN_ROOT to the developer's real $HOME, and
# every discover_runs()/notification scan would then recursively glob the whole
# home directory — turning the suite from seconds into minutes. STUDIO_SCAN_DIR
# takes precedence over HOME in that constant, so an empty temp dir makes the
# scan instant. Individual tests may still monkeypatch SCAN_ROOT on top.
os.environ.setdefault("STUDIO_SCAN_DIR", tempfile.mkdtemp())


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite database path."""
    return str(tmp_path / "test_studio.db")


@pytest.fixture
def client(tmp_db, monkeypatch):
    """Create a TestClient with a patched database path."""
    # Patch the database path before importing main
    monkeypatch.setenv("HOME", tempfile.mkdtemp())

    # Keep startup hermetic: don't seed image-bundled tools (would run real
    # knowledge adapters if e.g. opencode is on the dev machine's PATH), and
    # default the chat engine to builtin so /stream tests never spawn a real
    # `opencode serve`. Production default is opencode.
    monkeypatch.setenv("STUDIO_DEFAULT_TOOLS", "")
    monkeypatch.setenv("STUDIO_CHAT_ENGINE_DEFAULT", "builtin")

    # Set KNOWLEDGE_ROOT to a temp dir for tests
    knowledge_dir = tempfile.mkdtemp()
    monkeypatch.setenv("KNOWLEDGE_ROOT", knowledge_dir)

    # Create required knowledge subdirs
    for subdir in ("skills", "agents", "templates", "examples", "references"):
        os.makedirs(os.path.join(knowledge_dir, subdir), exist_ok=True)

    import sys
    # Ensure studio-api is on the path
    studio_api_dir = os.path.dirname(os.path.dirname(__file__))
    if studio_api_dir not in sys.path:
        sys.path.insert(0, studio_api_dir)

    from db import Database
    from main import app

    # Replace the module-level db with our test db
    import main
    main.db = Database(db_path=tmp_db)

    with TestClient(app) as tc:
        yield tc
