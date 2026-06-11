"""Discover a workflow generator's configurable parameters at runtime.

Wraps ``introspect_argparse.py`` so the studio can build the Generate form
dynamically — each workflow's ``workflow_generator.py`` exposes different
arguments.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import structlog

log = structlog.get_logger()

_SENTINEL = "__ARGPARSE_SCHEMA__"
_TIMEOUT = 30

# Standard pegasus-plan options exposed for the Plan/Submit actions. These are
# the same across workflows (they are pegasus-plan args, not generator args).
PLAN_PARAMS: list[dict[str, Any]] = [
    {
        "dest": "site",
        "flag": "-s",
        "help": "Execution site (from the site catalog)",
        "default": "condorpool",
        "required": True,
        "is_flag": False,
        "choices": None,
    },
    {
        "dest": "output_site",
        "flag": "-o",
        "help": "Output site where final results are staged",
        "default": "local",
        "required": True,
        "is_flag": False,
        "choices": None,
    },
]


def _introspect_script() -> str:
    # introspect_argparse.py lives at the studio-api root (parent of services/)
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir, "introspect_argparse.py")
    )


async def introspect_generator(project_dir: str) -> dict[str, Any]:
    """Return the generator's parameter schema for a project directory."""
    generator = os.path.join(project_dir, "workflow_generator.py")
    if not os.path.isfile(generator):
        return {"params": [], "mutex_required": []}

    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", _introspect_script(), generator,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_dir,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_TIMEOUT
        )
    except asyncio.TimeoutError:
        return {"params": [], "mutex_required": [], "error": "introspection timed out"}
    except Exception as e:
        return {"params": [], "mutex_required": [], "error": str(e)}

    out = stdout.decode("utf-8", errors="replace")
    # Extract our sentinel-tagged JSON (the generator may log other lines).
    for line in reversed(out.splitlines()):
        if line.startswith(_SENTINEL):
            try:
                return json.loads(line[len(_SENTINEL):])
            except json.JSONDecodeError:
                break

    err = stderr.decode("utf-8", errors="replace")
    log.warning("generator_introspect_failed", project_dir=project_dir, stderr=err[:500])
    return {"params": [], "mutex_required": [], "error": err[:500] or "no schema emitted"}
