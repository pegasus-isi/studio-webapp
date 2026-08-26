"""Tests for runtime failure diagnosis (services/run_diagnosis.py, #241 P5).

The invariant that matters most is structural rather than per-rule: a cause is
never returned without an action. Naming a problem and stopping is the failure
mode of the raw-stderr panel this replaces — information the user cannot act
on, presented as though it were help.
"""

from __future__ import annotations

import pytest

from services import run_diagnosis as rd


# ── the structural invariant ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "stderr",
    [
        "",
        "something nobody wrote a rule for",
        "Segmentation fault",
        "\x00\xff binary garbage",
    ],
)
def test_every_job_failure_gets_a_cause_and_an_action(stderr):
    cause, action = rd.classify_job_failure(stderr, 1)
    assert cause and action, "a cause without an action is not help"


@pytest.mark.parametrize("reason", ["", "unintelligible hold text", "12345"])
def test_every_hold_gets_a_cause_and_an_action(reason):
    cause, action = rd.classify_hold(reason)
    assert cause and action


# ── job failures ──────────────────────────────────────────────────────────


def test_exit_137_is_explained_as_out_of_memory():
    # The least self-evident code a user will ever be shown: 128 + SIGKILL,
    # nearly always the OOM killer, and meaningless without translation.
    cause, action = rd.classify_job_failure("", 137)
    assert "out of memory" in cause.lower()
    assert "memory" in action.lower()


def test_missing_input_file_is_named():
    cause, action = rd.classify_job_failure(
        "IOError: No such file or directory: '/data/subjects.tsv'", 1
    )
    assert "/data/subjects.tsv" in cause
    assert "workspace" in action.lower()


def test_missing_python_package_says_where_to_install_it():
    cause, action = rd.classify_job_failure(
        "ModuleNotFoundError: No module named 'mne'", 1
    )
    assert "mne" in cause
    # The trap this rule exists to close: installing it in a terminal does
    # nothing, because the steps run somewhere else entirely.
    assert "software environment" in action.lower()
    assert "terminal" in action.lower()


def test_disk_full_is_distinguished_from_memory():
    cause, _ = rd.classify_job_failure("No space left on device", 1)
    assert "disk" in cause.lower()
    assert "memory" not in cause.lower()


def test_container_failure_points_at_the_environment():
    cause, action = rd.classify_job_failure(
        "FATAL: while running apptainer: container not found", 255
    )
    assert "software environment" in cause.lower()
    assert "rebuild" in action.lower()


def test_exitcode_alone_is_enough_when_stderr_is_empty():
    # A job killed by the OOM killer often writes nothing at all.
    cause, _ = rd.classify_job_failure("", 137)
    assert cause != rd._JOB_FALLBACK[0]


# ── holds ─────────────────────────────────────────────────────────────────


def test_memory_hold_says_which_number_to_raise():
    cause, action = rd.classify_hold(
        "Error from slot1@node7: Job has gone over memory usage limit"
    )
    assert "memory" in cause.lower()
    assert "increase" in action.lower()


def test_transient_slot_failure_suggests_retrying_first():
    cause, action = rd.classify_hold("Failed to execute /bin/sh: no such file")
    assert "run again" in action.lower()


def test_credential_hold_names_a_human_rather_than_a_retry():
    # Nothing self-service exists here; saying so is still an action, and
    # honest — unlike telling them to retry something that cannot work.
    _, action = rd.classify_hold("Failed to authenticate proxy credential")
    assert "report" in action.lower() or "runs studio" in action.lower()
    # Must not send them round the loop again: retrying an expired credential
    # fails identically every time.
    assert "run again" not in action.lower()
    assert "try again" not in action.lower()


# ── card assembly ─────────────────────────────────────────────────────────


def test_only_failed_jobs_produce_cards():
    jobs = [
        {"job_id": "a", "status": "succeeded", "exitcode": 0},
        {"job_id": "b", "status": "failed", "exitcode": 1, "stderr": "boom"},
        {"job_id": "c", "status": "running", "exitcode": None},
    ]
    cards = rd.diagnose_jobs(jobs)
    assert len(cards) == 1
    assert cards[0]["step"] == "b"


def test_cards_prefer_the_transformation_over_the_pegasus_job_id():
    # `epoch_epoch_1002` names nothing the user wrote; the transformation is
    # the step as their workflow defines it.
    cards = rd.diagnose_jobs([
        {
            "job_id": "epoch_epoch_1002",
            "transformation": "epoch",
            "status": "failed",
            "exitcode": 1,
            "stderr": "boom",
        }
    ])
    assert cards[0]["step"] == "epoch"


def test_nonzero_exitcode_counts_as_failed_even_without_the_status():
    cards = rd.diagnose_jobs([{"job_id": "a", "exitcode": 2, "stderr": "x"}])
    assert len(cards) == 1


def test_stderr_is_preserved_as_technical_detail_not_discarded():
    cards = rd.diagnose_jobs([
        {"job_id": "a", "status": "failed", "exitcode": 1, "stderr": "raw trace"}
    ])
    assert cards[0]["technical"] == "raw trace"


def test_identical_hold_reasons_are_deduplicated():
    # One bad requirement holds every job in a step; forty identical cards is
    # noise, not forty problems.
    cards = rd.diagnose_holds(["over memory limit"] * 40)
    assert len(cards) == 1


def test_blank_hold_reasons_are_dropped():
    assert rd.diagnose_holds(["", "   ", None]) == []


def test_no_failures_yields_no_cards():
    assert rd.diagnose_jobs([]) == []
    assert rd.diagnose_jobs([{"job_id": "a", "status": "succeeded", "exitcode": 0}]) == []


# ── endpoint ──────────────────────────────────────────────────────────────


def test_diagnosis_endpoint_404s_for_an_unknown_run(client):
    assert client.get("/api/workflows/nope/diagnosis").status_code == 404
