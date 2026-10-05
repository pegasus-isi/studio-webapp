"""The admin feedback-collection script (scripts/collect_feedback.py).

It runs against live users' databases on the access point, so the properties
that matter are: it never writes to a database, one broken or old database
never stops the run, and pre-existing workspaces stay out of the timing figure.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import sqlite3
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "collect_feedback.py"


@pytest.fixture(scope="module")
def cf():
    spec = importlib.util.spec_from_file_location("collect_feedback", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_db(path: Path, settings: dict, tables: dict | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL,"
        " updated_at TEXT NOT NULL)"
    )
    for key, value in settings.items():
        conn.execute(
            "INSERT INTO app_settings VALUES (?, ?, 'x')", (key, json.dumps(value))
        )
    for table, (cols, rows) in (tables or {}).items():
        conn.execute(f"CREATE TABLE {table} ({', '.join(cols)})")
        for row in rows:
            conn.execute(
                f"INSERT INTO {table} VALUES ({', '.join('?' * len(cols))})", row
            )
    conn.commit()
    conn.close()
    return path


def _funnel(seen: str, success: str | None, pre: bool = False) -> dict:
    data = {
        "first_seen_at": seen,
        "first_workflow_at": seen,
        "first_run_at": seen,
        "preexisting_workspace": pre,
    }
    if success:
        data["first_success_at"] = success
    return data


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def homes(tmp_path):
    root = tmp_path / "home"
    mf_cols = ["session_id", "message_id", "conversation_id", "model", "agent",
               "rating", "created_at", "updated_at"]
    # alice: current image, everything present.
    _make_db(
        root / "alice" / ".studio" / "studio.db",
        {
            "onboarding_funnel": _funnel(
                "2026-10-01T10:00:00+00:00", "2026-10-01T12:00:00+00:00"
            ),
            "user_profile": {
                "research_field": "seismology", "role": "faculty",
                "pegasus_experience": "never", "coding_comfort": 2,
                "answered_at": "2026-10-01T10:05:00+00:00",
            },
        },
        {
            "message_feedback": (
                mf_cols,
                [
                    ("s", "m1", "c", "kimi", "builtin", 1,
                     "2026-10-01T11:00:00+00:00", "2026-10-01T11:00:00+00:00"),
                    ("s", "m0", "c", "kimi", "builtin", -1,
                     "2026-09-01T11:00:00+00:00", "2026-09-01T11:00:00+00:00"),
                ],
            ),
            "feedback_episodes": (
                ["id", "workflow", "run_id", "failure_category", "failed_at"],
                [("e1", "wf", "r1", "oom", "2026-10-02T09:00:00+00:00")],
            ),
            "feedback_giveups": (
                ["id", "created_at", "conversation_id", "episode_id", "reason", "tag"],
                [("g1", "2026-10-02T10:00:00+00:00", "c", "e1", "tired", "t")],
            ),
        },
    )
    # bob: older image at the legacy path, no feedback tables, no profile.
    _make_db(
        root / "bob" / "work" / ".studio" / "studio.db",
        {"onboarding_funnel": _funnel(
            "2026-10-01T09:00:00+00:00", "2026-10-01T13:00:00+00:00"
        )},
    )
    # carol: pre-existing workspace — succeeds instantly, must not count.
    _make_db(
        root / "carol" / ".studio" / "studio.db",
        {"onboarding_funnel": _funnel(
            "2026-10-01T09:00:00+00:00", "2026-10-01T09:00:01+00:00", pre=True
        )},
    )
    # dave: not a database at all.
    (root / "dave" / ".studio").mkdir(parents=True)
    (root / "dave" / ".studio" / "studio.db").write_bytes(b"not sqlite" * 50)
    # erin: a home with no Studio state, and a stray file in /home.
    (root / "erin").mkdir()
    (root / "README").write_text("not a home")
    return root


def test_collects_all_files_and_tolerates_old_and_broken_dbs(cf, homes, tmp_path, capsys):
    out = tmp_path / "out"
    summary = cf.run(homes, out, since=None)

    assert summary["users_found"] == 4  # alice, bob, carol, dave
    assert summary["unreadable"] == 1  # dave
    assert "dave" in capsys.readouterr().err

    funnel = {r["user"]: r for r in _read(out / "funnel.csv")}
    assert set(funnel) == {"alice", "bob", "carol"}
    assert funnel["carol"]["preexisting_workspace"] == "True"
    assert funnel["bob"]["first_success_at"] == "2026-10-01T13:00:00+00:00"

    profiles = _read(out / "profiles.csv")
    assert [p["user"] for p in profiles] == ["alice"]
    assert profiles[0]["role"] == "faculty"
    assert profiles[0]["coding_comfort"] == "2"

    feedback = _read(out / "message_feedback.csv")
    assert len(feedback) == 2
    assert {r["user"] for r in feedback} == {"alice"}
    assert len(_read(out / "episodes.csv")) == 1
    assert _read(out / "giveups.csv")[0]["reason"] == "tired"


def test_median_excludes_preexisting_workspaces(cf, homes, tmp_path):
    summary = cf.run(homes, tmp_path / "out", since=None)
    # alice 2h, bob 4h; carol's one-second "success" is left out.
    assert summary["median_hours_to_first_success"] == pytest.approx(3.0)
    assert summary["new_users_succeeded"] == 2


def test_since_filters_each_file_on_its_own_timestamp(cf, homes, tmp_path):
    out = tmp_path / "out"
    cf.run(homes, out, since="2026-10-02")
    # Every funnel started on 10-01; every profile was answered on 10-01.
    assert _read(out / "funnel.csv") == []
    assert _read(out / "profiles.csv") == []
    assert _read(out / "message_feedback.csv") == []
    assert len(_read(out / "episodes.csv")) == 1
    assert len(_read(out / "giveups.csv")) == 1

    cf.run(homes, out, since="2026-10-01")
    assert len(_read(out / "message_feedback.csv")) == 1  # drops the 09-01 row


def test_new_path_wins_over_legacy(cf, tmp_path):
    root = tmp_path / "home"
    _make_db(root / "u" / ".studio" / "studio.db",
             {"user_profile": {"role": "postdoc", "answered_at": "2026-10-01"}})
    _make_db(root / "u" / "work" / ".studio" / "studio.db",
             {"user_profile": {"role": "faculty", "answered_at": "2026-10-01"}})
    out = tmp_path / "out"
    cf.run(root, out, since=None)
    assert _read(out / "profiles.csv")[0]["role"] == "postdoc"


def test_never_writes_to_any_database(cf, homes, tmp_path):
    dbs = sorted(homes.rglob("studio.db"))
    before = {p: (_digest(p), p.stat().st_mtime_ns) for p in dbs}
    siblings_before = {p for p in homes.rglob("*")}

    cf.run(homes, tmp_path / "out", since=None)

    assert {p: (_digest(p), p.stat().st_mtime_ns) for p in dbs} == before
    # No journal, -wal or -shm left beside any database.
    assert {p for p in homes.rglob("*")} == siblings_before


def test_readonly_open_refuses_writes(cf, homes):
    conn = cf.open_readonly(homes / "alice" / ".studio" / "studio.db")
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM app_settings")
    finally:
        conn.close()


def test_locked_database_is_skipped_not_fatal(cf, homes, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cf, "_BUSY_TIMEOUT_S", 0.05)
    locker = sqlite3.connect(homes / "alice" / ".studio" / "studio.db")
    locker.execute("BEGIN EXCLUSIVE")
    try:
        summary = cf.run(homes, tmp_path / "out", since=None)
    finally:
        locker.rollback()
        locker.close()
    assert summary["unreadable"] == 2  # alice (locked) + dave (garbage)
    assert "alice" in capsys.readouterr().err
    users = {r["user"] for r in _read(tmp_path / "out" / "funnel.csv")}
    assert users == {"bob", "carol"}


def test_missing_homes_root_writes_empty_csvs(cf, tmp_path, capsys):
    out = tmp_path / "out"
    summary = cf.run(tmp_path / "nope", out, since=None)
    assert summary["users_found"] == 0
    assert "cannot list" in capsys.readouterr().err
    assert (out / "funnel.csv").read_text().startswith("user,")


def test_cli_rejects_bad_since_and_prints_summary(cf, homes, tmp_path, capsys):
    with pytest.raises(SystemExit):
        cf.main(["--out", str(tmp_path / "o"), "--since", "10/01/2026"])
    assert cf.main(["--homes", str(homes), "--out", str(tmp_path / "o")]) == 0
    printed = capsys.readouterr().out
    assert "users with a Studio database: 4" in printed
    assert "median" in printed


def test_script_is_stdlib_only():
    """It runs on the AP with the system Python — no app dependencies."""
    import ast
    import sys

    tree = ast.parse(_SCRIPT.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}
    assert os.access(_SCRIPT, os.R_OK)
