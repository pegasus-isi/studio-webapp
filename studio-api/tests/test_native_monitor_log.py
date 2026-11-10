"""A native pegasus-monitor event log must not be read as the legacy one (#656).

Pegasus 6.0's ``pegasus-monitor --log/--serve`` writes
``SUBMIT_DIR/workflow-events.jsonl`` by default: the filename Studio's legacy
reader looks for first, in a format it cannot read. Its records are keyed by
``record_type`` and never carry ``event_type``. Taken as the legacy schema the
file is a non-empty list with nothing recognisable in it, so the run read
"planning" with zero jobs and the stampede DB was never consulted. The zombie
guard does not rescue that either: it only runs for a "running" status.
"""

from __future__ import annotations

import json
import os

from services import event_reader, stampede_reader, workflow_scanner

# The first records of a native stream, trimmed to the keys that identify it
# (pegasus test/monitor/contracts/jsonl-v1-records.json).
NATIVE_RECORDS = [
    {
        "schema_version": 1,
        "record_type": "header",
        "sequence": 1,
        "stream_id": "s-1",
        "contract_status": "frozen",
        "workflow": {"wf_uuid": "u-1", "root_wf_uuid": "u-1"},
    },
    {
        "schema_version": 1,
        "record_type": "checkpoint",
        "sequence": 2,
        "stream_id": "s-1",
        "reason": "final",
        "snapshot": {"workflow": {"state": "WORKFLOW_TERMINATED", "status": 0}},
    },
]

LEGACY_RECORDS = [
    {"event_type": "jobs_init", "total_jobs": 1, "jobs": [{"job_id": "a"}]},
    {"event_type": "job_state", "job_id": "a", "state": "JOB_SUCCESS", "exitcode": 0},
    {"event_type": "workflow_end", "wf_status": 0, "total_jobs": 1, "done": 1},
]

STAMPEDE_EVENTS = [
    {"event_type": "jobs_init", "total_jobs": 2, "jobs": [{"job_id": "x"}, {"job_id": "y"}]},
    {"event_type": "job_state", "job_id": "x", "state": "JOB_SUCCESS", "exitcode": 0},
    {"event_type": "job_state", "job_id": "y", "state": "EXECUTE"},
    {"event_type": "workflow_state", "state": "WORKFLOW_STARTED"},
]


def _write(path, records, *, leading_blank=False):
    with open(path, "w") as f:
        if leading_blank:
            f.write("\n")
        for r in records:
            f.write(json.dumps(r) + "\n")


def _stampede(monkeypatch):
    monkeypatch.setattr(
        stampede_reader, "read_stampede_events", lambda run_dir: list(STAMPEDE_EVENTS)
    )


def test_native_log_is_recognised(tmp_path):
    native = tmp_path / "workflow-events.jsonl"
    _write(native, NATIVE_RECORDS, leading_blank=True)
    assert event_reader.is_native_monitor_log(str(native))


def test_legacy_and_unreadable_logs_are_not_native(tmp_path):
    legacy = tmp_path / "legacy.jsonl"
    _write(legacy, LEGACY_RECORDS)
    torn = tmp_path / "torn.jsonl"
    torn.write_text('{"record_type": "hea')
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    array = tmp_path / "array.jsonl"
    array.write_text("[1, 2]\n")
    for path in (legacy, torn, empty, array, tmp_path / "missing.jsonl"):
        assert not event_reader.is_native_monitor_log(str(path)), path.name


def test_native_log_falls_through_to_the_stampede_db(tmp_path, monkeypatch):
    _write(tmp_path / "workflow-events.jsonl", NATIVE_RECORDS)
    _stampede(monkeypatch)

    assert event_reader.find_events_file(str(tmp_path)) is None
    events = event_reader.read_events(str(tmp_path))
    assert events == STAMPEDE_EVENTS
    # What the bug looked like: "planning", no jobs.
    assert event_reader.workflow_status(events) == "running"
    assert [j["status"] for j in event_reader.jobs(events)] == ["succeeded", "running"]
    detail = event_reader.workflow_detail(events)
    assert detail["total_jobs"] == 2
    assert detail["completed_jobs"] == 1


def test_native_log_does_not_hide_a_legacy_wfevents_log(tmp_path, monkeypatch):
    """The sniff is per candidate file: the lookup moves on to wfevents.jsonl."""
    _write(tmp_path / "workflow-events.jsonl", NATIVE_RECORDS)
    _write(tmp_path / "wfevents.jsonl", LEGACY_RECORDS)
    _stampede(monkeypatch)

    assert event_reader.find_events_file(str(tmp_path)) == os.path.join(
        str(tmp_path), "wfevents.jsonl"
    )
    assert event_reader.workflow_status(event_reader.read_events(str(tmp_path))) == "succeeded"


def test_native_log_in_the_submit_dir_is_skipped_too(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    submit_dir = tmp_path / "submit"
    run_dir.mkdir()
    submit_dir.mkdir()
    (run_dir / "braindump.yml").write_text(f"submit_dir: {submit_dir}\n")
    _write(submit_dir / "workflow-events.jsonl", NATIVE_RECORDS)
    _stampede(monkeypatch)

    assert event_reader.find_events_file(str(run_dir)) is None
    assert event_reader.read_events(str(run_dir)) == STAMPEDE_EVENTS


def test_legacy_log_is_still_preferred(tmp_path, monkeypatch):
    _write(tmp_path / "workflow-events.jsonl", LEGACY_RECORDS)
    _stampede(monkeypatch)

    assert event_reader.find_events_file(str(tmp_path)).endswith("workflow-events.jsonl")
    assert event_reader.workflow_status(event_reader.read_events(str(tmp_path))) == "succeeded"


def test_zombie_guard_still_applies_behind_a_native_log(tmp_path, monkeypatch):
    """A "planning" misread skipped the dagman.out cross-check, which only runs
    for "running". With the native log skipped, the stampede events say
    "running" and DAGMan's own exit line corrects it (#296)."""
    _write(tmp_path / "workflow-events.jsonl", NATIVE_RECORDS)
    _stampede(monkeypatch)
    (tmp_path / "wf-0.dag.dagman.out").write_text(
        "11/10/26 10:00:00 **** condor_scheduniv_exec.1.0 (condor_DAGMAN) STARTING UP\n"
        "11/10/26 10:05:00 **** condor_scheduniv_exec.1.0 (condor_DAGMAN) pid 9 "
        "EXITING WITH STATUS 1\n"
    )

    assert workflow_scanner.get_run_status(str(tmp_path)) == "failed"
