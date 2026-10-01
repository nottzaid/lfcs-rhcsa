"""Mock exam rehearsals score the checks run since they began, with partial credit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from sysadmin_lab.adapters.sqlite_rehearsals import SqliteMockRehearsalRepository
from sysadmin_lab.domain.progress import CheckAttempt
from sysadmin_lab.domain.rehearsals import TaskStatus, score_rehearsal

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
    score = score_rehearsal(tasks, versions, attempts, START, passing_percent=67)

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
    score = score_rehearsal(tasks, dict.fromkeys(tasks, 2), attempts, START, passing_percent=67)
    assert score.percent == 67 and score.passed
    assert not score_rehearsal(
        tasks, dict.fromkeys(tasks, 2), attempts, START, passing_percent=70
    ).passed


def test_starting_again_replaces_the_previous_rehearsal(tmp_path: Path) -> None:
    with SqliteMockRehearsalRepository(tmp_path / "state.db") as rehearsals:
        assert rehearsals.started_at("lfcs-mock-a") is None
        rehearsals.start("lfcs-mock-a", START)
        rehearsals.start("lfcs-mock-a", START + timedelta(days=1))
        rehearsals.start("lfcs-mock-b", START)
    with SqliteMockRehearsalRepository(tmp_path / "state.db") as reopened:
        assert reopened.started_at("lfcs-mock-a") == START + timedelta(days=1)
        assert reopened.started_at("lfcs-mock-b") == START
        with pytest.raises(ValueError, match="must include a timezone"):
            reopened.start("lfcs-mock-a", datetime(2026, 10, 1, 9, 0))
