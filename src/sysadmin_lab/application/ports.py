from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sysadmin_lab.domain.models import CheckSpec, ScenarioManifest


@dataclass(frozen=True, slots=True)
class LabSession:
    session_id: str
    scenario_id: str


@dataclass(frozen=True, slots=True)
class CheckObservation:
    check_id: str
    passed: bool
    message: str
    error: bool = False


class ScenarioDriver(Protocol):
    """Infrastructure boundary used by both the product and its verification harness."""

    def provision(self, manifest: ScenarioManifest) -> LabSession: ...

    def run_check(self, session: LabSession, check: CheckSpec) -> CheckObservation: ...

    def apply_reference_solution(self, session: LabSession, manifest: ScenarioManifest) -> None: ...

    def reboot(self, session: LabSession, hosts: tuple[str, ...]) -> None: ...

    def reset(self, session: LabSession, manifest: ScenarioManifest) -> LabSession: ...

    def destroy(self, session: LabSession) -> None: ...
