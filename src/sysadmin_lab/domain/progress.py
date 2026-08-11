from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sysadmin_lab.domain.resources import NAME_COMPONENT


@dataclass(frozen=True, slots=True)
class CheckAttempt:
    attempt_id: UUID
    session_id: UUID
    scenario_id: str
    scenario_version: int
    checked_at: datetime
    earned_weight: int
    available_weight: int
    required_passed: bool
    has_errors: bool

    def __post_init__(self) -> None:
        if not NAME_COMPONENT.fullmatch(self.scenario_id):
            raise ValueError(f"invalid scenario_id: {self.scenario_id}")
        if self.scenario_version < 1:
            raise ValueError("scenario version must be positive")
        if self.checked_at.tzinfo is None:
            raise ValueError("check time must include a timezone")
        if self.earned_weight < 0 or self.available_weight < 1:
            raise ValueError("check weights are invalid")
        if self.earned_weight > self.available_weight:
            raise ValueError("earned weight cannot exceed available weight")

    @classmethod
    def now(
        cls,
        *,
        session_id: UUID,
        scenario_id: str,
        scenario_version: int,
        earned_weight: int,
        available_weight: int,
        required_passed: bool,
        has_errors: bool,
    ) -> CheckAttempt:
        return cls(
            attempt_id=uuid4(),
            session_id=session_id,
            scenario_id=scenario_id,
            scenario_version=scenario_version,
            checked_at=datetime.now(UTC),
            earned_weight=earned_weight,
            available_weight=available_weight,
            required_passed=required_passed,
            has_errors=has_errors,
        )


@dataclass(frozen=True, slots=True)
class ScenarioProgress:
    scenario_id: str
    scenario_version: int
    attempts: int
    solved: bool
    best_earned_weight: int
    best_available_weight: int
    last_checked_at: datetime

    @property
    def best_fraction(self) -> float:
        return self.best_earned_weight / self.best_available_weight
