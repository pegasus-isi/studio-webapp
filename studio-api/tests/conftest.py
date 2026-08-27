"""Test fixtures for studio-api."""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

# Point the scan root at a throwaway dir at collection time. discover_runs()
# resolves its root from STUDIO_SCAN_DIR (via the active compute backend) at
# call time; without this, a test that scans before the `client` fixture
# patches HOME would recursively glob the developer's entire real $HOME —
# turning the suite from seconds into minutes. An empty temp dir makes the
# scan instant. Individual tests monkeypatch.setenv("STUDIO_SCAN_DIR", ...)
# on top.
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

    # routers/files.py freezes WORKSPACE_ROOT from $HOME at import time, which
    # is before (or independent of) the HOME patch above — so anything writing
    # through the files API lands in the developer's *real* ~/work. The upload
    # endpoint made that visible by creating files there; repoint the constant
    # so no test can write outside its tmpdir. Same hazard, and same fix, as
    # STUDIO_SCAN_DIR at the top of this file.
    import routers.files

    workspace = tempfile.mkdtemp()
    monkeypatch.setattr(routers.files, "WORKSPACE_ROOT", workspace)

    # The chat-turn broker is a process-wide singleton, so a turn started by
    # one test is still "in flight" for the next one — which made a test
    # asserting "no turn in progress" pass alone and fail in the suite. Give
    # each test its own, the same way main.db is replaced above.
    from services import chat_turn

    monkeypatch.setattr(chat_turn, "manager", chat_turn.ChatTurnManager())

    with TestClient(app) as tc:
        tc.workspace_root = workspace  # type: ignore[attr-defined]
        yield tc
