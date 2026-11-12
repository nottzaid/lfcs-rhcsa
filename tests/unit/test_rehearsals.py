"""Mock exam rehearsals score the checks run since they began, with partial credit."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from sysadmin_lab.adapters.sqlite_rehearsals import SqliteMockRehearsalRepository
from sysadmin_lab.domain.progress import CheckAttempt
from sysadmin_lab.domain.rehearsals import Rehearsal, TaskStatus, score_rehearsal

START = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def attempt(
    scenario_id: str,
    minutes: int,
    earned: int,
    *,
    solved: bool = False,
    version: int = 2,
    errors: bool = False,
) -> CheckAttempt:
    return CheckAttempt(
        attempt_id=uuid4(),
        session_id=uuid4(),
        scenario_id=scenario_id,
        scenario_version=version,
        checked_at=START + timedelta(minutes=minutes),
        earned_weight=earned,
        available_weight=4,
        required_passed=solved,
        has_errors=errors,
    )


def test_tasks_score_only_what_was_checked_during_the_rehearsal() -> None:
    tasks = ("lvm-online-growth", "nfs-client-recovery", "chrony-source-recovery", "acl-x")
    versions = dict.fromkeys(tasks, 2)
    attempts = [
        attempt("lvm-online-growth", -30, 4, solved=True),  # before the rehearsal: ignored
        attempt("lvm-online-growth", 10, 1),
        attempt("lvm-online-growth", 25, 3),  # best share so far: 3 of 4
        attempt("nfs-client-recovery", 5, 2),
        attempt("nfs-client-recovery", 40, 4, solved=True),
        attempt("chrony-source-recovery", 15, 4, solved=True, version=1),  # an older version
        attempt("acl-x", 20, 4, solved=True, errors=True),  # an errored check proves nothing
    ]
    score = score_rehearsal(tasks, versions, attempts, Rehearsal(START), passing_percent=67)

    assert [(task.status, task.credit) for task in score.tasks] == [
        (TaskStatus.PARTIAL, 0.75),
        (TaskStatus.SOLVED, 1.0),
        (TaskStatus.UNTOUCHED, 0.0),
        (TaskStatus.UNTOUCHED, 0.0),
    ]
    assert score.solved == 1
    assert score.percent == 44  # (0.75 + 1) / 4
    assert not score.passed


def test_the_pass_mark_is_the_curriculums() -> None:
    tasks = tuple(f"task-{n}" for n in range(3))
    attempts = [attempt("task-0", 1, 4, solved=True), attempt("task-1", 2, 4, solved=True)]
    versions = dict.fromkeys(tasks, 2)
    score = score_rehearsal(tasks, versions, attempts, Rehearsal(START), passing_percent=67)
    assert score.percent == 67 and score.passed
    assert not score_rehearsal(
        tasks, versions, attempts, Rehearsal(START), passing_percent=70
    ).passed


def test_a_timed_rehearsal_ignores_checks_after_its_deadline() -> None:
    tasks = ("lvm-online-growth", "nfs-client-recovery")
    timed = Rehearsal(START, deadline=START + timedelta(minutes=120))
    attempts = [
        attempt("lvm-online-growth", 119, 4, solved=True),
        attempt("nfs-client-recovery", 121, 4, solved=True),  # one minute too late
    ]
    score = score_rehearsal(tasks, dict.fromkeys(tasks, 2), attempts, timed, passing_percent=67)

    assert [task.status for task in score.tasks] == [TaskStatus.SOLVED, TaskStatus.UNTOUCHED]
    assert score.deadline == timed.deadline
    assert not score.time_is_up(START + timedelta(minutes=120))
    assert score.time_is_up(START + timedelta(minutes=120, seconds=1))
    untimed = score_rehearsal(tasks, dict.fromkeys(tasks, 2), attempts, Rehearsal(START), 67)
    assert untimed.solved == 2 and not untimed.time_is_up(START + timedelta(days=9))


def test_starting_again_replaces_the_previous_rehearsal(tmp_path: Path) -> None:
    timed = Rehearsal(START + timedelta(days=1), START + timedelta(days=1, hours=2))
    with SqliteMockRehearsalRepository(tmp_path / "state.db") as rehearsals:
        assert rehearsals.get("lfcs-mock-a") is None
        rehearsals.start("lfcs-mock-a", Rehearsal(START))
        rehearsals.start("lfcs-mock-a", timed)
        rehearsals.start("lfcs-mock-b", Rehearsal(START))
    with SqliteMockRehearsalRepository(tmp_path / "state.db") as reopened:
        assert reopened.get("lfcs-mock-a") == timed
        assert reopened.get("lfcs-mock-b") == Rehearsal(START)
        with pytest.raises(ValueError, match="must include a timezone"):
            reopened.start("lfcs-mock-a", Rehearsal(datetime(2026, 10, 1, 9, 0)))
        with pytest.raises(ValueError, match="must include a timezone"):
            reopened.start("lfcs-mock-a", Rehearsal(START, datetime(2026, 10, 1, 11, 0)))


def test_rehearsals_kept_before_timing_existed_still_load(tmp_path: Path) -> None:
    state = tmp_path / "state.db"
    with sqlite3.connect(state) as old:
        old.execute(
            "CREATE TABLE mock_rehearsals (mock_id TEXT PRIMARY KEY, started_at TEXT NOT NULL)"
        )
        old.execute("INSERT INTO mock_rehearsals VALUES (?, ?)", ("lfcs-mock-a", START.isoformat()))
    state.chmod(0o600)
    with SqliteMockRehearsalRepository(state) as rehearsals:
        assert rehearsals.get("lfcs-mock-a") == Rehearsal(START)
        rehearsals.start("lfcs-mock-b", Rehearsal(START, START + timedelta(hours=2)))
        assert rehearsals.get("lfcs-mock-b") == Rehearsal(START, START + timedelta(hours=2))
