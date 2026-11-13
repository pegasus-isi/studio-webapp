"""Re-run a workflow from a chosen step, reusing what earlier runs produced (#680).

Why this exists
---------------
Every Run used to recompute everything. A user who changed only the plot at
the end of a workflow, and asked the assistant to apply it, watched the whole
workflow — fetches, processing, all of it — run again.

Pegasus can skip finished work (data reuse, on by default): it drops a job
whose outputs are all in the replica catalog. It just never had anything to
find. Each run records its outputs in a catalog inside its *own* submit
directory, `pegasus-plan --reuse <dir>` is the only way to point at an earlier
one, and generated workflows commonly set ``register_replica=False`` on every
output, so even that catalog is empty. Probed on Pegasus 6.0.0.dev1
(2026-11-13): `--reuse` would have reused nothing for the workflow in the
report.

How
---
Studio writes the catalog itself, from files that exist: a replica file
listing earlier outputs of every step *not* being re-run, passed to the
planner with ``--inherited-rc-files`` (lower priority than the workflow's own
catalog, so it can only add locations, never override one the user wrote).
The chosen step and everything downstream of it are left out, so Pegasus runs
them; everything upstream is pruned. Nothing in the user's workflow changes.

Which files are current is decided from the run directories themselves, not
from a record Studio keeps (so a run started from a terminal counts too). Each
run directory holds a copy of the abstract workflow and a stampede DB saying
which abstract jobs succeeded and when they were submitted. For each output,
the newest run in which its producing job succeeded "owns" it, and the file on
disk is reused only if it was written after that job started — a stage-out
that failed, or a file from an older run that a newer one should have
replaced, is not.

Only staged-out outputs can be found this way: intermediates live in the
staging site's scratch and the default cleanup deletes them, so a re-run
starting mid-workflow may need the steps that made them. The preview says so.

Pegasus's pruning, measured on 6.0.0.dev1 and mirrored by `_simulate()`:
a job is dropped when all its outputs are listed; a job whose children were
all dropped is dropped too, unless one of its own staged-out outputs is
missing (it is a deliverable, so it runs). The preview is an estimate made
before Generate; the planned DAG is the final word, and the Run flow reports
what Pegasus actually planned.
"""

from __future__ import annotations

import glob
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any

import structlog
import yaml

log = structlog.get_logger()

# A file written a little before its producer's recorded submit time is still
# its output: the submit host, the workers and the NFS server keep separate
# clocks.
_CLOCK_SKEW_S = 120

# Where Studio keeps the replica file it hands the planner. Inside the
# project's own .studio dir (already used for remote-plan scratch), so the
# path is on shared storage and visible to the submit host.
_RC_DIR = os.path.join(".studio", "reuse")
RC_NAME = "rerun.rc"

# Job types that run a whole workflow. Their outputs come from inside the
# sub-workflow, which this module does not look into.
_SUBWORKFLOW_TYPES = {"pegasusWorkflow", "condorWorkflow"}


class RerunError(ValueError):
    """The re-run can't be prepared; the message is for the user."""


@dataclass
class Job:
    id: str
    name: str
    inputs: set[str] = field(default_factory=set)
    # lfn -> staged out to the output site
    outputs: dict[str, bool] = field(default_factory=dict)
    children: set[str] = field(default_factory=set)
    parents: set[str] = field(default_factory=set)


@dataclass
class Produced:
    lfn: str
    path: str
    run_name: str
    job_id: str


@dataclass
class RerunPlan:
    from_step: str
    run: list[str]  # abstract job ids Pegasus is expected to run
    reused: list[str]  # abstract job ids expected to be skipped
    missing: list[dict[str, str]]  # inputs to re-run steps nobody kept
    entries: list[Produced]
    jobs: dict[str, Job]

    def summary(self) -> dict[str, Any]:
        return {
            "from_step": self.from_step,
            "run": _group(self.run, self.jobs),
            "reused": _group(self.reused, self.jobs),
            "missing": self.missing,
            "sources": sorted({e.run_name for e in self.entries}),
            "files": len(self.entries),
        }


# ── the abstract workflow ────────────────────────────────────────────────


def load_workflow(path: str) -> dict[str, Job]:
    """Jobs of a Pegasus 5 YAML abstract workflow, with their files and edges."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        raise RerunError(f"Couldn't read {os.path.basename(path)}: {e}") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("jobs"), list):
        raise RerunError(f"{os.path.basename(path)} has no jobs.")

    jobs: dict[str, Job] = {}
    for raw in doc["jobs"]:
        if not isinstance(raw, dict) or "id" not in raw:
            continue
        if raw.get("type") in _SUBWORKFLOW_TYPES:
            raise RerunError(
                "This workflow runs sub-workflows, and re-running from a step "
                "doesn't look inside them yet. Run it from the beginning."
            )
        job = Job(id=str(raw["id"]), name=str(raw.get("name") or raw["id"]))
        for use in raw.get("uses") or []:
            lfn = use.get("lfn")
            if not lfn:
                continue
            if use.get("type") == "input":
                job.inputs.add(lfn)
            elif use.get("type") == "output":
                job.outputs[lfn] = bool(use.get("stageOut", True))
        jobs[job.id] = job

    for dep in doc.get("jobDependencies") or []:
        parent = str(dep.get("id"))
        for child in dep.get("children") or []:
            child = str(child)
            if parent in jobs and child in jobs:
                jobs[parent].children.add(child)
                jobs[child].parents.add(parent)
    # Pegasus also infers edges from data flow; so do we, or a workflow that
    # declares none would look like unrelated jobs.
    producer = {lfn: j.id for j in jobs.values() for lfn in j.outputs}
    for job in jobs.values():
        for lfn in job.inputs:
            p = producer.get(lfn)
            if p and p != job.id:
                jobs[p].children.add(job.id)
                job.parents.add(p)
    return jobs


def steps(jobs: dict[str, Job]) -> list[dict[str, Any]]:
    """The steps a user can start from: one per job name, upstream first."""
    order = _topological(jobs)
    seen: dict[str, int] = {}
    for jid in order:
        name = jobs[jid].name
        seen[name] = seen.get(name, 0) + 1
    return [{"name": n, "count": c} for n, c in seen.items()]


def _topological(jobs: dict[str, Job]) -> list[str]:
    indeg = {j: len(jobs[j].parents) for j in jobs}
    ready = [j for j in jobs if indeg[j] == 0]
    out: list[str] = []
    while ready:
        j = ready.pop(0)
        out.append(j)
        for c in sorted(jobs[j].children):
            indeg[c] -= 1
            if indeg[c] == 0:
                ready.append(c)
    # A cycle can't plan anyway; keep whatever's left rather than lose it.
    out += [j for j in jobs if j not in out]
    return out


def _downstream(jobs: dict[str, Job], start: set[str]) -> set[str]:
    seen = set(start)
    stack = list(start)
    while stack:
        for c in jobs[stack.pop()].children:
            if c not in seen:
                seen.add(c)
                stack.append(c)
    return seen


# ── what earlier runs produced ───────────────────────────────────────────


def project_runs(project_dir: str) -> list[str]:
    """Run directories planned from this project, oldest first.

    Pegasus lays a run out as ``<base>/<user>/pegasus/<label>/runNNNN``, with
    ``<base>`` the plan's ``--dir`` (``submit`` for Studio, or the project
    root). Globbing that shape rather than the whole tree keeps a project with
    a large data directory from being walked file by file.
    """
    from services.workflow_scanner import _parse_braindump, _planned_at

    found: set[str] = set()
    for pattern in (
        os.path.join(project_dir, "*", "pegasus", "*", "run*", "braindump.yml"),
        os.path.join(project_dir, "*", "*", "pegasus", "*", "run*", "braindump.yml"),
    ):
        found.update(glob.glob(pattern))
    dated = []
    for bd_path in found:
        try:
            when = _planned_at(_parse_braindump(bd_path), bd_path)
        except Exception:  # noqa: BLE001 — an unreadable braindump sorts first
            when = 0.0
        dated.append((when, os.path.dirname(bd_path)))
    return [d for _, d in sorted(dated)]


def _succeeded(run_dir: str) -> dict[str, float]:
    """abstract job id -> submit time, for jobs whose last attempt succeeded."""
    from services.stampede_reader import find_stampede_db

    db = find_stampede_db(run_dir)
    if not db:
        return {}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return {}
    try:
        rows = con.execute(
            """
            SELECT t.abs_task_id, ji.exitcode,
                   (SELECT MIN(s.timestamp) FROM jobstate s
                     WHERE s.job_instance_id = ji.job_instance_id
                       AND s.state IN ('SUBMIT', 'EXECUTE'))
              FROM task t
              JOIN job_instance ji ON ji.job_id = t.job_id
             WHERE ji.job_submit_seq = (
                   SELECT MAX(j2.job_submit_seq) FROM job_instance j2
                    WHERE j2.job_id = t.job_id)
            """
        ).fetchall()
    except sqlite3.Error as e:
        log.info("rerun_stampede_unreadable", db=db, error=str(e))
        return {}
    finally:
        con.close()
    return {
        str(abs_id): float(started or 0)
        for abs_id, exitcode, started in rows
        if abs_id is not None and exitcode == 0
    }


def _run_workflow(run_dir: str) -> dict[str, Job] | None:
    """The abstract workflow this run was planned from (Pegasus copies it in)."""
    from services.workflow_scanner import _parse_braindump

    try:
        dax = _parse_braindump(os.path.join(run_dir, "braindump.yml")).get("dax")
    except Exception:  # noqa: BLE001
        return None
    if not dax:
        return None
    copy = os.path.join(run_dir, os.path.basename(str(dax)))
    if not os.path.isfile(copy):
        return None
    try:
        return load_workflow(copy)
    except RerunError:
        return None


def produced_files(project_dir: str) -> dict[str, Produced]:
    """lfn -> the current copy of it on disk, from this project's runs."""
    from services.run_outputs import resolve_output_dir
    from services.workflow_scanner import project_roots

    owner: dict[str, tuple[Produced, float]] = {}
    for run_dir in project_runs(project_dir):
        jobs = _run_workflow(run_dir)
        if not jobs:
            continue
        ok = _succeeded(run_dir)
        if not ok:
            continue
        out_dir, how = resolve_output_dir(run_dir, project_roots())
        # A guessed directory ("convention") proves nothing about where this
        # run's files went.
        if not out_dir or how != "catalog":
            continue
        for jid, started in ok.items():
            job = jobs.get(jid)
            if not job:
                continue
            for lfn, staged in job.outputs.items():
                if staged:
                    owner[lfn] = (
                        Produced(
                            lfn=lfn,
                            path=os.path.join(out_dir, lfn),
                            run_name=os.path.basename(run_dir),
                            job_id=jid,
                        ),
                        started,
                    )

    current: dict[str, Produced] = {}
    for lfn, (p, started) in owner.items():
        try:
            mtime = os.stat(p.path).st_mtime
        except OSError:
            continue
        if mtime + _CLOCK_SKEW_S >= started:
            current[lfn] = p
    return current


# ── the plan ─────────────────────────────────────────────────────────────


def prepare(project_dir: str, from_step: str, workflow_file: str) -> RerunPlan:
    """Work out a re-run of `workflow_file` from `from_step` (a job name or id)."""
    jobs = load_workflow(workflow_file)
    start = {j.id for j in jobs.values() if j.name == from_step or j.id == from_step}
    if not start:
        names = ", ".join(s["name"] for s in steps(jobs))
        raise RerunError(
            f"This workflow has no step named {from_step!r}. Its steps are: {names}."
        )
    rerun = _downstream(jobs, start)

    produced = produced_files(project_dir)
    fresh = {lfn for j in rerun for lfn in jobs[j].outputs}
    entries = [
        produced[lfn]
        for jid in _topological(jobs)
        if jid not in rerun
        for lfn in sorted(jobs[jid].outputs)
        if lfn in produced and lfn not in fresh
    ]
    listed = {e.lfn for e in entries}
    dropped = _simulate(jobs, rerun, listed)
    run = [j for j in _topological(jobs) if j not in dropped]
    reused = [j for j in _topological(jobs) if j in dropped]

    # Inputs of re-run steps that no earlier run left behind: why steps
    # upstream of the chosen one are running too.
    producer = {lfn: j.id for j in jobs.values() for lfn in j.outputs}
    missing: list[dict[str, str]] = []
    seen: set[str] = set()
    for jid in run:
        if jid not in rerun:
            continue
        for lfn in sorted(jobs[jid].inputs):
            if lfn in seen or lfn not in producer:
                continue
            if producer[lfn] in rerun or lfn in listed:
                continue
            seen.add(lfn)
            missing.append({"file": lfn, "step": jobs[producer[lfn]].name})
    return RerunPlan(
        from_step=from_step, run=run, reused=reused, missing=missing,
        entries=entries, jobs=jobs,
    )


def _simulate(jobs: dict[str, Job], rerun: set[str], listed: set[str]) -> set[str]:
    """The jobs Pegasus's data reuse will drop (see the module docstring)."""
    dropped = {
        j.id for j in jobs.values()
        if j.id not in rerun and j.outputs and all(o in listed for o in j.outputs)
    }
    changed = True
    while changed:
        changed = False
        for j in jobs.values():
            if j.id in dropped or j.id in rerun or not j.children:
                continue
            if not all(c in dropped for c in j.children):
                continue
            if any(staged and o not in listed for o, staged in j.outputs.items()):
                continue
            dropped.add(j.id)
            changed = True
    return dropped


def write_rc(project_dir: str, plan: RerunPlan) -> str:
    """Write the replica file for the planner; returns its absolute path."""
    rc_dir = os.path.join(project_dir, _RC_DIR)
    os.makedirs(rc_dir, exist_ok=True)
    path = os.path.join(rc_dir, RC_NAME)
    lines = [
        "# Written by Pegasus AI Studio for a re-run from step "
        f"{plan.from_step!r} (#680). Replaced on every re-run.",
    ]
    for e in plan.entries:
        lines.append(f'{_q(e.lfn)} {_q("file://" + e.path)} site="local"')
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    return path


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ── reading back what Pegasus planned ────────────────────────────────────

# Jobs Pegasus adds around the user's own. Everything else in the DAG is a
# compute job (or a cluster of them).
_AUX_PREFIXES = (
    "create_dir_", "stage_in_", "stage_out_", "stage_inter_", "stage_worker_",
    "cleanup_", "clean_up_", "register_", "chmod_",
)


def planned_steps(run_dir: str, jobs: dict[str, Job]) -> list[dict[str, Any]] | None:
    """The user's steps in a planned run's DAG, grouped like `steps()`.

    Compute jobs are named ``<transformation>_<abstract id>`` in the DAG. None
    when the DAG can't be read, so the caller says nothing rather than
    something wrong.
    """
    dags = glob.glob(os.path.join(run_dir, "*.dag"))
    if not dags:
        return None
    names: list[str] = []
    try:
        with open(dags[0], encoding="utf-8") as f:
            for line in f:
                if line.startswith("JOB "):
                    names.append(line.split()[1])
    except OSError:
        return None
    ids = []
    for n in names:
        if n.startswith(_AUX_PREFIXES):
            continue
        # Longest id wins: with jobs "A" and "analyze_A", the DAG job
        # "analyze_station_analyze_A" ends with both.
        matches = [j for j in jobs if n.endswith("_" + j)]
        if matches:
            ids.append(max(matches, key=len))
    return _group(ids, jobs)


def _group(ids: list[str], jobs: dict[str, Job]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for j in ids:
        name = jobs[j].name if j in jobs else j
        counts[name] = counts.get(name, 0) + 1
    return [{"name": n, "count": c} for n, c in counts.items()]


def describe(groups: list[dict[str, Any]]) -> str:
    """'aggregate, analyze_station ×3' for a run log line."""
    if not groups:
        return "nothing"
    return ", ".join(
        g["name"] + (f" ×{g['count']}" if g["count"] > 1 else "") for g in groups
    )
