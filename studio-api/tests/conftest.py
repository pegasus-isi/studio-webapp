"""Test fixtures for studio-api."""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite database path."""
    return str(tmp_path / "test_studio.db")


@pytest.fixture
def client(tmp_db, monkeypatch):
    """Create a TestClient with a patched database path."""
    # Patch the database path before importing main
    monkeypatch.setenv("HOME", tempfile.mkdtemp())

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
