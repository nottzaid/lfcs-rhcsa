from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from sysadmin_lab.adapters.sqlite_progress import SqliteCheckAttemptRepository
from sysadmin_lab.application.progress import ProgressService
from sysadmin_lab.domain.progress import CheckAttempt

SESSION_ID = UUID("10000000-0000-0000-0000-000000000001")
ATTEMPT_ID = UUID("20000000-0000-0000-0000-000000000002")


def attempt(
    *,
    attempt_id: UUID = ATTEMPT_ID,
    scenario_version: int = 1,
    earned: int = 2,
    passed: bool = False,
    checked_at: datetime | None = None,
) -> CheckAttempt:
    return CheckAttempt(
        attempt_id=attempt_id,
        session_id=SESSION_ID,
        scenario_id="account-repair",
        scenario_version=scenario_version,
        checked_at=checked_at or datetime(2026, 8, 11, tzinfo=UTC),
        earned_weight=earned,
        available_weight=6,
        required_passed=passed,
        has_errors=False,
    )


def test_progress_repository_persists_attempts(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    expected = attempt()
    with SqliteCheckAttemptRepository(path) as repository:
        repository.add(expected)
    with SqliteCheckAttemptRepository(path) as repository:
        assert repository.list_all() == (expected,)


def test_progress_service_tracks_best_score_solved_state_and_versions(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with SqliteCheckAttemptRepository(path) as repository:
        service = ProgressService(repository)
        first = attempt()
        passed = attempt(
            attempt_id=UUID("30000000-0000-0000-0000-000000000003"),
            earned=6,
            passed=True,
            checked_at=first.checked_at + timedelta(minutes=5),
        )
        new_version = attempt(
            attempt_id=UUID("40000000-0000-0000-0000-000000000004"),
            scenario_version=2,
            earned=1,
            checked_at=first.checked_at + timedelta(minutes=10),
        )
        for item in (first, passed, new_version):
            service.record(item)

        latest, old = service.list_all()
        assert latest.scenario_version == 2
        assert not latest.solved
        assert old.attempts == 2
        assert old.solved
        assert (old.best_earned_weight, old.best_available_weight) == (6, 6)
        assert old.best_fraction == 1.0


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"scenario_id": "Bad"}, "invalid scenario_id"),
        ({"scenario_version": 0}, "version must be positive"),
        ({"checked_at": datetime(2026, 8, 11)}, "timezone"),
        ({"earned_weight": -1}, "weights are invalid"),
        ({"available_weight": 0}, "weights are invalid"),
        ({"earned_weight": 7}, "cannot exceed"),
    ],
)
def test_check_attempt_validates_invariants(changes: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        replace(attempt(), **changes)


def test_check_attempt_now_uses_aware_time_and_new_identity() -> None:
    created = CheckAttempt.now(
        session_id=SESSION_ID,
        scenario_id="account-repair",
        scenario_version=1,
        earned_weight=6,
        available_weight=6,
        required_passed=True,
        has_errors=False,
    )
    assert created.attempt_id != ATTEMPT_ID
    assert created.checked_at.tzinfo is not None
