"""Workspace file operation endpoints.

Ported _safe_path() from pegasus-ai-extension/tools.py.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException, Query

from models import (
    FileEntry,
    FileListResponse,
    FileReadResponse,
    FileWriteRequest,
    MkdirRequest,
)

log = structlog.get_logger()

router = APIRouter(prefix="/api/files", tags=["files"])

WORKSPACE_ROOT = os.environ.get(
    "STUDIO_WORKSPACE_DIR",
    os.path.join(os.environ.get("HOME", "/tmp"), "work"),
)

_MAX_READ_BYTES = 100 * 1024  # 100 KB


def _safe_path(path: str) -> str:
    """Resolve path and verify it's inside WORKSPACE_ROOT.

    Returns the resolved absolute path or raises HTTPException.
    """
    if not path:
        return WORKSPACE_ROOT
    resolved = os.path.realpath(os.path.expanduser(path))
    root = os.path.realpath(WORKSPACE_ROOT)
    if not resolved.startswith(root + os.sep) and resolved != root:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: path must be inside {WORKSPACE_ROOT}",
        )
    return resolved


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
