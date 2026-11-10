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


@pytest.fixture(autouse=True)
def _no_schedd_nudge(request, monkeypatch):
    """No background condor_reschedule from code under test (#494).

    A successful submit nudges the schedd from a background task. Under test
    that task would outlive the event loop and, worse, run through whatever
    fake a test installed for asyncio.create_subprocess_exec. Its own tests
    exercise it directly.
    """
    if request.node.module.__name__.endswith("test_schedd"):
        return
    import services.schedd as sd

    monkeypatch.setattr(sd, "nudge", lambda *a, **k: None)


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
    # knowledge adapters if e.g. opencode is on the dev machine's PATH).
    monkeypatch.setenv("STUDIO_DEFAULT_TOOLS", "")
    # The catalog fills blank descriptions from GitHub; tests must not reach
    # the network, and CI would burn the unauthenticated rate limit doing it.
    monkeypatch.setenv("STUDIO_MARKETPLACE_ENRICH", "0")

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

    # routers/files.py freezes WORKSPACE_ROOT from $HOME at import time, which
    # is before (or independent of) the HOME patch above — so anything writing
    # through the files API lands in the developer's *real* ~/work. The upload
    # endpoint made that visible by creating files there; repoint the constant
    # so no test can write outside its tmpdir. Same hazard, and same fix, as
    # STUDIO_SCAN_DIR at the top of this file.
    import routers.files

    workspace = tempfile.mkdtemp()
    monkeypatch.setattr(routers.files, "WORKSPACE_ROOT", workspace)

    with TestClient(app) as tc:
        tc.workspace_root = workspace  # type: ignore[attr-defined]
        yield tc
