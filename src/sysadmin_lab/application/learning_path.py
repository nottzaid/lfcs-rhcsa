from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sysadmin_lab.domain.curricula import CurriculumManifest
from sysadmin_lab.domain.models import ScenarioManifest
from sysadmin_lab.domain.progress import ScenarioProgress


@dataclass(frozen=True, slots=True)
class PathStep:
    scenario: ScenarioManifest
    progress: ScenarioProgress | None = None

    @property
    def solved(self) -> bool:
        return self.progress is not None and self.progress.solved


@dataclass(frozen=True, slots=True)
class PathSection:
    key: str
    title: str
    note: str
    steps: tuple[PathStep, ...]

    @property
    def solved(self) -> int:
        return sum(step.solved for step in self.steps)


def _order(scenario: ScenarioManifest) -> tuple[int, int, str]:
    return (scenario.difficulty.rank, scenario.estimated_minutes, scenario.title)


def build_learning_path(
    scenarios: Iterable[ScenarioManifest],
    curriculum: CurriculumManifest,
    progress: Iterable[ScenarioProgress],
) -> tuple[PathSection, ...]:
    """Group focused scenarios by curriculum domain, easiest first, then integrated incidents.

    Progress only counts for the scenario version currently published, so a rewritten
    scenario must be solved again.
    """
    current = {(item.scenario_id, item.scenario_version): item for item in progress}
    grouped: dict[str, list[ScenarioManifest]] = {}
    integrated: list[ScenarioManifest] = []
    for scenario in scenarios:
        if scenario.integrated:
            integrated.append(scenario)
        else:
            domain_id = scenario.primary_objective.objective_id.split(".", 1)[0]
            grouped.setdefault(domain_id, []).append(scenario)

    def steps(members: list[ScenarioManifest]) -> tuple[PathStep, ...]:
        return tuple(
            PathStep(scenario, current.get((scenario.scenario_id, scenario.version)))
            for scenario in sorted(members, key=_order)
        )

    sections: list[PathSection] = []
    for domain in curriculum.domains:
        members = grouped.pop(domain.domain_id, [])
        if members:
            note = f"{domain.weight_percent}% of the exam"
            sections.append(PathSection(domain.domain_id, domain.title, note, steps(members)))
    leftovers = [scenario for members in grouped.values() for scenario in members]
    if leftovers:
        sections.append(PathSection("other", "Other practice", "", steps(leftovers)))
    if integrated:
        note = "Multi-competency incidents in exam conditions"
        sections.append(PathSection("integrated", "Integrated incidents", note, steps(integrated)))
    return tuple(sections)
