from __future__ import annotations

from uuid import UUID

from sysadmin_lab.application.checking import CheckEngine, CheckReport
from sysadmin_lab.application.guest_execution import GuestEndpoint
from sysadmin_lab.application.machines import SessionMachineRepository
from sysadmin_lab.application.placeholders import render_host_addresses
from sysadmin_lab.application.sessions import SessionCoordinator
from sysadmin_lab.domain.models import ScenarioManifest
from sysadmin_lab.domain.sessions import SessionStatus


class SessionScenarioMismatchError(ValueError):
    pass


class SessionCheckService:
    def __init__(
        self,
        sessions: SessionCoordinator,
        machines: SessionMachineRepository,
        engine: CheckEngine,
    ) -> None:
        self._sessions = sessions
        self._machines = machines
        self._engine = engine

    def run(self, session_id: UUID, manifest: ScenarioManifest) -> CheckReport:
        current = self._sessions.get(session_id)
        if current.scenario_id != manifest.scenario_id:
            raise SessionScenarioMismatchError(
                f"session runs {current.scenario_id}, not {manifest.scenario_id}"
            )
        self._sessions.transition(session_id, SessionStatus.CHECKING)
        try:
            endpoints = {
                machine.host_name: GuestEndpoint(
                    machine.address,
                    machine.username,
                    machine.private_key,
                )
                for machine in self._machines.list(session_id)
                if machine.address is not None
            }
            checks = tuple(
                check.model_copy(
                    update={"parameters": render_host_addresses(check.parameters, endpoints)}
                )
                for check in manifest.checks
            )
            report = self._engine.run(checks, endpoints)
        except Exception as exc:
            self._sessions.transition(session_id, SessionStatus.FAILED, error=str(exc))
            raise
        self._sessions.transition(session_id, SessionStatus.READY)
        return report
