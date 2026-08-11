from __future__ import annotations

from typing import Protocol
from uuid import UUID

from sysadmin_lab.domain.sessions import SessionState, SessionStatus


class SessionNotFoundError(LookupError):
    pass


class SessionConflictError(RuntimeError):
    pass


class SessionRepository(Protocol):
    def create(self, state: SessionState) -> None: ...

    def get(self, session_id: UUID) -> SessionState | None: ...

    def list_all(self, *, limit: int = 100) -> tuple[SessionState, ...]: ...

    def save(self, previous_revision: int, state: SessionState) -> None: ...


class SessionCoordinator:
    """Owns durable, revision-checked lifecycle transitions."""

    def __init__(self, repository: SessionRepository) -> None:
        self._repository = repository

    def declare(self, scenario_id: str, *, session_id: UUID | None = None) -> SessionState:
        state = SessionState.declared(scenario_id, session_id)
        self._repository.create(state)
        return state

    def get(self, session_id: UUID) -> SessionState:
        state = self._repository.get(session_id)
        if state is None:
            raise SessionNotFoundError(f"session does not exist: {session_id}")
        return state

    def list_all(self, *, limit: int = 100) -> tuple[SessionState, ...]:
        if limit < 1:
            raise ValueError("session list limit must be positive")
        return self._repository.list_all(limit=limit)

    def transition(
        self, session_id: UUID, target: SessionStatus, *, error: str | None = None
    ) -> SessionState:
        current = self.get(session_id)
        updated = current.transition(target, error=error)
        self._repository.save(current.revision, updated)
        return updated
