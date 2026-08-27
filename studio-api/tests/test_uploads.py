"""Tests for workspace data upload (#241, Phase 2).

Upload is the Runner persona's first step — without it they cannot start their
task at all — so the risks worth pinning are the ones that lose or corrupt
data: a client-supplied filename escaping the workspace, an upload silently
overwriting an existing file, and a rejected upload leaving a truncated file
behind that looks like real data.
"""

from __future__ import annotations

import io
import os

import pytest
from fastapi import HTTPException

from routers import files as files_router


def _upload(client, name: str, content: bytes = b"a,b\n1,2\n", dest: str | None = None):
    data = {"dest": dest} if dest is not None else {}
    return client.post(
        "/api/files/upload",
        files={"file": (name, io.BytesIO(content), "text/csv")},
        data=data,
    )


# ── filename safety ───────────────────────────────────────────────────────


def test_safe_upload_name_keeps_a_plain_name():
    assert files_router.safe_upload_name("results.csv") == "results.csv"


@pytest.mark.parametrize(
    "attempt",
    [
        "../escape.csv",
        "/etc/passwd",
        "../../root/.ssh/authorized_keys",
        # Windows separators: os.path.basename ignores these on Linux, so a
        # client sending a Windows path would otherwise smuggle a directory in.
        r"C:\Users\me\data.csv",
        r"..\..\escape.csv",
    ],
)
def test_safe_upload_name_strips_or_rejects_paths(attempt):
    name = files_router.safe_upload_name(attempt)
    assert os.sep not in name
    assert "\\" not in name
    assert not name.startswith("..")


@pytest.mark.parametrize("attempt", ["", "   ", ".", "..", ".hidden"])
def test_safe_upload_name_rejects_unusable_names(attempt):
    with pytest.raises(HTTPException):
        files_router.safe_upload_name(attempt)


# ── happy path ────────────────────────────────────────────────────────────


def test_upload_lands_in_the_data_directory(client):
    res = _upload(client, "measurements.csv")
    assert res.status_code == 200
    body = res.json()

    assert body["name"] == "measurements.csv"
    assert body["size"] == len(b"a,b\n1,2\n")
    # The relative path is what the UI shows and what a user pastes into a run
    # parameter — it must not be an absolute container path.
    assert body["relative_path"] == os.path.join("data", "measurements.csv")
    assert os.path.isfile(body["path"])
    with open(body["path"], "rb") as fh:
        assert fh.read() == b"a,b\n1,2\n"


def test_uploaded_file_appears_in_the_listing(client):
    _upload(client, "visible.csv")
    # The listing API takes an absolute path (its _safe_path resolves a
    # relative one against the process CWD, not the workspace).
    data_dir = os.path.join(client.workspace_root, "data")
    entries = client.get(f"/api/files?path={data_dir}").json()["entries"]
    assert any(e["name"] == "visible.csv" for e in entries)


# ── collisions ────────────────────────────────────────────────────────────


def test_second_upload_of_the_same_name_does_not_overwrite(client):
    first = _upload(client, "data.csv", b"original").json()
    second = _upload(client, "data.csv", b"different").json()

    assert first["path"] != second["path"]
    assert second["name"] == "data (2).csv"
    # The original must survive: destroying it is unrecoverable, while an extra
    # file is merely untidy.
    with open(first["path"], "rb") as fh:
        assert fh.read() == b"original"


def test_collision_suffix_preserves_the_extension(client):
    _upload(client, "table.tsv")
    second = _upload(client, "table.tsv").json()
    assert second["name"].endswith(".tsv")


# ── limits ────────────────────────────────────────────────────────────────


def test_upload_over_the_limit_is_rejected(client, monkeypatch):
    monkeypatch.setattr(files_router, "MAX_UPLOAD_BYTES", 16)
    res = _upload(client, "big.csv", b"x" * 64)
    assert res.status_code == 413
    assert "limit" in res.json()["detail"].lower()


def test_a_rejected_upload_leaves_no_partial_file(client, monkeypatch):
    # A truncated CSV sitting in the data directory looking uploaded is worse
    # than a failed upload: the user runs their analysis against half a file.
    monkeypatch.setattr(files_router, "MAX_UPLOAD_BYTES", 16)
    _upload(client, "partial.csv", b"x" * 64)

    data_dir = os.path.join(client.workspace_root, "data")
    assert not os.path.exists(os.path.join(data_dir, "partial.csv"))


def test_upload_limit_endpoint_reports_the_ceiling(client):
    body = client.get("/api/files/upload-limit").json()
    assert body["max_bytes"] == files_router.MAX_UPLOAD_BYTES
    assert body["data_dir"] == os.path.join(client.workspace_root, "data")
    assert body["free_bytes"] >= 0


def test_upload_limit_stays_under_the_nginx_body_cap():
    # nginx caps the request body at 100M; the multipart envelope rides on top
    # of the file, so an app limit at or above nginx's makes the largest
    # "allowed" file fail at the proxy with a 413 the app never sees.
    assert files_router.MAX_UPLOAD_BYTES < 100 * 1024 * 1024


# ── destination safety ────────────────────────────────────────────────────


def test_upload_to_a_path_outside_the_workspace_is_refused(client):
    res = _upload(client, "sneaky.csv", dest="../../etc")
    assert res.status_code == 403


def test_upload_into_a_project_subdirectory(client):
    # Uploading straight into a workflow's own directory is legitimate — the
    # data dir is a default, not a cage.
    res = _upload(client, "inputs.csv", dest="workflows/my-analysis/inputs")
    assert res.status_code == 200
    assert res.json()["relative_path"] == os.path.join(
        "workflows", "my-analysis", "inputs", "inputs.csv"
    )
