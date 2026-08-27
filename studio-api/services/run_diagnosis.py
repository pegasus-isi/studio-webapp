"""What went wrong with a running workflow, in the user's terms.

Phase 5 of docs/UX-NON-SPECIALIST-DESIGN.md (#241). Phase 1 gave the *pre-run*
stages (generate, build, plan, submit) plain-language failure cards. This does
the same for the workflow itself once it is running, where the raw material is
different: job stderr, exit codes, and HTCondor hold reasons rather than the
stdout of a command Studio ran.

## Why not just show the analyzer output

`pegasus-analyzer` is excellent and completely opaque to someone who has never
met Pegasus. It answers "which job, which exit code, which log file"; a
scientist is asking "what do I do now". Both are worth having, so the analyzer
text is preserved as the technical detail and this module supplies the sentence
that goes above it.

## The rule that shapes every entry

**A cause is never returned without an action.** Naming a problem and stopping
is the failure mode of the current raw-stderr panel: it is information the user
cannot act on, presented as though it were help. Where nothing self-service
exists, the action says so explicitly and points at a human — that is still an
action, and it is honest.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

log = structlog.get_logger()

# Pattern → (cause, action). Ordered; first match wins, so specific before
# general. Formatted with the match's groups, as in run_flow.classify().
#
# These are drawn from what actually kills jobs in this environment: container
# problems, missing inputs, resource limits. Each is written for someone who
# does not know what PegasusLite, a transformation catalog, or a slot is.
_JOB_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (
        # 137 = 128 + SIGKILL. Nearly always the OOM killer on a worker node,
        # and the least self-evident exit code a user will ever be shown.
        re.compile(r"\b(?:exit(?:code)?|status)[\s:=]*137\b|Killed\b", re.I),
        "This step ran out of memory and was stopped.",
        "Ask for more memory for this step in your workflow, or run it on a "
        "smaller piece of the data to check that is the only problem.",
    ),
    (
        re.compile(r"(?:Out of memory|Cannot allocate memory|MemoryError)", re.I),
        "This step ran out of memory.",
        "Ask for more memory for this step in your workflow, or process the "
        "data in smaller pieces.",
    ),
    (
        re.compile(r"No space left on device", re.I),
        "The machine running this step ran out of disk space.",
        "Delete old runs from your workspace to free space, then run again. If "
        "the step itself writes very large files, it may need a bigger "
        "scratch allocation — worth asking whoever runs the cluster.",
    ),
    (
        re.compile(
            r"(?:FATAL|ERROR).*?(?:container|apptainer|singularity)", re.I
        ),
        "The software environment for this step couldn't be started.",
        "Rebuild the software environment and run again. If it keeps failing, "
        "the definition file is the place to look — ask the assistant to "
        "check it.",
    ),
    (
        re.compile(
            r"(?:No such file or directory|cannot open|not found):?\s*'?([^\s'\"]+)'?",
            re.I,
        ),
        "This step couldn't find a file it needed: {0}.",
        "Check that the input exists and that the path in your run settings is "
        "right. If the file is on your own computer, add it to your workspace "
        "first.",
    ),
    (
        re.compile(r"Permission denied:?\s*'?([^\s'\"]+)'?", re.I),
        "This step wasn't allowed to read or write {0}.",
        "Check the file is in your workspace. Files outside it aren't readable "
        "from the machines that run your steps.",
    ),
    (
        re.compile(r"ModuleNotFoundError: No module named ['\"]([\w.]+)", re.M),
        "A Python package this step needs isn't in the software environment: "
        "{0}.",
        "Add {0} to the software environment definition, rebuild it, and run "
        "again. Installing it in a terminal won't help — the steps run "
        "somewhere else.",
    ),
    (
        re.compile(r"command not found|executable file not found", re.I),
        "A program this step runs isn't in the software environment.",
        "Add it to the software environment definition and rebuild. The steps "
        "don't run in this workspace, so installing it here won't reach them.",
    ),
    (
        re.compile(r"(?:Connection|Network is unreachable|Temporary failure "
                   r"in name resolution|timed out)", re.I),
        "This step couldn't reach something over the network.",
        "If it downloads data, the source may be temporarily unavailable — "
        "running again often works. If it keeps failing, the machines running "
        "your steps may not have internet access.",
    ),
]

# HTCondor hold reasons. A held job is the single most misread state in the
# product: "held" sounds intentional, and the queue's own text is written for
# an administrator.
_HOLD_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (
        # HTCondor phrases this several ways depending on version and
        # whether it was the startd or the schedd that noticed:
        # "Job has gone over memory limit of N megabytes",
        # "memory usage exceeded request_memory".
        re.compile(r"(?:over|exceeded)\s+memory|memory\s+(?:usage\s+)?(?:limit|exceeded)", re.I),
        "This step asked for less memory than it used, so the cluster stopped "
        "it.",
        "Increase the memory requested for this step in your workflow, then "
        "run again.",
    ),
    (
        re.compile(r"(?:over|exceeded)\s+disk|disk\s+(?:usage\s+)?(?:limit|exceeded)", re.I),
        "This step used more disk than it asked for, so the cluster stopped it.",
        "Increase the disk requested for this step in your workflow, then run "
        "again.",
    ),
    (
        re.compile(r"wall.?clock|time limit|exceeded.*runtime", re.I),
        "This step ran longer than the time it was allowed.",
        "Increase the time requested for this step, or split the work into "
        "smaller pieces.",
    ),
    (
        re.compile(r"(?:Failed to (?:execute|open)|Error from slot)", re.I),
        "The cluster couldn't start this step on the machine it picked.",
        "Run again — this is often a problem with one machine rather than with "
        "your workflow. If it repeats, ask whoever runs the cluster to look.",
    ),
    (
        re.compile(r"credential|proxy|authenticat", re.I),
        "The cluster couldn't verify your credentials for this step.",
        "This needs a fix from whoever runs Studio — your access token has "
        "likely expired. Report it rather than retrying.",
    ),
]

_JOB_FALLBACK = (
    "This step failed.",
    "The details below have the error it produced. Use “Explain this failure” "
    "to have the assistant read it for you.",
)

_HOLD_FALLBACK = (
    "The cluster stopped this step and is waiting rather than retrying.",
    "It won't continue on its own. Use “Explain this failure” to have the "
    "assistant read the reason, or ask whoever runs the cluster.",
)


def _apply(
    rules: list[tuple[re.Pattern[str], str, str]],
    text: str,
    fallback: tuple[str, str],
) -> tuple[str, str]:
    """(cause, action) for `text`, never empty and never cause-without-action."""
    for pattern, cause, action in rules:
        match = pattern.search(text or "")
        if not match:
            continue
        try:
            groups = [
                (g.strip() if isinstance(g, str) else g) for g in match.groups()
            ]
            return cause.format(*groups), action.format(*groups)
        except (IndexError, KeyError):
            return cause, action
    return fallback


def classify_job_failure(stderr: str, exitcode: Any = None) -> tuple[str, str]:
    """Explain one failed job. `exitcode` is folded into the searched text so
    the numeric-only signals (137 for the OOM killer) can match even when the
    job wrote nothing to stderr."""
    haystack = f"{stderr or ''}\nexitcode: {exitcode}"
    return _apply(_JOB_RULES, haystack, _JOB_FALLBACK)


def classify_hold(reason: str) -> tuple[str, str]:
    """Explain why the cluster is holding a step instead of running it."""
    return _apply(_HOLD_RULES, reason or "", _HOLD_FALLBACK)


def _step_name(job: dict[str, Any]) -> str:
    """The friendliest available name for a failed job.

    Prefers the transformation (the thing the user's workflow calls the step)
    over the Pegasus job id, which carries clustering and retry decoration the
    user never chose — `epoch_epoch_1002` names nothing they wrote.
    """
    for key in ("transformation", "exec_job_id", "job_id"):
        value = job.get(key)
        if value:
            return str(value)
    return "a step"


def diagnose_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A failure card per failed job, newest information first.

    Only failed jobs produce cards: a run with three failures and forty
    successes should show three things, not forty-three.
    """
    cards: list[dict[str, Any]] = []
    for job in jobs or []:
        status = str(job.get("status") or "").lower()
        exitcode = job.get("exitcode")
        failed = status == "failed" or (
            exitcode not in (None, "", 0, "0") and status != "succeeded"
        )
        if not failed:
            continue
        stderr = str(job.get("stderr") or "")
        cause, action = classify_job_failure(stderr, exitcode)
        cards.append({
            "kind": "job",
            "step": _step_name(job),
            "cause": cause,
            "action": action,
            "exitcode": exitcode,
            # Kept, never led with.
            "technical": stderr,
        })
    return cards


def diagnose_holds(held_reasons: list[str]) -> list[dict[str, Any]]:
    """A failure card per distinct hold reason.

    Deduplicated: one bad requirement holds every job in a step, and forty
    identical cards is noise rather than forty problems.
    """
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for reason in held_reasons or []:
        text = str(reason or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cause, action = classify_hold(text)
        cards.append({
            "kind": "held",
            "step": "",
            "cause": cause,
            "action": action,
            "exitcode": None,
            "technical": text,
        })
    return cards
