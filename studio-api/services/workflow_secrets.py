"""Credentials a workflow's generator needs, settable from the GUI.

Why this exists
---------------
A generator that fetches data usually needs a credential, and the only way it
reaches the *job* is `add_env` at generation time: HTCondor runs jobs in a
clean environment, so exporting a key in a shell never reaches the job or its
container. Generators therefore read the value from their own environment:

    fetch_job.add_env(KAGGLE_KEY=os.environ["KAGGLE_KEY"])

In Studio that environment is the API server's, inside a pod the user cannot
edit. So a workflow needing a key was simply unrunnable here: `crophealth
--data-source kaggle` read `os.environ.get('KAGGLE_KEY', '')`, got an empty
string, and failed on a worker node with a Kaggle API error that named nothing
about credentials. The alternative was an operator editing the KubeSpawner
profile and restarting the pod, per key.

This module holds those values, encrypted at rest, and overlays them on the
environment of the generator when Studio runs it.

Scope and limits
----------------
- **Workspace-wide, not per project.** A Kaggle key is a credential for a
  service, not for a directory; the same user has one. Per-project storage
  would ask the same question once per workflow and get the same answer.
- **The generate step only.** That is where a generator reads the environment
  and bakes values into the workflow it writes. Planning deliberately runs with
  `getenv = false` (see services/remote_exec.py) and pushing secrets to the
  submit host would widen where they exist for no benefit.
- **Not a secret from the user.** These are the user's own credentials, in
  their own single-user workspace, and anything they can already run in the
  Terminal can read the environment of what they launch. What encryption at
  rest buys is that a copy of the DB file alone — an NFS snapshot, a support
  bundle — is not a copy of the keys (see secretbox.py, S7).
"""

from __future__ import annotations

import os
import re

import structlog

log = structlog.get_logger()

#: Environment variable names, POSIX-style. Lowercase is accepted and upcased:
#: a `kaggle_key` typed in the form would otherwise sit next to `KAGGLE_KEY`
#: in the list looking like a second, different credential.
_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")

#: Names refused outright, because setting them would change how the
#: subprocess *runs* rather than what it can authenticate to. `PATH` or
#: `PYTHONPATH` from this table would silently redirect which python and which
#: libraries a generator gets; `LD_PRELOAD` executes attacker-chosen code;
#: `HOME` moves the workspace out from under it. None of these are credentials,
#: and all of them turn a mistyped entry into a confusing, hard-to-attribute
#: breakage of every run.
_RESERVED = frozenset({
    "PATH", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "LD_PRELOAD",
    "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH",
    "HOME", "USER", "LOGNAME", "SHELL", "IFS", "BASH_ENV", "ENV",
    "PEGASUS_HOME", "PEGASUS_PYTHON", "CONDOR_CONFIG",
})

MAX_NAME_LEN = 128
#: Long enough for a PEM-ish blob pasted by mistake to be refused rather than
#: silently stored and truncated somewhere downstream.
MAX_VALUE_LEN = 8192


class InvalidSecret(ValueError):
    """The name or value cannot be used as an environment variable."""


def normalize_name(raw: str) -> str:
    """Canonical env-var name, or raise InvalidSecret explaining why not."""
    name = (raw or "").strip().upper()
    if not name:
        raise InvalidSecret("A name is required.")
    if len(name) > MAX_NAME_LEN:
        raise InvalidSecret(f"Name is longer than {MAX_NAME_LEN} characters.")
    if not _NAME_RE.match(name):
        raise InvalidSecret(
            f"{name!r} is not a usable environment variable name — use letters, "
            "digits and underscores, starting with a letter or underscore."
        )
    if name in _RESERVED:
        raise InvalidSecret(
            f"{name} controls how the workflow's own tools run rather than what "
            "they can log in to, so it cannot be set here."
        )
    return name


def validate_value(value: str) -> str:
    """The value as it will be stored, or raise InvalidSecret."""
    if value is None:
        raise InvalidSecret("A value is required.")
    if len(value) > MAX_VALUE_LEN:
        raise InvalidSecret(f"Value is longer than {MAX_VALUE_LEN} characters.")
    # A newline would be truncated or reinterpreted by whatever reads the
    # environment, so a pasted multi-line blob is refused rather than mangled.
    if "\n" in value or "\r" in value or "\0" in value:
        raise InvalidSecret(
            "Value cannot contain line breaks. Paste a single-line token; for "
            "a key file, put the file in your workspace and set the variable "
            "to its path."
        )
    return value


async def env_overlay() -> dict[str, str]:
    """Stored secrets as environment variables. Empty when none are set."""
    from main import db

    try:
        secrets = await db.get_workflow_secret_env()
    except Exception as e:  # noqa: BLE001 — a secrets read must not block a run
        log.warning("workflow_secrets_read_failed", error=str(e))
        return {}
    # A reserved name could only get here from a hand-edited DB, but injecting
    # PATH from a table is exactly the failure this is meant to avoid.
    return {
        name: value
        for name, value in secrets.items()
        if name not in _RESERVED and _NAME_RE.match(name)
    }


async def subprocess_env() -> dict[str, str]:
    """The server's environment with the stored secrets overlaid.

    Overlaid, not replaced: a generator still needs PATH, HOME and the rest to
    run at all. Secrets win over an inherited variable of the same name, since
    what the user typed in the GUI is the more deliberate of the two.
    """
    overlay = await env_overlay()
    if not overlay:
        return dict(os.environ)
    log.info("workflow_secrets_injected", names=sorted(overlay))
    return {**os.environ, **overlay}
