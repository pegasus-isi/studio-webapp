"""Discover a workflow generator's configurable parameters at runtime.

Wraps ``introspect_argparse.py`` so the studio can build the Generate form
dynamically — each workflow's ``workflow_generator.py`` exposes different
arguments.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

import structlog

from services.procutil import communicate_or_kill

log = structlog.get_logger()

_SENTINEL = "__ARGPARSE_SCHEMA__"
_TIMEOUT = 30

# Standard pegasus-plan options exposed for the Plan/Submit actions. These are
# the same across workflows (they are pegasus-plan args, not generator args),
# and are given the same shape introspection produces
# for generator params — including the Phase 3 hints (#241), so a consumer
# never has to care which source a parameter came from.
#
# Both are marked `advanced`: the simple Run form asks "where it runs" as a
# plain-language choice and drives these from it, so surfacing the raw site
# names again would be asking the same question twice in two vocabularies.
PLAN_PARAMS: list[dict[str, Any]] = [
    {
        "dest": "site",
        "flag": "-s",
        "help": "Execution site (from the site catalog)",
        "default": "condorpool",
        "required": True,
        "is_flag": False,
        "choices": None,
        "type": "text",
        "label": "Execution site",
        "advanced": True,
    },
    {
        "dest": "output_site",
        "flag": "-o",
        "help": "Output site where final results are staged",
        "default": "local",
        "required": True,
        "is_flag": False,
        "choices": None,
        "type": "text",
        "label": "Output site",
        "advanced": True,
    },
]


def _introspect_script() -> str:
    # introspect_argparse.py lives at the studio-api root (parent of services/)
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir, "introspect_argparse.py")
    )


#: `os.environ["X"]`, `os.environ.get("X")`, `os.getenv("X")` and
#: `environ.get("X")`. A regex rather than an AST walk on purpose: this reads
#: the file without importing it, it is a hint rather than a contract, and a
#: generator that builds a variable name dynamically was never going to be
#: discoverable anyway.
_ENV_READ_RE = re.compile(
    r"""(?:os\.)?(?:environ(?:\.get)?|getenv)\s*[\[(]\s*["']([A-Z_][A-Z0-9_]*)["']""",
)

#: Read by everything, and telling the user their analysis "needs" PATH would
#: be noise that buries the one name that matters.
_ENV_UNINTERESTING = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "PWD", "TMPDIR", "TMP", "TEMP",
    "LANG", "LC_ALL", "TERM", "HOSTNAME", "PYTHONPATH", "PYTHONUNBUFFERED",
    "VIRTUAL_ENV", "CONDA_PREFIX", "PEGASUS_HOME", "PEGASUS_PYTHON",
    "CONDOR_CONFIG", "STUDIO_WORKSPACE_DIR", "SLURM_JOB_ID",
})


def generator_env_vars(generator_path: str) -> list[str]:
    """Environment variables the generator source reads, if any.

    The form can then name the credential a workflow wants instead of leaving
    the user to read the generator: crophealth needs KAGGLE_USERNAME and
    KAGGLE_KEY, and nothing in the UI said so — the run just failed on a worker
    node with a Kaggle API error (#364).

    Best-effort by construction. A name that does not appear literally in the
    source is not found, and a name that appears is not proof the workflow
    needs it for the options you chose (crophealth reads the Kaggle variables
    only for `--data-source kaggle`). It is a hint, and the UI says so.
    """
    try:
        with open(generator_path, "r", errors="replace") as f:
            source = f.read()
    except OSError as e:
        log.warning("generator_env_scan_failed", path=generator_path, error=str(e))
        return []
    found = {
        name for name in _ENV_READ_RE.findall(source)
        if name not in _ENV_UNINTERESTING
    }
    return sorted(found)


async def introspect_generator(project_dir: str) -> dict[str, Any]:
    """Return the generator's parameter schema for a project directory."""
    from services.workflow_scanner import find_generator

    generator = find_generator(project_dir)
    if not generator:
        return {"params": [], "mutex_required": [], "env_vars": []}

    env_vars = generator_env_vars(generator)

    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", _introspect_script(), generator,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_dir,
        )
        stdout, stderr = await communicate_or_kill(proc, _TIMEOUT)
    except asyncio.TimeoutError:
        return {
            "params": [], "mutex_required": [], "env_vars": env_vars,
            "error": "introspection timed out",
        }
    except Exception as e:
        return {
            "params": [], "mutex_required": [], "env_vars": env_vars,
            "error": str(e),
        }

    out = stdout.decode("utf-8", errors="replace")
    # Extract our sentinel-tagged JSON (the generator may log other lines).
    for line in reversed(out.splitlines()):
        if line.startswith(_SENTINEL):
            try:
                schema = json.loads(line[len(_SENTINEL):])
            except json.JSONDecodeError:
                break
            # The scan is ours, not introspect_argparse's, so it is merged in
            # here rather than plumbed through the subprocess protocol.
            schema.setdefault("env_vars", env_vars)
            return schema

    err = stderr.decode("utf-8", errors="replace")
    log.warning("generator_introspect_failed", project_dir=project_dir, stderr=err[:500])
    return {
        "params": [], "mutex_required": [], "env_vars": env_vars,
        "error": err[:500] or "no schema emitted",
    }
