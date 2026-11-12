from __future__ import annotations

from datetime import date
from typing import Self

from pydantic import Field, HttpUrl, model_validator

from sysadmin_lab.domain.models import IDENTIFIER_PATTERN, StrictModel, Track


class AuthoritySource(StrictModel):
    source_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    title: str = Field(min_length=1)
    url: HttpUrl
    retrieved_on: date


class Competency(StrictModel):
    competency_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    title: str = Field(min_length=1)


class CurriculumDomain(StrictModel):
    domain_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    title: str = Field(min_length=1)
    weight_percent: int = Field(gt=0, le=100)
    competencies: tuple[Competency, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def competency_ids_are_unique(self) -> Self:
        identifiers = [item.competency_id for item in self.competencies]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError(f"duplicate competency identifiers in {self.domain_id}")
        return self


class ExamProfile(StrictModel):
    performance_based: bool
    command_line: bool
    duration_minutes: int = Field(gt=0)
    task_count_min: int = Field(gt=0)
    task_count_max: int = Field(gt=0)
    passing_score_percent: int = Field(gt=0, le=100)
    distribution_specific: bool
    operational_notes: tuple[str, ...] = Field(min_length=1)
    source_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def task_range_is_ordered(self) -> Self:
        if self.task_count_min > self.task_count_max:
            raise ValueError("exam task count minimum exceeds maximum")
        return self


class CurriculumManifest(StrictModel):
    schema_version: int = Field(default=1, ge=1)
    curriculum_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    track: Track
    title: str = Field(min_length=1)
    effective_as_of: date
    sources: tuple[AuthoritySource, ...] = Field(min_length=1)
    domains: tuple[CurriculumDomain, ...] = Field(min_length=1)
    exam: ExamProfile

    @model_validator(mode="after")
    def validate_curriculum(self) -> Self:
        if sum(domain.weight_percent for domain in self.domains) != 100:
            raise ValueError("curriculum domain weights must total 100")
        domain_ids = [domain.domain_id for domain in self.domains]
        if len(domain_ids) != len(set(domain_ids)):
            raise ValueError("curriculum domain identifiers must be unique")
        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("curriculum source identifiers must be unique")
        missing_sources = sorted(set(self.exam.source_ids) - set(source_ids))
        if missing_sources:
            raise ValueError(
                f"exam profile references unknown sources: {', '.join(missing_sources)}"
            )
        return self

    @property
    def objective_ids(self) -> frozenset[str]:
        return frozenset(self.objective_titles)

    @property
    def objective_titles(self) -> dict[str, str]:
        return {
            f"{domain.domain_id}.{competency.competency_id}": competency.title
            for domain in self.domains
            for competency in domain.competencies
        }
