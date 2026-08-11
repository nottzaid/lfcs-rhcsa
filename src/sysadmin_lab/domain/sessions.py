from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID, uuid4

from sysadmin_lab.domain.resources import NAME_COMPONENT


class SessionStatus(StrEnum):
    DECLARED = "declared"
    PROVISIONING = "provisioning"
    READY = "ready"
    CHECKING = "checking"
    REBOOTING = "rebooting"
    RESETTING = "resetting"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"
    FAILED = "failed"


ALLOWED_TRANSITIONS: dict[SessionStatus, frozenset[SessionStatus]] = {
    SessionStatus.DECLARED: frozenset(
        {SessionStatus.PROVISIONING, SessionStatus.DESTROYING, SessionStatus.FAILED}
    ),
    SessionStatus.PROVISIONING: frozenset(
        {SessionStatus.READY, SessionStatus.DESTROYING, SessionStatus.FAILED}
    ),
    SessionStatus.READY: frozenset(
        {
            SessionStatus.CHECKING,
            SessionStatus.REBOOTING,
            SessionStatus.RESETTING,
            SessionStatus.DESTROYING,
            SessionStatus.FAILED,
        }
    ),
    SessionStatus.CHECKING: frozenset(
        {SessionStatus.READY, SessionStatus.DESTROYING, SessionStatus.FAILED}
    ),
    SessionStatus.REBOOTING: frozenset(
        {SessionStatus.READY, SessionStatus.DESTROYING, SessionStatus.FAILED}
    ),
    SessionStatus.RESETTING: frozenset(
        {SessionStatus.READY, SessionStatus.DESTROYING, SessionStatus.FAILED}
    ),
    SessionStatus.FAILED: frozenset({SessionStatus.RESETTING, SessionStatus.DESTROYING}),
    SessionStatus.DESTROYING: frozenset({SessionStatus.DESTROYED, SessionStatus.FAILED}),
    SessionStatus.DESTROYED: frozenset(),
}


class InvalidSessionTransition(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SessionState:
    session_id: UUID
    scenario_id: str
    status: SessionStatus
    generation: int
    revision: int
    error: str | None = None

    def __post_init__(self) -> None:
        if not NAME_COMPONENT.fullmatch(self.scenario_id):
            raise ValueError(f"invalid scenario_id: {self.scenario_id}")
        if self.generation < 0 or self.revision < 0:
            raise ValueError("session counters cannot be negative")
        if self.status is SessionStatus.FAILED and not self.error:
            raise ValueError("failed sessions require an error")
        if self.status is not SessionStatus.FAILED and self.error is not None:
            raise ValueError("only failed sessions may contain an error")

    @classmethod
    def declared(cls, scenario_id: str, session_id: UUID | None = None) -> SessionState:
        return cls(
            session_id=session_id or uuid4(),
            scenario_id=scenario_id,
            status=SessionStatus.DECLARED,
            generation=0,
            revision=0,
        )

    def transition(self, target: SessionStatus, *, error: str | None = None) -> SessionState:
        if target not in ALLOWED_TRANSITIONS[self.status]:
            raise InvalidSessionTransition(f"cannot transition {self.status} to {target}")
        if target is SessionStatus.FAILED:
            if not error or not error.strip():
                raise InvalidSessionTransition("failed transitions require an error")
            next_error = error.strip()
        elif error is not None:
            raise InvalidSessionTransition("errors are valid only for failed transitions")
        else:
            next_error = None
        generation = self.generation + int(target is SessionStatus.RESETTING)
        return replace(
            self,
            status=target,
            generation=generation,
            revision=self.revision + 1,
            error=next_error,
        )
