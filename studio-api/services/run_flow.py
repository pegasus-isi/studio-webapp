"""Run-flow orchestration — the single "Run" action.

Phase 1 of docs/UX-NON-SPECIALIST-DESIGN.md (#241). Studio's project page has
historically exposed Pegasus's four phases as four sibling buttons — Generate,
Build Image, Plan, Run — with no ordering and no gating. That is a faithful
rendering of Pegasus's model and an unusable one for a domain scientist, who
cannot know that Plan must precede Run or what either word means. This module
collapses them into one flow the user starts once.

## Why the flow lives on the server

The cheap implementation is to have the browser call the four existing
endpoints in sequence. It breaks in ways that land hardest on the user this
redesign is for:

- Closing the tab mid-flow strands a half-prepared project. There is no record
  of how far it got, so the next visit shows an inconsistent page and the only
  recovery is understanding the Pegasus phases — the exact knowledge we are
  trying not to require.
- Generate/plan/submit are request/response; build is an SSE stream. Two
  progress channels cannot be rendered as one honest progress indicator.
- A flow that only exists inside a request cannot be reattached to.

So a flow is a background task with a buffered event log. Subscribers replay
from the beginning and then follow live, which makes "reopen the page and see
what happened" fall out for free rather than needing its own persistence
story.

## Scope of the state

In-memory, per container. Studio runs one container per user (there is no
multi-user state here), and a flow is minutes long, so surviving a container
restart is not worth a schema: a restart kills the subprocesses the flow is
narrating anyway, and the run directory on disk remains the durable record.
What a restart must *not* do is leave a project looking mid-flow forever, which
is why nothing about flow state is persisted.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Literal

import structlog

from services import remote_exec, submit_host

log = structlog.get_logger()

StageKey = Literal["generate", "build", "plan", "submit"]
StageStatus = Literal["pending", "running", "done", "skipped", "failed"]
FlowState = Literal["running", "succeeded", "failed"]

# Stage labels are deliberately in the user's terms, not Pegasus's — this is
# the whole point of the phase. "Plan" becomes "Checking the plan", "submit"
# becomes "Sending it to run". The Pegasus verb survives only in `key`, which
# is never displayed.
STAGE_LABELS: dict[str, str] = {
    "generate": "Preparing your analysis",
    "build": "Checking the software environment",
    "plan": "Checking the plan",
    "submit": "Sending it to run",
}

STAGE_ORDER: tuple[StageKey, ...] = ("generate", "build", "plan", "submit")

# How long a finished flow stays queryable. Long enough that a user who wanders
# off and comes back still sees the outcome; short enough that a long-lived
# container doesn't accumulate flows forever.
_RETAIN_SECONDS = 3600.0


@dataclass
class Stage:
    key: str
    label: str
    status: StageStatus = "pending"
    # One plain-language line about what happened, shown under the label.
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class Failure:
    """A stage failure in the three parts a non-specialist needs.

    `cause` says what went wrong in their terms, `action` says what to do
    about it, and `technical` is the raw log — collapsed in the UI, never
    discarded. A cause without an action is half an answer, which is the
    failure mode of the current raw-stdout panel.
    """

    stage: str
    cause: str
    action: str
    technical: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "cause": self.cause,
            "action": self.action,
            "technical": self.technical,
        }


# ── failure classification ────────────────────────────────────────────────
#
# Pattern → (cause, action), per stage. This is the structured form of the
# frontend's `outputHint()` heuristic, moved server-side and split per stage.
# Splitting matters: `outputHint()` runs one rule set over every log, which is
# why a Docker build log trips its "exit"/"fail" patterns and tells the user to
# "Run Analyzer" — advice that makes no sense for a build. A rule can only
# match the stage it was written for here.
#
# Ordered; first match wins, so put specific patterns before general ones.

_RULES: dict[str, list[tuple[re.Pattern[str], str, str]]] = {
    "generate": [
        (
            re.compile(r"ModuleNotFoundError: No module named ['\"]([\w.]+)", re.M),
            "Your workflow script needs a Python package that isn't installed here: {0}.",
            "Install it in a terminal with `pip install {0}`, then run again. If the "
            "package is only needed inside your workflow's steps, add it to the "
            "software environment instead.",
        ),
        (
            re.compile(r"^\s*SyntaxError: (.+)$", re.M),
            "There's a typo in your workflow script that stops Python reading it: {0}.",
            "Open the script and fix the line shown in the details below. Ask the "
            "assistant to look at it if the message isn't clear.",
        ),
        (
            re.compile(
                r"error: the following arguments are required: (.+)$", re.M
            ),
            "Your workflow needs values you haven't filled in yet: {0}.",
            "Fill in the required fields in the form above and run again.",
        ),
        (
            re.compile(r"FileNotFoundError.*?['\"]([^'\"]+)['\"]", re.S),
            "Your workflow script looked for a file that isn't there: {0}.",
            "Check the file path in the form above. If the file is on your own "
            "computer, add it to your workspace first.",
        ),
    ],
    "build": [
        (
            # Documented in knowledge/overlay/STUDIO_ENV.md: an older base image
            # gets this host's libfakeroot and dies before %post runs, so nothing
            # in the definition is at fault and nothing in the log points at the
            # base image. Worth a rule precisely because the log misleads.
            re.compile(r"GLIBC_[\d.]+' not found", re.M),
            "The software environment is built on a base image older than this "
            "system, which fails before any of your setup commands run.",
            "Edit the definition file to use a newer base (for example "
            "`debian:trixie`) and build again. Nothing in your own setup steps "
            "is wrong.",
        ),
        (
            re.compile(r"No space left on device", re.M),
            "The workspace ran out of disk space while building the software "
            "environment.",
            "Delete old runs or large files from your workspace, then run again.",
        ),
        (
            re.compile(r"No Apptainer found", re.M),
            "The tool that builds software environments isn't available in this "
            "workspace.",
            "This needs a fix from whoever runs Studio — the container image is "
            "missing Apptainer. Report it rather than retrying.",
        ),
        (
            re.compile(r"while running %post section: exit status (\d+)", re.M),
            "One of the setup commands in your software environment failed.",
            "Look at the last few lines of the details below for the command that "
            "failed, fix it in the definition file, and build again.",
        ),
    ],
    "plan": [
        (
            re.compile(r"Illegal key .* for element workflow", re.M),
            "The planner was handed a catalog file instead of your workflow.",
            "Run again — the preparation step rewrites the workflow file and "
            "usually clears this. If it persists, ask the assistant to check "
            "which file your generator writes.",
        ),
        (
            re.compile(r"[Nn]o workflow YAML file found", re.M),
            "There's no workflow file to run yet.",
            "The preparation step should have created one. Check the details "
            "below for why it didn't, then run again.",
        ),
        (
            re.compile(r"(?:command not found|not found).*pegasus-plan", re.M),
            "Pegasus isn't available in this workspace.",
            "This needs a fix from whoever runs Studio. Report it rather than "
            "retrying.",
        ),
    ],
    "submit": [
        (
            re.compile(r"already running", re.I),
            "This analysis is already running, or a previous run left a lock "
            "behind after finishing.",
            "If it's still going, watch it below instead of starting another. If "
            "it has finished, run again to start a fresh one.",
        ),
    ],
}

# Last-resort cause/action per stage, when nothing matched. Vague, but it still
# names the stage and offers a next step, which beats a wall of stdout.
_FALLBACK: dict[str, tuple[str, str]] = {
    "generate": (
        "Preparing your analysis failed.",
        "The details below have the error. Ask the assistant to explain it if "
        "it isn't clear.",
    ),
    "build": (
        "Building the software environment failed.",
        "The last lines of the details below usually say which setup command "
        "failed. Ask the assistant if it isn't clear.",
    ),
    "plan": (
        "Checking the plan failed.",
        "The details below have the error. Ask the assistant to explain it if "
        "it isn't clear.",
    ),
    "submit": (
        "Sending the analysis to run failed.",
        "The details below have the error. Ask the assistant to explain it if "
        "it isn't clear.",
    ),
}


def classify(stage: str, output: str) -> tuple[str, str]:
    """(cause, action) in plain language for a failed stage.

    Never raises and never returns empty strings: a classification bug must
    degrade to the generic message, not to a blank failure card.
    """
    text = output or ""
    for pattern, cause, action in _RULES.get(stage, []):
        match = pattern.search(text)
        if not match:
            continue
        try:
            groups = [g.strip() if isinstance(g, str) else g for g in match.groups()]
            return cause.format(*groups), action.format(*groups)
        except (IndexError, KeyError):  # malformed template — use it verbatim
            return cause, action
    return _FALLBACK.get(stage, ("Something went wrong.", "Check the details below."))


def image_is_stale(definition: str, sif: str) -> bool:
    """True when the software environment needs (re)building.

    Rebuilding an unchanged environment is the slowest avoidable step in the
    flow — minutes, every run — so the default Run path skips it. Stale means
    the image is missing, empty, or older than the definition it was built
    from. Any error reading either path returns True: building unnecessarily
    costs time, while wrongly skipping produces a confusing downstream failure
    against a stale image.
    """
    try:
        if not os.path.isfile(sif) or os.path.getsize(sif) == 0:
            return True
        if not os.path.isfile(definition):
            return False  # nothing to compare against; existing image stands
        return os.path.getmtime(definition) > os.path.getmtime(sif)
    except OSError:
        return True


@dataclass
class RunFlow:
    """One in-flight (or recently finished) Run, and everything said about it."""

    flow_id: str
    project_id: str
    stages: list[Stage]
    state: FlowState = "running"
    failure: Failure | None = None
    run_dir: str = ""
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None

    # Every event ever emitted, so a late subscriber can replay the whole flow.
    # Bounded only by flow length (minutes) — build log lines dominate and are
    # capped by the builder's own output.
    _events: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _subscribers: list[asyncio.Queue] = field(default_factory=list, repr=False)
    _task: asyncio.Task | None = field(default=None, repr=False)

    # ── event plumbing ────────────────────────────────────────────────────

    def _emit(self, event: dict[str, Any]) -> None:
        """Append to the replay log and fan out to live subscribers.

        Synchronous on purpose: asyncio is single-threaded, so appending and
        fanning out without an await between them means a subscriber can never
        observe an event in the queue that is missing from the buffer, or the
        reverse.
        """
        event = {"seq": len(self._events), **event}
        self._events.append(event)
        for queue in self._subscribers:
            queue.put_nowait(event)

    def snapshot(self) -> dict[str, Any]:
        """The flow as plain JSON, for a reattach that doesn't want a stream."""
        return {
            "flow_id": self.flow_id,
            "project_id": self.project_id,
            "state": self.state,
            "stages": [s.as_dict() for s in self.stages],
            "failure": self.failure.as_dict() if self.failure else None,
            "run_dir": self.run_dir,
            "active": self.state == "running",
        }

    async def subscribe(self) -> AsyncGenerator[dict[str, Any], None]:
        """Replay everything so far, then follow live until the flow ends.

        A client that connects late — or reconnects after navigating away —
        sees the identical event sequence a client present from the start saw.
        That is what makes the flow reattachable without persisting anything.
        """
        queue: asyncio.Queue = asyncio.Queue()
        # Register before snapshotting the backlog (no await between the two),
        # so an event emitted in the gap lands in the queue rather than being
        # lost between replay and live.
        self._subscribers.append(queue)
        backlog = list(self._events)
        replayed = len(backlog)

        try:
            for event in backlog:
                yield event
            if self.state != "running":
                return
            while True:
                event = await queue.get()
                # Skip anything the backlog already covered.
                if event.get("seq", 0) < replayed:
                    continue
                yield event
                if event.get("type") == "flow":
                    return  # terminal event
        finally:
            try:
                self._subscribers.remove(queue)
            except ValueError:
                pass

    # ── stage transitions ─────────────────────────────────────────────────

    def _stage(self, key: str) -> Stage:
        return next(s for s in self.stages if s.key == key)

    def _begin(self, key: str) -> None:
        stage = self._stage(key)
        stage.status = "running"
        stage.detail = ""
        self._emit({"type": "stage", "stage": stage.as_dict()})

    def _finish(self, key: str, status: StageStatus, detail: str = "") -> None:
        stage = self._stage(key)
        stage.status = status
        stage.detail = detail
        self._emit({"type": "stage", "stage": stage.as_dict()})

    def _line(self, key: str, text: str) -> None:
        """A raw log line from a stage, for the technical-details pane."""
        self._emit({"type": "line", "stage": key, "line": text})

    def _fail(self, key: str, output: str) -> None:
        cause, action = classify(key, output)
        self.failure = Failure(
            stage=key, cause=cause, action=action, technical=output
        )
        self._finish(key, "failed", cause)
        # Stages after the failed one will never run; say so rather than
        # leaving them spinning in the UI.
        hit = False
        for stage in self.stages:
            if stage.key == key:
                hit = True
                continue
            if hit and stage.status == "pending":
                stage.status = "skipped"
                stage.detail = "Not reached"
                self._emit({"type": "stage", "stage": stage.as_dict()})
        self.state = "failed"
        log.info(
            "run_flow_failed", flow_id=self.flow_id,
            project_id=self.project_id, stage=key, cause=cause,
        )

    def _succeed(self) -> None:
        self.state = "succeeded"
        log.info(
            "run_flow_succeeded",
            flow_id=self.flow_id, project_id=self.project_id, run_dir=self.run_dir,
        )

    def _terminal(self) -> None:
        self.finished_at = time.monotonic()
        self._emit({
            "type": "flow",
            "state": self.state,
            "run_dir": self.run_dir,
            "failure": self.failure.as_dict() if self.failure else None,
        })


class RunFlowManager:
    """Owns the flows, one at a time per project."""

    def __init__(self) -> None:
        self._flows: dict[str, RunFlow] = {}

    def get(self, project_id: str) -> RunFlow | None:
        flow = self._flows.get(project_id)
        if flow is None:
            return None
        # Expire a long-finished flow so a stale outcome doesn't masquerade as
        # the current state of the project.
        if (
            flow.state != "running"
            and flow.finished_at is not None
            and time.monotonic() - flow.finished_at > _RETAIN_SECONDS
        ):
            self._flows.pop(project_id, None)
            return None
        return flow

    def active(self, project_id: str) -> RunFlow | None:
        flow = self.get(project_id)
        return flow if flow and flow.state == "running" else None

    def start(
        self,
        *,
        project_id: str,
        project_dir: str,
        generator_file: str | None,
        has_container_def: bool,
        generator_args: list[str],
        site: str,
        output_site: str,
        force_build: bool = False,
        plan_properties: dict[str, str] | None = None,
        placement: str | None = None,
    ) -> RunFlow:
        """Begin a flow as a background task and return it immediately.

        Raises RuntimeError if one is already running for this project —
        letting two flows plan into the same directory concurrently produces a
        corrupted run dir and an error message that explains nothing.
        """
        if self.active(project_id):
            raise RuntimeError("A run is already in progress for this workflow")

        stages = [
            Stage(key=key, label=STAGE_LABELS[key])
            for key in STAGE_ORDER
            # A project with no generator is already-generated; a project with
            # no definition has no environment to build. Omit rather than show
            # a permanently skipped step.
            if not (key == "generate" and not generator_file)
            and not (key == "build" and not has_container_def)
        ]
        flow = RunFlow(
            flow_id=uuid.uuid4().hex[:12], project_id=project_id, stages=stages
        )
        self._flows[project_id] = flow
        flow._task = asyncio.create_task(
            _execute(
                flow,
                project_dir=project_dir,
                generator_file=generator_file,
                generator_args=generator_args,
                site=site,
                output_site=output_site,
                force_build=force_build,
                plan_properties=plan_properties or {},
                placement=placement,
            )
        )
        log.info(
            "run_flow_started",
            flow_id=flow.flow_id, project_id=project_id, site=site,
        )
        return flow


manager = RunFlowManager()


# ── execution ─────────────────────────────────────────────────────────────


def _apply_placement(
    flow: RunFlow, project_dir: str, site: str, placement: str, *, after_generate: bool = False
) -> None:
    """Record this run's placement in the workflow, saying so in the log.

    Never fails the run: a workflow that sets its own requirements keeps
    them, and that is reported rather than overridden.
    """
    from services import run_placement as rp

    try:
        before = rp.project_placement(project_dir, site)
        state = rp.set_project_placement(project_dir, site, placement)
    except ValueError as e:
        if not after_generate:
            flow._line("plan", str(e))
        return
    except OSError as e:
        flow._line("plan", f"Could not record where this workflow runs: {e}")
        return
    if after_generate and before["placement"] != state["placement"]:
        flow._line(
            "plan",
            "The generator rewrote pegasus.properties; Studio put back "
            "\u201cOnly my annex\u201d for this workflow.",
        )
    elif not after_generate:
        where = "only your annex" if state["placement"] == rp.ANNEX else "everywhere except your annex"
        flow._line("plan", f"Steps run on {where} (this workflow's pegasus.properties).")


async def _execute(
    flow: RunFlow,
    *,
    project_dir: str,
    generator_file: str | None,
    generator_args: list[str],
    site: str,
    output_site: str,
    force_build: bool,
    plan_properties: dict[str, str] | None = None,
    placement: str | None = None,
) -> None:
    """Run the stages in order, stopping at the first failure.

    Deliberately broad exception handling: this runs detached from any request,
    so an unhandled exception would leave the flow "running" forever with no
    client able to learn why. Every exit path reaches `_terminal()`.
    """
    try:
        # Where the planner runs decides whether a Pegasus mismatch matters,
        # so resolve it once, up front, and reuse it for the plan stage.
        try:
            plan_mode, plan_mode_source = await _resolve_plan_mode()
        except Exception as e:  # noqa: BLE001 — detection must never block a run
            log.warning("plan_mode_detect_failed", error=str(e))
            plan_mode, plan_mode_source = "local", "fallback"

        # Preflight before anything expensive. A mismatch between this
        # workspace and the submit host fails every workflow on its first
        # node, and the error names a missing file rather than the cause — so
        # catch it here rather than after a ten-minute image build.
        #
        # It only blocks when planning happens *here*. Planning on the submit
        # host makes the mismatch irrelevant: the paths baked into the submit
        # files are then the submit host's own. Blocking anyway would take a
        # difference that no longer matters and turn it into an outage —
        # refusing every run on a deployment where runs work.
        compat = await submit_host.check()
        if compat.blocks_run and plan_mode == "local":
            # Whose decision put us on the local path changes the advice
            # completely. If the user turned remote planning off to try local,
            # the fix is one switch in their own Settings — telling them to
            # find an administrator would be wrong and would strand them in a
            # state they chose and can leave.
            if plan_mode_source == "setting":
                action = (
                    "You have planning set to run in this workspace "
                    "(Settings → Pegasus Options → Where planning runs). This "
                    "workspace and the submit host disagree about Pegasus, so "
                    "that cannot work here — set it back to Automatic to plan "
                    "on the submit host."
                )
            else:
                action = (
                    "This needs an administrator: the submit host and this "
                    "workspace have to agree on Pegasus. Nothing you change "
                    "here will fix it."
                )
            flow.failure = Failure(
                stage="plan",
                cause=compat.detail,
                action=action,
                technical=(
                    f"workspace: {compat.local_version or '?'} at "
                    f"{compat.local_share_dir or '?'} · "
                    f"submit host {compat.schedd or '?'}: "
                    f"{compat.remote_version or '?'} at "
                    f"{compat.remote_share_dir or '?'}"
                ),
            )
            flow.state = "failed"
            for st in flow.stages:
                if st.status == "pending":
                    st.status = "skipped"
            log.warning(
                "run_flow_blocked_submit_host_mismatch",
                flow_id=flow.flow_id,
                plan_mode_source=plan_mode_source,
                **compat.as_dict(),
            )
            return
        if compat.status != "ok":
            # Worth recording, not worth stopping for.
            log.info(
                "run_flow_submit_host_uncertain",
                plan_mode=plan_mode,
                **compat.as_dict(),
            )
        if plan_mode == "remote":
            warning = submit_host.series_warning(compat)
            if warning:
                flow._line("plan", warning)
        if compat.blocks_run and plan_mode == "remote":
            # Say it in the run's own output. Planning on the submit host
            # handles a different *share* dir (the paths baked into auxiliary
            # jobs are then its own), which is the case this was written for:
            # same package, different Python minor (#334). It does not handle
            # a different *bin* dir. Submission still happens here, and
            # condor_submit refuses an executable that does not exist locally,
            # so a plan naming the submit host's pegasus-dagman somewhere this
            # workspace does not have it fails at submit (#490).
            flow._line(
                "plan",
                "This workspace and the submit host have Pegasus installed in "
                "different places. Planning on the submit host covers most of "
                "that, but the run is still submitted from here: if submission "
                "then fails with \"Can't access executable file "
                ".../pegasus-dagman\", the submit host's Pegasus is not at the "
                "same path in this workspace, and an administrator needs to "
                "install it to match (or make that path visible here).",
            )

        # The site is checked before anything runs, and handed to a
        # gromacs-template generator's -e so its transformations are bound to
        # the site the plan will use (#466).
        from services import execution_sites as xs

        loop = asyncio.get_event_loop()
        problem = await loop.run_in_executor(
            None, lambda: xs.site_problem(project_dir, site)
        )
        if problem:
            flow.failure = Failure(
                stage="plan",
                cause=problem,
                action="Choose another place under \u201cWhere it runs\u201d.",
                technical=f"pegasus-plan -s {site}: no catalog defines it",
            )
            flow.state = "failed"
            for st in flow.stages:
                if st.status == "pending":
                    st.status = "skipped"
            log.warning("run_flow_blocked_undefined_site", flow_id=flow.flow_id, site=site)
            return
        if generator_file:
            from services.generator_introspect import introspect_generator

            params = (await introspect_generator(project_dir)).get("params") or []
            generator_args = xs.sync_site_arg(generator_args, params, site)

        # Which machines (services/run_placement.py): written into the
        # project's pegasus.properties so a terminal pegasus-plan agrees with
        # this run, and checked again after Generate, which may rewrite it.
        if placement:
            _apply_placement(flow, project_dir, site, placement)

        if any(s.key == "generate" for s in flow.stages):
            if not await _run_generate(
                flow, project_dir=project_dir,
                generator_file=generator_file or "", args=generator_args,
            ):
                return
            if placement:
                _apply_placement(flow, project_dir, site, placement, after_generate=True)

        if any(s.key == "build" for s in flow.stages):
            if not await _run_build(
                flow, project_dir=project_dir, force=force_build
            ):
                return

        # After generate (generators write pegasus.properties and the
        # catalogs), before the plan: a workflow that needs the workers to see
        # this workspace fails every job on a pool that has no shared
        # filesystem, with an error naming neither cause (#520).
        from services import shared_fs

        fs_problem = await loop.run_in_executor(
            None,
            lambda: shared_fs.problem(
                project_dir, site, plan_properties=plan_properties
            ),
        )
        if fs_problem:
            flow._begin("plan")
            flow._fail("plan", fs_problem)
            # _fail classified the text; this cause and action are exact.
            flow.failure = Failure(
                stage="plan",
                cause=fs_problem,
                action=(
                    "Pick a cluster under \u201cWhere it runs\u201d, or ask the "
                    "assistant to make the workflow stage its inputs."
                ),
                technical=f"pegasus-plan -s {site}: not run, shared-filesystem check",
            )
            log.warning("run_flow_blocked_shared_fs", flow_id=flow.flow_id, site=site)
            return

        if not await _run_plan(
            flow,
            project_dir=project_dir,
            site=site,
            output_site=output_site,
            plan_mode=plan_mode,
            plan_properties=plan_properties,
        ):
            return

        await _run_submit(
            flow, project_dir=project_dir, site=site, output_site=output_site
        )
    except asyncio.CancelledError:
        flow.state = "failed"
        flow.failure = Failure(
            stage="submit",
            cause="The run was stopped before it finished starting.",
            action="Run again when you're ready.",
            technical="Flow cancelled.",
        )
        raise
    except Exception as e:  # noqa: BLE001 — see docstring
        log.exception("run_flow_crashed", flow_id=flow.flow_id, error=str(e))
        current = next(
            (s.key for s in flow.stages if s.status == "running"), "submit"
        )
        flow._fail(current, f"Unexpected error: {e}")
    finally:
        if flow.state == "running":
            # Reached only if a stage returned without deciding an outcome.
            flow._fail("submit", "The run ended without reporting a result.")
        flow._terminal()


async def _capture(
    cmd: list[str], cwd: str, timeout: int = 300,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Run a command, returning (returncode, combined output).

    `env` replaces the child's environment outright when given, so callers pass
    a full environment (see services/workflow_secrets.subprocess_env) rather
    than a handful of additions.
    """
    from services.procutil import communicate_or_kill

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
        stdout, stderr = await communicate_or_kill(proc, timeout)
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        return proc.returncode or 0, out + (f"\nstderr:\n{err}" if err else "")
    except FileNotFoundError:
        return 127, f"{cmd[0]}: command not found"
    except asyncio.TimeoutError:
        return 124, f"{cmd[0]} timed out after {timeout}s"


def _emit_output(flow: RunFlow, stage: str, output: str, limit: int = 200) -> None:
    """Push a command's output into the flow as log lines (tail-limited)."""
    lines = [ln for ln in output.splitlines() if ln.strip()]
    for line in lines[-limit:]:
        flow._line(stage, line)


async def _run_generate(
    flow: RunFlow, *, project_dir: str, generator_file: str, args: list[str]
) -> bool:
    flow._begin("generate")
    # The generator is the one step that reads credentials from its own
    # environment — that is how a value gets baked into the workflow it writes
    # (`add_env`), since HTCondor gives jobs a clean environment. Settings >
    # Pegasus Options > Workflow secrets is where they come from; without this
    # a workflow needing an API key could not run in Studio at all.
    from services import workflow_secrets

    code, output = await _capture(
        ["python3", generator_file, *args],
        cwd=project_dir,
        env=await workflow_secrets.subprocess_env(),
    )
    _emit_output(flow, "generate", output)
    if code != 0:
        flow._fail("generate", output)
        return False
    flow._finish("generate", "done", "Your analysis is ready to plan.")
    return True


async def _run_build(flow: RunFlow, *, project_dir: str, force: bool) -> bool:
    from services.image_builder import find_definition

    flow._begin("build")
    found = find_definition(project_dir)
    if not found:
        flow._finish("build", "skipped", "No software environment to build.")
        return True

    definition, sif = found
    if not force and not image_is_stale(definition, sif):
        # The common case on a re-run. Saying "already up to date" rather than
        # silently skipping is what stops a user wondering if it was missed.
        flow._finish("build", "skipped", "Already up to date.")
        return True

    from main import db
    import secretbox
    from services.image_builder import stream_build

    registry = await db.get_setting("container_registry", "") or ""
    username = await db.get_setting("container_registry_username", "") or ""
    password = (
        secretbox.decrypt(await db.get_setting("container_registry_password", ""))
        or ""
    )

    collected: list[str] = []
    failed_error: str | None = None
    async for chunk in stream_build(
        project_id=flow.project_id,
        project_dir=project_dir,
        registry=registry,
        username=username,
        password=password,
        tag="latest",
        push=False,
    ):
        event = _parse_sse(chunk)
        if event is None:
            continue
        if line := event.get("line"):
            collected.append(str(line))
            flow._line("build", str(line))
        if phase := event.get("phase"):
            flow._line("build", f"── {phase} ──")
        if event.get("status") == "error":
            failed_error = str(event.get("error") or "Build failed")
            collected.append(failed_error)

    if failed_error is not None:
        flow._fail("build", "\n".join(collected))
        return False
    flow._finish("build", "done", "Software environment is ready.")
    return True


def _parse_sse(chunk: str) -> dict[str, Any] | None:
    """Pull the JSON payload out of one `data: {...}` SSE frame."""
    import json

    for line in chunk.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            parsed = json.loads(payload)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


#: What the user may set for `plan_mode`. "auto" is the shipped behaviour.
PLAN_MODE_CHOICES = ("auto", "remote", "local")
#: Where the answer came from, so a message can name it. A block on a mismatch
#: reads very differently depending on whether the user chose local planning or
#: the deployment did.
PlanModeSource = Literal["setting", "env", "detected", "no-condor", "fallback"]


async def _plan_mode_setting() -> str:
    """The user's stored preference, or "auto".

    Wrapped because this is consulted on the run path: a settings read that
    fails must fall through to detection, not stop a run.
    """
    try:
        from main import db

        value = await db.get_setting("plan_mode", "auto")
    except Exception as e:  # noqa: BLE001 — a settings read must never block a run
        log.warning("plan_mode_setting_read_failed", error=str(e))
        return "auto"
    if isinstance(value, str) and value.strip().lower() in PLAN_MODE_CHOICES:
        return value.strip().lower()
    return "auto"


async def _resolve_plan_mode() -> tuple[str, PlanModeSource]:
    """("remote"|"local", why) — where pegasus-plan should run.

    Planning has to happen where the workflow will be submitted. The planner
    writes its own absolute paths into the submit files, and Pegasus's local
    universe then executes those on the schedd host, so planning in the pod
    against a remote schedd produces a DAG that references files only the pod
    has (#334).

    `auto` is deliberate rather than defaulting to remote: a native or Open
    OnDemand deployment runs the schedd on the same machine, which is the
    arrangement Pegasus expects, and remoting a job to yourself would add a
    queue round-trip for nothing.

    Precedence is user setting, then deployment env var, then detection. The
    user's own choice comes first on purpose: this is a single-user workspace,
    and the reason to reach for the switch is to find out whether local
    planning works here — an answer the deployment default cannot give you.
    Since the image moved to Debian trixie the pod and the AP run the same
    Pegasus tree, so local planning is expected to work again; that is exactly
    the thing worth being able to try without an operator and a pod restart.
    """
    chosen = await _plan_mode_setting()
    if chosen in ("local", "remote"):
        return chosen, "setting"

    setting = os.environ.get("STUDIO_PLAN_ON_SUBMIT_HOST", "auto").strip().lower()
    if setting in ("never", "0", "false", "local"):
        return "local", "env"
    if setting in ("always", "1", "true", "remote"):
        return "remote", "env"
    if not remote_exec.available():
        return "local", "no-condor"
    remote = await submit_host.is_remote()
    # None means undetermined; keep today's behaviour rather than guessing.
    return ("remote" if remote else "local"), "detected"


async def _plan_mode() -> str:
    """`_resolve_plan_mode` without the provenance, for callers that don't need it."""
    mode, _ = await _resolve_plan_mode()
    return mode


async def _plan_capture(
    flow: RunFlow, argv: list[str], *, project_dir: str, mode: str | None = None
) -> tuple[int, str]:
    """Run the planner, on the submit host when that is where it belongs.

    `mode` is resolved once by the caller, because the preflight needs the same
    answer and asking twice could get two — the detection is a network call.
    """
    if mode is None:
        try:
            mode = await _plan_mode()
        except Exception as e:  # noqa: BLE001 — detection must never block a run
            log.warning("plan_mode_detect_failed", error=str(e))
            mode = "local"

    if mode == "local":
        return await _capture(argv, cwd=project_dir)

    flow._line(
        "plan",
        "Planning on the submit host, so the plan refers to its files and not "
        "this workspace's.",
    )
    scratch = os.path.join(project_dir, ".studio", "remote")
    try:
        return await remote_exec.run_on_submit_host(
            argv, cwd=project_dir, scratch_dir=scratch
        )
    except remote_exec.RemoteExecUnavailable as e:
        # Falling back is right: a broken mechanism should not stop a run that
        # might well succeed, and on a matched pair of hosts it will. Say so
        # in the output, because if it then fails on its first node this line
        # is the explanation.
        log.warning("remote_plan_unavailable", error=str(e))
        flow._line(
            "plan",
            f"Couldn't plan on the submit host ({e}); planning here instead. "
            "If the run fails on its first step, this is why.",
        )
        return await _capture(argv, cwd=project_dir)


async def _run_plan(
    flow: RunFlow,
    *,
    project_dir: str,
    site: str,
    output_site: str,
    plan_mode: str | None = None,
    plan_properties: dict[str, str] | None = None,
) -> bool:
    from services.workflow_submitter import SUBMIT_BASE, _find_workflow_file

    flow._begin("plan")
    wf_file = _find_workflow_file(project_dir)
    if not wf_file:
        flow._fail("plan", "No workflow YAML file found in the project.")
        return False

    # --dir is relative to the project directory, which is the cwd both for a
    # local plan and for one wrapped onto the submit host (remote_exec cd's
    # there first). See SUBMIT_BASE for why the GUI and the assistant share it.
    argv = ["pegasus-plan", "--dir", SUBMIT_BASE, "-s", site, "-o", output_site]
    # A project with no catalog for the output site (the gromacs template)
    # would otherwise stage results to Pegasus's default ./wf-output.
    from services.execution_sites import plan_output_dir

    output_dir = plan_output_dir(project_dir, output_site)
    if output_dir:
        argv += ["--output-dir", output_dir]
    if plan_properties:
        from services.run_placement import plan_args

        argv += plan_args(plan_properties)
        flow._line("plan", "This run: " + ", ".join(f"{k.rsplit('.', 1)[-1]} = {v}" for k, v in sorted(plan_properties.items())))
    argv.append(wf_file)
    code, output = await _plan_capture(
        flow, argv, project_dir=project_dir, mode=plan_mode
    )
    _emit_output(flow, "plan", output)
    if code != 0:
        flow._fail("plan", output)
        return False

    from services.workflow_submitter import _extract_run_dir, _newest_run_dir

    flow.run_dir = _extract_run_dir(output) or _newest_run_dir(project_dir) or ""
    flow._finish("plan", "done", "The plan checks out.")
    return True


async def _run_submit(
    flow: RunFlow, *, project_dir: str, site: str, output_site: str
) -> bool:
    from services.workflow_submitter import run_planned_workflow

    flow._begin("submit")
    result = await run_planned_workflow(
        project_dir,
        run_dir=flow.run_dir or None,
        site=site,
        output_site=output_site,
    )
    output = str(result.get("output") or "")
    _emit_output(flow, "submit", output)

    if result.get("status") != "submitted":
        flow._fail("submit", output or "pegasus-run did not report success.")
        return False

    flow.run_dir = str(result.get("run_dir") or flow.run_dir)
    flow._finish("submit", "done", "It's running.")
    flow._succeed()

    # Same submit-time notification the individual Run action raises, so a
    # unified run is announced identically to a manual one.
    try:
        from routers.workflows import _notify_run_started

        await _notify_run_started(result)
    except Exception as e:  # noqa: BLE001 — never fail a run over a notification
        log.warning("run_flow_notify_failed", flow_id=flow.flow_id, error=str(e))
    return True
