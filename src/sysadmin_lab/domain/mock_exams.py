from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from sysadmin_lab.domain.models import IDENTIFIER_PATTERN, ScenarioStatus, StrictModel


class MockExamManifest(StrictModel):
    """A rehearsal: a review of practice scenarios, or exam tasks seen nowhere else.

    `suggested_minutes` is the time limit when a learner starts a timed rehearsal.
    """

    schema_version: int = Field(default=1, ge=1)
    mock_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    status: ScenarioStatus = ScenarioStatus.DRAFT
    kind: Literal["review", "exam"]
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    suggested_minutes: int = Field(ge=30, le=240)
    tasks: tuple[str, ...] = Field(min_length=17, max_length=20)

    @model_validator(mode="after")
    def unique_tasks(self) -> Self:
        if len(self.tasks) != len(set(self.tasks)):
            raise ValueError("mock exam task identifiers must be unique")
        return self
