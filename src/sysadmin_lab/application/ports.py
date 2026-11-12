from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sysadmin_lab.domain.models import ScenarioManifest


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

    def run_checks(
        self, session: LabSession, manifest: ScenarioManifest
    ) -> tuple[CheckObservation, ...]: ...

    def apply_solution(
        self, session: LabSession, manifest: ScenarioManifest, solution: str
    ) -> None: ...

    def reboot(self, session: LabSession, hosts: tuple[str, ...]) -> None: ...

    def reset(self, session: LabSession, manifest: ScenarioManifest) -> LabSession: ...

    def destroy(self, session: LabSession) -> None: ...
