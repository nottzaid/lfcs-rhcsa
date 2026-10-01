"""Scoring a mock exam rehearsal from the checks a learner ran after starting it."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sysadmin_lab.domain.progress import CheckAttempt


class TaskStatus(StrEnum):
    SOLVED = "solved"
    PARTIAL = "partial"
    UNTOUCHED = "untouched"


@dataclass(frozen=True, slots=True)
class TaskScore:
    scenario_id: str
    status: TaskStatus
    credit: float  # 0 to 1: a solved task earns all of it, an unsolved one its best check share


@dataclass(frozen=True, slots=True)
class RehearsalScore:
    started_at: datetime
    tasks: tuple[TaskScore, ...]
    passing_percent: int

    @property
    def percent(self) -> int:
        return round(100 * sum(task.credit for task in self.tasks) / len(self.tasks))

    @property
    def solved(self) -> int:
        return sum(task.status is TaskStatus.SOLVED for task in self.tasks)

    @property
    def passed(self) -> bool:
        return self.percent >= self.passing_percent


def score_rehearsal(
    tasks: Iterable[str],
    versions: Mapping[str, int],
    attempts: Iterable[CheckAttempt],
    started_at: datetime,
    passing_percent: int,
) -> RehearsalScore:
    """Score each task by the checks run on its current version since the rehearsal began.

    Like the exam, a task earns partial credit for the requirements it meets; a check that
    errored says nothing about the learner's work and earns nothing.
    """
    counted = [
        attempt
        for attempt in attempts
        if attempt.checked_at >= started_at and not attempt.has_errors
    ]
    scores = []
    for scenario_id in tasks:
        relevant = [
            attempt
            for attempt in counted
            if attempt.scenario_id == scenario_id
            and attempt.scenario_version == versions[scenario_id]
        ]
        if not relevant:
            scores.append(TaskScore(scenario_id, TaskStatus.UNTOUCHED, 0.0))
        elif any(attempt.required_passed for attempt in relevant):
            scores.append(TaskScore(scenario_id, TaskStatus.SOLVED, 1.0))
        else:
            best = max(attempt.earned_weight / attempt.available_weight for attempt in relevant)
            scores.append(TaskScore(scenario_id, TaskStatus.PARTIAL, best))
    return RehearsalScore(started_at, tuple(scores), passing_percent)
