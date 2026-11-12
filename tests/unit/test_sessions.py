from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from sysadmin_lab.adapters.sqlite_sessions import SqliteSessionRepository
from sysadmin_lab.application.sessions import (
    SessionConflictError,
    SessionCoordinator,
    SessionNotFoundError,
)
from sysadmin_lab.domain.sessions import (
    InvalidSessionTransition,
    SessionState,
    SessionStatus,
)

SESSION_ID = UUID("10000000-0000-0000-0000-000000000001")


def test_session_state_machine_tracks_revision_generation_and_failure() -> None:
    state = SessionState.declared("base-smoke", SESSION_ID)
    state = state.transition(SessionStatus.PROVISIONING)
    state = state.transition(SessionStatus.READY)
    assert state.revision == 2
    assert state.generation == 0

    state = state.transition(SessionStatus.RESETTING)
    assert state.generation == 1
    state = state.transition(SessionStatus.FAILED, error=" image build failed ")
    assert state.error == "image build failed"
    state = state.transition(SessionStatus.DESTROYING)
    assert state.error is None
    assert state.transition(SessionStatus.DESTROYED).status is SessionStatus.DESTROYED


def test_session_state_machine_rejects_invalid_transitions_and_errors() -> None:
    state = SessionState.declared("base-smoke", SESSION_ID)
    with pytest.raises(InvalidSessionTransition, match="cannot transition"):
        state.transition(SessionStatus.READY)
    with pytest.raises(InvalidSessionTransition, match="require an error"):
        state.transition(SessionStatus.FAILED)
    with pytest.raises(InvalidSessionTransition, match="only for failed"):
        state.transition(SessionStatus.PROVISIONING, error="unexpected")


def test_sqlite_repository_round_trip_and_compare_and_swap(tmp_path: Path) -> None:
    with SqliteSessionRepository(tmp_path / "state.db") as repository:
        coordinator = SessionCoordinator(repository)
        declared = coordinator.declare("base-smoke", session_id=SESSION_ID)
        assert coordinator.get(SESSION_ID) == declared
        assert coordinator.list_all() == (declared,)
        provisioning = coordinator.transition(SESSION_ID, SessionStatus.PROVISIONING)
        assert provisioning.revision == 1

        stale_update = declared.transition(SessionStatus.FAILED, error="stale")
        with pytest.raises(SessionConflictError, match="concurrently"):
            repository.save(declared.revision, stale_update)


def test_sqlite_repository_rejects_duplicates_and_revision_jumps(tmp_path: Path) -> None:
    state = SessionState.declared("base-smoke", SESSION_ID)
    with SqliteSessionRepository(tmp_path / "state.db") as repository:
        repository.create(state)
        with pytest.raises(SessionConflictError, match="already exists"):
            repository.create(state)
        with pytest.raises(SessionConflictError, match="exactly one"):
            repository.save(0, replace(state, revision=2))


def test_coordinator_reports_missing_session(tmp_path: Path) -> None:
    with (
        SqliteSessionRepository(tmp_path / "state.db") as repository,
        pytest.raises(SessionNotFoundError, match="does not exist"),
    ):
        SessionCoordinator(repository).get(uuid4())


def test_session_listing_is_newest_first_and_requires_positive_limit(tmp_path: Path) -> None:
    with SqliteSessionRepository(tmp_path / "state.db") as repository:
        coordinator = SessionCoordinator(repository)
        first = coordinator.declare("first-scenario")
        second = coordinator.declare("second-scenario")
        assert coordinator.list_all(limit=1) == (second,)
        assert coordinator.list_all() == (second, first)
        with pytest.raises(ValueError, match="positive"):
            coordinator.list_all(limit=0)


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"scenario_id": "Bad"}, "invalid scenario_id"),
        ({"generation": -1}, "cannot be negative"),
        ({"status": SessionStatus.FAILED}, "require an error"),
        ({"error": "not failed"}, "only failed"),
    ],
)
def test_session_state_validates_invariants(changes: dict[str, object], match: str) -> None:
    state = SessionState.declared("base-smoke", SESSION_ID)
    with pytest.raises(ValueError, match=match):
        replace(state, **changes)
