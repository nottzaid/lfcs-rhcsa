from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from types import TracebackType

from sysadmin_lab.adapters.guest_checks import (
    CommandCheckProvider,
    FileCheckProvider,
    ServiceCheckProvider,
)
from sysadmin_lab.adapters.libvirt_gateway import LibvirtGateway
from sysadmin_lab.adapters.sqlite_machines import SqliteSessionMachineRepository
from sysadmin_lab.adapters.sqlite_registry import SqliteResourceRegistry
from sysadmin_lab.adapters.sqlite_sessions import SqliteSessionRepository
from sysadmin_lab.adapters.ssh_guest import BoundedSubprocessRunner, SshGuestExecutor
from sysadmin_lab.application.actions import ActionRunner
from sysadmin_lab.application.checking import CheckEngine
from sysadmin_lab.application.guest_execution import GuestReadiness
from sysadmin_lab.application.machines import DomainLeaseReadiness
from sysadmin_lab.application.resources import ResourceManager
from sysadmin_lab.application.scenario_sessions import ScenarioSessionService
from sysadmin_lab.application.session_artifacts import SessionArtifactBuilder, SubprocessRunner
from sysadmin_lab.application.session_checks import SessionCheckService
from sysadmin_lab.application.sessions import SessionCoordinator
from sysadmin_lab.application.vm_sessions import SingleHostVmSessionService


class VmRuntime:
    """Composition root for CLI and, later, the web process."""

    sessions: SessionCoordinator
    machines: SqliteSessionMachineRepository
    vm_sessions: SingleHostVmSessionService
    checks: SessionCheckService
    scenarios: ScenarioSessionService

    def __init__(self, runtime_root: Path) -> None:
        self._runtime_root = runtime_root.resolve()
        self._stack = ExitStack()

    def __enter__(self) -> VmRuntime:
        self._runtime_root.mkdir(parents=True, exist_ok=True)
        state_path = self._runtime_root / "state" / "lab.db"
        gateway = self._stack.enter_context(LibvirtGateway.connect())
        resources = self._stack.enter_context(SqliteResourceRegistry(state_path))
        session_repository = self._stack.enter_context(SqliteSessionRepository(state_path))
        self.machines = self._stack.enter_context(SqliteSessionMachineRepository(state_path))
        self.sessions = SessionCoordinator(session_repository)
        executor = SshGuestExecutor(BoundedSubprocessRunner())
        self.vm_sessions = SingleHostVmSessionService(
            sessions=self.sessions,
            machines=self.machines,
            resources=ResourceManager(gateway, resources),
            artifacts=SessionArtifactBuilder(self._runtime_root, SubprocessRunner()),
            leases=DomainLeaseReadiness(gateway),
            guest_readiness=GuestReadiness(executor),
            guest_executor=executor,
        )
        self.checks = SessionCheckService(
            self.sessions,
            self.machines,
            CheckEngine(
                (
                    CommandCheckProvider(executor),
                    FileCheckProvider(executor),
                    ServiceCheckProvider(executor),
                )
            ),
        )
        self.scenarios = ScenarioSessionService(
            sessions=self.sessions,
            machines=self.machines,
            vm_sessions=self.vm_sessions,
            checks=self.checks,
            actions=ActionRunner(executor),
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stack.__exit__(exc_type, exc, traceback)


def open_vm_runtime(runtime_root: Path) -> VmRuntime:
    return VmRuntime(runtime_root)
