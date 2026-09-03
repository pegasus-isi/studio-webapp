"""Workspace file operation endpoints.

Ported _safe_path() from pegasus-ai-extension/tools.py.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from models import (
    FileEntry,
    FileListResponse,
    FileReadResponse,
    FileWriteRequest,
    MkdirRequest,
)
from workspace import is_protected, workspace_root

log = structlog.get_logger()

router = APIRouter(prefix="/api/files", tags=["files"])

WORKSPACE_ROOT = workspace_root()

_MAX_READ_BYTES = 100 * 1024  # 100 KB

# Where uploads land by default. A single, predictable directory is the point:
# a scientist should be able to answer "where did my file go?" without learning
# the workspace layout (#241, Phase 2).
DATA_DIR_NAME = "data"

# Upload ceiling. nginx caps the request body at 100M (docker/nginx/default.conf),
# and a multipart envelope adds boundary + headers on top of the file itself, so
# the app limit sits below nginx's — otherwise the largest "allowed" file is
# rejected by the proxy with a 413 the app never sees and cannot explain.
MAX_UPLOAD_BYTES = int(
    os.environ.get("STUDIO_UPLOAD_MAX_BYTES", str(95 * 1024 * 1024))
)

# Refuse an upload that would leave the workspace this close to full. Filling a
# user's disk breaks Pegasus in ways that look nothing like "the disk is full".
_MIN_FREE_BYTES = 256 * 1024 * 1024  # 256 MB

_UPLOAD_CHUNK = 1024 * 1024  # 1 MB


def _safe_path(path: str) -> str:
    """Resolve path and verify it's inside WORKSPACE_ROOT.

    Returns the resolved absolute path or raises HTTPException.
    """
    if not path:
        return WORKSPACE_ROOT
    p = path.strip()
    if not os.path.isabs(p):
        # tolerate relative paths like "workflows/..." — join to WORKSPACE_ROOT
        # so "workflows/hello.py" → "/home/.../work/workflows/hello.py"
        p = os.path.join(WORKSPACE_ROOT, p)
    resolved = os.path.realpath(os.path.expanduser(p))
    root = os.path.realpath(WORKSPACE_ROOT)
    if not resolved.startswith(root + os.sep) and resolved != root:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: path must be inside {WORKSPACE_ROOT}",
        )
    if is_protected(resolved, root):
        # The root is $HOME now, so "inside the workspace" is no longer enough
        # on its own — it includes the Condor token and Studio's own database.
        raise HTTPException(
            status_code=403,
            detail="Access denied: that directory holds credentials",
        )
    return resolved


def safe_upload_name(filename: str) -> str:
    """A filename safe to join onto a directory, or raise HTTPException.

    Browsers mostly send a bare name, but the multipart filename is
    client-controlled and some clients send a full path — including Windows
    ones, whose separator `os.path.basename` ignores on Linux. Strip both, then
    reject anything that isn't a plain name.
    """
    import ntpath

    name = ntpath.basename(os.path.basename(filename or "")).strip()
    # A leading dot is legal but hides the file from the very listing the user
    # is about to look at, which reads as a failed upload.
    if not name or name in (".", "..") or name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid file name")
    if os.sep in name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Invalid file name")
    return name


def _unique_path(directory: str, name: str) -> str:
    """A path in `directory` for `name` that doesn't already exist.

    Uploads never overwrite. Someone re-uploading a corrected file expects to
    replace it, but someone uploading `data.csv` from a second project expects
    both to survive — and silently destroying the first is unrecoverable while
    an extra file is merely untidy.
    """
    candidate = os.path.join(directory, name)
    if not os.path.exists(candidate):
        return candidate
    stem, ext = os.path.splitext(name)
    for n in range(2, 1000):
        candidate = os.path.join(directory, f"{stem} ({n}){ext}")
        if not os.path.exists(candidate):
            return candidate
    raise HTTPException(status_code=409, detail="Too many files with that name")


@router.get("/upload-limit")
async def upload_limit() -> dict[str, int | str]:
    """Ceiling and free space, so the UI can warn *before* a long upload.

    A size check that only happens after the bytes are sent is not a check; it
    is a way to waste somebody's afternoon on a slow connection.
    """
    try:
        free = shutil.disk_usage(WORKSPACE_ROOT).free
    except OSError:
        free = 0
    return {
        "max_bytes": MAX_UPLOAD_BYTES,
        "free_bytes": free,
        "data_dir": os.path.join(WORKSPACE_ROOT, DATA_DIR_NAME),
    }


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    dest: str = Form(DATA_DIR_NAME),
) -> dict[str, str | int]:
    """Stream an uploaded file into the workspace (#241, Phase 2).

    Studio had no way to get data in: a scientist's files are on their laptop,
    and the only routes were JupyterLab's uploader or scp — the escape hatches
    this redesign is trying not to require. Without this the Runner persona
    cannot start their task at all.

    Streamed in chunks rather than read whole: these are data files, and
    buffering a 90 MB upload in memory per concurrent request is how a
    single-container-per-user service falls over.
    """
    # `dest` is relative to the workspace; _safe_path rejects anything escaping.
    target_dir = _safe_path(os.path.join(WORKSPACE_ROOT, dest or DATA_DIR_NAME))
    name = safe_upload_name(file.filename or "")

    try:
        free = shutil.disk_usage(WORKSPACE_ROOT).free
    except OSError:
        free = None
    if free is not None and free < _MIN_FREE_BYTES:
        raise HTTPException(
            status_code=507,
            detail=(
                "Not enough free space in your workspace. Delete some old runs "
                "or files and try again."
            ),
        )

    os.makedirs(target_dir, exist_ok=True)
    path = _unique_path(target_dir, name)

    written = 0
    try:
        with open(path, "wb") as out:
            while chunk := await file.read(_UPLOAD_CHUNK):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"File is larger than the "
                            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload "
                            "limit."
                        ),
                    )
                out.write(chunk)
    except Exception:
        # Never leave a partial file behind pretending to be data — a truncated
        # CSV that looks uploaded is worse than a failed upload.
        try:
            os.unlink(path)
        except OSError:
            pass
        raise

    log.info("file_uploaded", path=path, bytes=written)
    return {
        "path": path,
        "name": os.path.basename(path),
        "size": written,
        # Relative to the workspace, for display and for pasting into a run
        # parameter without exposing the absolute container path. Both sides
        # are resolved first: `path` came back from _safe_path (already a
        # realpath), so comparing it against an unresolved WORKSPACE_ROOT
        # produces a "../.." escape hatch of a path whenever any component of
        # the root is a symlink.
        "relative_path": os.path.relpath(path, os.path.realpath(WORKSPACE_ROOT)),
    }


@router.get("", response_model=FileListResponse)
async def list_files(path: str = Query("", description="Directory path")) -> FileListResponse:
    """List files and directories in the workspace."""
    resolved = _safe_path(path or WORKSPACE_ROOT)
    if not os.path.isdir(resolved):
        raise HTTPException(status_code=404, detail="Directory not found")

    entries = []
    for name in sorted(os.listdir(resolved)):
        full = os.path.join(resolved, name)
        try:
            stat = os.stat(full)
            ftype = "dir" if os.path.isdir(full) else "file"
            entries.append(FileEntry(
                name=name,
                type=ftype,
                size=stat.st_size if ftype == "file" else None,
                modified=datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            ))
        except OSError:
            entries.append(FileEntry(name=name, type="unknown"))

    return FileListResponse(path=resolved, entries=entries)


@router.get("/search")
async def search_files(
    q: str = Query("", max_length=100, description="Fuzzy search query"),
    limit: int = Query(20, ge=1, le=50, description="Max results"),
) -> list[dict]:
    """Fuzzy file/dir search under WORKSPACE_ROOT for @ mentions.

    Returns list of {path, type: file|dir} relative to WORKSPACE_ROOT,
    capped and sorted. Respects .gitignore-ish ignores.
    """
    q = q.strip().lower()
    results: list[dict] = []
    ignore_dirs = {
        ".git",
        ".hg",
        "node_modules",
        ".pegasus",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        "site-packages",
        ".opencode",
        ".ipynb_checkpoints",
        ".venv",
        "venv",
    }
    for root, dirs, files in os.walk(WORKSPACE_ROOT):
        # prune ignored dirs in-place
        dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]
        rel_root = os.path.relpath(root, WORKSPACE_ROOT)
        if rel_root == ".":
            rel_root = ""
        for name in dirs + files:
            rel = os.path.join(rel_root, name) if rel_root else name
            if q and q not in rel.lower():
                continue
            full = os.path.join(root, name)
            try:
                is_dir = os.path.isdir(full)
            except OSError:
                continue
            # _safe_path check — reuse confinement
            try:
                _safe_path(rel)
            except HTTPException:
                continue
            results.append({"path": rel, "type": "dir" if is_dir else "file"})
            if len(results) >= limit * 3:
                break
        if len(results) >= limit * 3:
            break
    if q:
        # prefix match first, then dirs before files, then alphabetical
        results.sort(
            key=lambda r: (
                0 if r["path"].lower().startswith(q) else 1,
                0 if r["type"] == "dir" else 1,
                r["path"].lower(),
            )
        )
    else:
        results.sort(key=lambda r: (0 if r["type"] == "dir" else 1, r["path"].lower()))
    return results[:limit]


@router.get("/read", response_model=FileReadResponse)
async def read_file(path: str = Query(..., description="File path")) -> FileReadResponse:
    """Read file content (100KB limit)."""
    resolved = _safe_path(path)
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")

    size = os.path.getsize(resolved)
    try:
        with open(resolved, "r") as f:
            content = f.read(_MAX_READ_BYTES)
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Binary file, cannot read as text")

    return FileReadResponse(path=resolved, content=content, size=size)


@router.post("/write")
async def write_file(req: FileWriteRequest) -> dict[str, str | int]:
    """Write file content."""
    resolved = _safe_path(req.path)
    os.makedirs(os.path.dirname(resolved), exist_ok=True)
    with open(resolved, "w") as f:
        f.write(req.content)
    log.info("file_written", path=resolved, size=len(req.content))
    return {"path": resolved, "size": len(req.content), "status": "ok"}


@router.delete("")
async def delete_file(path: str = Query(..., description="Path to delete")) -> dict[str, str]:
    """Delete a file or directory."""
    resolved = _safe_path(path)
    if not os.path.exists(resolved):
        raise HTTPException(status_code=404, detail="Path not found")

    if os.path.isdir(resolved):
        shutil.rmtree(resolved)
    else:
        os.remove(resolved)

    log.info("file_deleted", path=resolved)
    return {"status": "deleted", "path": resolved}


@router.post("/mkdir")
async def mkdir(req: MkdirRequest) -> dict[str, str]:
    """Create a directory."""
    resolved = _safe_path(req.path)
    os.makedirs(resolved, exist_ok=True)
    log.info("directory_created", path=resolved)
    return {"status": "created", "path": resolved}
