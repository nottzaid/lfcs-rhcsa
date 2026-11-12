from __future__ import annotations

from typing import Protocol

from sysadmin_lab.domain.progress import CheckAttempt, ScenarioProgress


class CheckAttemptRepository(Protocol):
    def add(self, attempt: CheckAttempt) -> None: ...

    def list_all(self) -> tuple[CheckAttempt, ...]: ...


class ProgressService:
    def __init__(self, repository: CheckAttemptRepository) -> None:
        self._repository = repository

    def record(self, attempt: CheckAttempt) -> None:
        self._repository.add(attempt)

    def attempts(self) -> tuple[CheckAttempt, ...]:
        return self._repository.list_all()

    def list_all(self) -> tuple[ScenarioProgress, ...]:
        grouped: dict[tuple[str, int], list[CheckAttempt]] = {}
        for attempt in self._repository.list_all():
            grouped.setdefault((attempt.scenario_id, attempt.scenario_version), []).append(attempt)

        progress: list[ScenarioProgress] = []
        for (scenario_id, scenario_version), attempts in grouped.items():
            best = max(attempts, key=lambda item: item.earned_weight / item.available_weight)
            progress.append(
                ScenarioProgress(
                    scenario_id=scenario_id,
                    scenario_version=scenario_version,
                    attempts=len(attempts),
                    solved=any(
                        attempt.required_passed and not attempt.has_errors for attempt in attempts
                    ),
                    best_earned_weight=best.earned_weight,
                    best_available_weight=best.available_weight,
                    last_checked_at=max(attempt.checked_at for attempt in attempts),
                )
            )
        return tuple(sorted(progress, key=lambda item: item.last_checked_at, reverse=True))
