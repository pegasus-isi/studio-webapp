#!/usr/bin/env python3
"""Collect early-user feedback from every Studio home into CSVs.

Run by an admin on the host that mounts the users' homes (the HTCondor access
point), e.g.::

    sudo python3 collect_feedback.py --homes /home --out ./feedback-$(date +%F)

Standard library only, on purpose: the AP has a system Python and none of the
app's dependencies, and this should not need a venv to answer "how did the
dry run go".

Each user's Studio state is a SQLite file in their home. This script opens
every one of them **read-only** (`mode=ro` URI) and never writes: the files
belong to live user sessions, and a collection script that took a write lock
or created a journal could fail a user's request mid-turn. For the same reason
every failure is per-user — a locked, corrupt or unreadable database is reported
on stderr and skipped, never fatal to the run.

Older images lack some of the tables (`message_feedback`, `feedback_episodes`,
`feedback_giveups`) and settings rows (`user_profile`); a missing one yields
no rows for that user rather than an error, so one script works across a
cohort spread over several releases.

Output (in --out):

    funnel.csv            one row per user: onboarding milestones and the
                          preexisting_workspace flag (exclude those from
                          cohort timing — see services/onboarding_metrics.py)
    profiles.csv          one row per user who answered the profile
    message_feedback.csv  thumbs up/down on chat replies
    episodes.csv          failure episodes and the prompts answered about them
    giveups.csv           recorded give-ups
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

# Where each home keeps the database. The second is the pre-move location
# (`_LEGACY_DB_PATH` in db.py): an image that never started after the move
# still has it there, and the app only moves it when the new path is absent —
# so when both exist the new one is the live one.
DB_RELATIVE_PATHS: tuple[str, ...] = (
    os.path.join(".studio", "studio.db"),
    os.path.join("work", ".studio", "studio.db"),
)

FUNNEL_MILESTONES: tuple[str, ...] = (
    "first_seen",
    "first_workflow",
    "first_run",
    "first_success",
)

PROFILE_FIELDS: tuple[str, ...] = (
    "research_field",
    "role",
    "pegasus_experience",
    "coding_comfort",
    "answered_at",
)

# table -> (output file, the timestamp column --since filters on)
TABLE_EXPORTS: tuple[tuple[str, str, str], ...] = (
    ("message_feedback", "message_feedback.csv", "created_at"),
    ("feedback_episodes", "episodes.csv", "failed_at"),
    ("feedback_giveups", "giveups.csv", "created_at"),
)

# Short: a database that stays locked for longer than this belongs to a busy
# session, and waiting on it only delays every user after it.
_BUSY_TIMEOUT_S = 2.0


def warn(msg: str) -> None:
    print(f"collect_feedback: {msg}", file=sys.stderr)


def find_databases(homes: Path) -> list[tuple[str, Path]]:
    """(user, db path) for every home that has a Studio database."""
    found: list[tuple[str, Path]] = []
    try:
        entries = sorted(os.scandir(homes), key=lambda e: e.name)
    except OSError as e:
        warn(f"cannot list {homes}: {e}")
        return found
    for entry in entries:
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        for rel in DB_RELATIVE_PATHS:
            candidate = Path(entry.path) / rel
            try:
                if candidate.is_file():
                    found.append((entry.name, candidate))
                    break
            except OSError as e:
                # A home we cannot even stat (root_squash on NFS, mode 700
                # without sudo) — say so, because silently missing users is
                # exactly what makes a cohort count wrong.
                warn(f"{entry.name}: cannot check {candidate}: {e}")
                break
    return found


def open_readonly(path: Path) -> sqlite3.Connection:
    # as_uri() percent-encodes the path, so a username with a space or `?`
    # cannot turn into URI parameters.
    conn = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=_BUSY_TIMEOUT_S
    )
    conn.row_factory = sqlite3.Row
    return conn


def read_setting(conn: sqlite3.Connection, key: str) -> Any:
    """A JSON value from app_settings, or None if absent or unparseable."""
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return None
        raise
    if row is None:
        return None
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return None


def table_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    """Every row of `table` as a dict; [] when the table does not exist."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if not exists:
        return []
    # The name is from TABLE_EXPORTS, never from input, and was just confirmed
    # to exist — interpolation is safe here.
    cur = conn.execute(f'SELECT * FROM "{table}"')
    return [dict(r) for r in cur.fetchall()]


def on_or_after(value: Any, since: str | None) -> bool:
    """Whether a stored ISO timestamp falls on or after `since` (YYYY-MM-DD).

    Compares the date prefix as a string: every timestamp Studio writes is ISO
    8601, and comparing prefixes avoids tripping over the mix of naive and
    UTC-offset values different releases stored. No timestamp means it cannot
    be placed in the window, so with --since it is left out.
    """
    if since is None:
        return True
    if not value:
        return False
    return str(value)[:10] >= since


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def collect_user(
    user: str, path: Path, since: str | None
) -> dict[str, Any] | None:
    """Everything one database contributes, or None if it could not be read."""
    try:
        conn = open_readonly(path)
    except sqlite3.Error as e:
        warn(f"{user}: cannot open {path}: {e}")
        return None
    try:
        out: dict[str, Any] = {"funnel": None, "profile": None, "tables": {}}

        funnel = read_setting(conn, "onboarding_funnel")
        if isinstance(funnel, dict) and on_or_after(
            funnel.get("first_seen_at"), since
        ):
            row: dict[str, Any] = {"user": user}
            for m in FUNNEL_MILESTONES:
                row[f"{m}_at"] = funnel.get(f"{m}_at") or ""
            row["preexisting_workspace"] = bool(funnel.get("preexisting_workspace"))
            out["funnel"] = row

        profile = read_setting(conn, "user_profile")
        if isinstance(profile, dict) and on_or_after(
            profile.get("answered_at"), since
        ):
            prow: dict[str, Any] = {"user": user}
            for f in PROFILE_FIELDS:
                value = profile.get(f)
                prow[f] = "" if value is None else value
            out["profile"] = prow

        for table, _, ts_col in TABLE_EXPORTS:
            try:
                rows = table_rows(conn, table)
            except sqlite3.Error as e:
                warn(f"{user}: cannot read {table}: {e}")
                rows = []
            out["tables"][table] = [
                {"user": user, **r} for r in rows if on_or_after(r.get(ts_col), since)
            ]
        return out
    except sqlite3.Error as e:
        # "database is locked", "file is not a database", a disk error.
        warn(f"{user}: cannot read {path}: {e}")
        return None
    finally:
        conn.close()


def write_csv(path: Path, rows: list[dict[str, Any]], leading: Iterable[str]) -> None:
    """Write rows whose columns may differ between users' schema versions.

    The header is the union of every row's keys, `leading` first, so a column
    added in a later release still appears and older rows leave it blank.
    """
    header = list(leading)
    for r in rows:
        for k in r:
            if k not in header:
                header.append(k)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, restval="")
        writer.writeheader()
        writer.writerows(rows)


def median_hours_to_first_success(
    funnel_rows: list[dict[str, Any]],
) -> tuple[float | None, int]:
    """Median first_seen -> first_success, in hours, for new workspaces only.

    Pre-existing workspaces are excluded: their "first success" is the first
    one *recorded*, minutes after an upgrade, and would drag the median toward
    zero (see the module docstring of services/onboarding_metrics.py).
    """
    hours: list[float] = []
    for r in funnel_rows:
        if r["preexisting_workspace"]:
            continue
        start = _parse_ts(r["first_seen_at"])
        end = _parse_ts(r["first_success_at"])
        if start is None or end is None:
            continue
        try:
            hours.append((end - start).total_seconds() / 3600)
        except TypeError:
            # One naive and one aware timestamp — not comparable, skip.
            continue
    return (statistics.median(hours) if hours else None), len(hours)


def run(homes: Path, out: Path, since: str | None) -> dict[str, Any]:
    dbs = find_databases(homes)
    funnel: list[dict[str, Any]] = []
    profiles: list[dict[str, Any]] = []
    tables: dict[str, list[dict[str, Any]]] = {t: [] for t, _, _ in TABLE_EXPORTS}
    unreadable = 0

    for user, path in dbs:
        got = collect_user(user, path, since)
        if got is None:
            unreadable += 1
            continue
        if got["funnel"]:
            funnel.append(got["funnel"])
        if got["profile"]:
            profiles.append(got["profile"])
        for table, rows in got["tables"].items():
            tables[table].extend(rows)

    out.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    write_csv(
        out / "funnel.csv",
        funnel,
        ["user", *(f"{m}_at" for m in FUNNEL_MILESTONES), "preexisting_workspace"],
    )
    counts["funnel.csv"] = len(funnel)
    write_csv(out / "profiles.csv", profiles, ["user", *PROFILE_FIELDS])
    counts["profiles.csv"] = len(profiles)
    for table, filename, _ in TABLE_EXPORTS:
        write_csv(out / filename, tables[table], ["user"])
        counts[filename] = len(tables[table])

    median, succeeded = median_hours_to_first_success(funnel)
    return {
        "users_found": len(dbs),
        "unreadable": unreadable,
        "rows": counts,
        "median_hours_to_first_success": median,
        "new_users_succeeded": succeeded,
        "new_users": sum(1 for r in funnel if not r["preexisting_workspace"]),
    }


def _since(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from e
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect Studio feedback data from every user's home into CSVs."
    )
    parser.add_argument(
        "--homes", type=Path, default=Path("/home"),
        help="directory containing one home per user (default: /home)",
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="directory to write the CSVs into"
    )
    parser.add_argument(
        "--since", type=_since, default=None,
        help="only rows on or after this date (YYYY-MM-DD); the funnel filters "
        "on first_seen, profiles on answered_at",
    )
    args = parser.parse_args(argv)

    summary = run(args.homes, args.out, args.since)

    print(f"users with a Studio database: {summary['users_found']}"
          f" ({summary['unreadable']} unreadable)")
    for name, n in summary["rows"].items():
        print(f"  {name:<22} {n} rows")
    median = summary["median_hours_to_first_success"]
    if median is None:
        print(f"time to first success: no data ({summary['new_users']} new users)")
    else:
        print(
            f"time to first success (median, new users only): {median:.1f} h"
            f" ({summary['new_users_succeeded']} of {summary['new_users']}"
            " new users have succeeded)"
        )
    print(f"written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
