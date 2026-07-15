"""Example-workflow marketplace.

Surfaces the bundled example generators (knowledge/examples/
workflow_generator_*.py) so users can browse them and add one into their
workspace with a click — it then shows up on the Workflows page like any
other project.
"""

from __future__ import annotations

import os
import re
import shutil

import structlog
from fastapi import APIRouter, HTTPException

log = structlog.get_logger()

router = APIRouter(prefix="/api/examples", tags=["examples"])

KNOWLEDGE_ROOT = os.environ.get("KNOWLEDGE_ROOT", "/opt/pegasus-ai/knowledge")
EXAMPLES_DIR = os.path.join(KNOWLEDGE_ROOT, "examples")
WORKSPACE_ROOT = os.environ.get(
    "STUDIO_WORKSPACE_DIR",
    os.path.join(os.environ.get("HOME", "/tmp"), "work"),
)
WORKFLOWS_DIR = os.path.join(WORKSPACE_ROOT, "workflows")

_PREFIX = "workflow_generator_"


def _title(example_id: str) -> str:
    return example_id.replace("_", " ").replace("-", " ").title()


def _description(path: str) -> str:
    """First paragraph of the file's module docstring, as a short summary."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(4000)
    except OSError:
        return ""
    m = re.search(r'"""(.*?)"""', text, re.DOTALL)
    if not m:
        return ""
    body = m.group(1).strip()
    # First non-empty paragraph (up to the first blank line).
    para: list[str] = []
    for line in body.splitlines():
        if not line.strip():
            if para:
                break
            continue
        para.append(line.strip())
    return " ".join(para)[:400]


def _list_examples() -> list[dict]:
    if not os.path.isdir(EXAMPLES_DIR):
        return []
    out = []
    for fname in sorted(os.listdir(EXAMPLES_DIR)):
        if not (fname.startswith(_PREFIX) and fname.endswith(".py")):
            continue
        example_id = fname[len(_PREFIX):-3]
        out.append({
            "id": example_id,
            "name": _title(example_id),
            "description": _description(os.path.join(EXAMPLES_DIR, fname)),
            "filename": fname,
        })
    return out


@router.get("")
async def list_examples() -> dict:
    """Browse the available example workflows."""
    return {"examples": _list_examples()}


@router.post("/{example_id}/add")
async def add_example(example_id: str) -> dict:
    """Copy an example generator into the workspace as a new project.

    Creates ``<workspace>/workflows/<example_id>/workflow_generator.py``. The
    project then appears on the Workflows page (Generate/Plan/Run).
    """
    # example_id comes from the listing (safe), but validate anyway — no path
    # separators or traversal.
    if not re.fullmatch(r"[A-Za-z0-9_-]+", example_id):
        raise HTTPException(status_code=400, detail="Invalid example id")

    src = os.path.join(EXAMPLES_DIR, f"{_PREFIX}{example_id}.py")
    if not os.path.isfile(src):
        raise HTTPException(status_code=404, detail=f"Unknown example: {example_id}")

    project_dir = os.path.join(WORKFLOWS_DIR, example_id)
    dest = os.path.join(project_dir, "workflow_generator.py")
    already = os.path.exists(dest)
    try:
        os.makedirs(project_dir, exist_ok=True)
        shutil.copy2(src, dest)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not add example: {e}")

    log.info("example_added", example_id=example_id, dest=dest, overwrote=already)
    return {
        "status": "added",
        "project_id": example_id,
        "project_dir": project_dir,
        "overwrote": already,
    }
