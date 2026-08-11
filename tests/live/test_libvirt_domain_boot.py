from __future__ import annotations

import os
from pathlib import Path

import pytest

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
from sysadmin_lab.application.checking import CheckEngine
from sysadmin_lab.application.guest_execution import GuestEndpoint, GuestReadiness
from sysadmin_lab.application.machines import DomainLeaseReadiness
from sysadmin_lab.application.resources import ResourceManager
from sysadmin_lab.application.session_artifacts import SessionArtifactBuilder, SubprocessRunner
from sysadmin_lab.application.sessions import SessionCoordinator
from sysadmin_lab.application.vm_sessions import SingleHostVmSessionService
from sysadmin_lab.domain.models import CheckKind, CheckSpec
from sysadmin_lab.domain.sessions import SessionStatus

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    os.environ.get("LAL_RUN_DOMAIN_LIVE") != "1",
    reason="set LAL_RUN_DOMAIN_LIVE=1 and LAL_BASE_IMAGE to boot a disposable guest",
)
def test_guarded_rocky_session_service_lifecycle() -> None:
    base_image_value = os.environ.get("LAL_BASE_IMAGE")
    if not base_image_value:
        pytest.fail("LAL_BASE_IMAGE must identify the verified Rocky cloud image")
    base_image = Path(base_image_value).resolve()
    runtime_root = Path("runtime/live-domain-test").resolve()
    state_path = runtime_root / "state.db"

    with (
        LibvirtGateway.connect() as gateway,
        SqliteResourceRegistry(state_path) as resource_registry,
        SqliteSessionRepository(state_path) as session_repository,
        SqliteSessionMachineRepository(state_path) as machine_repository,
    ):
        executor = SshGuestExecutor(BoundedSubprocessRunner())
        service = SingleHostVmSessionService(
            sessions=SessionCoordinator(session_repository),
            machines=machine_repository,
            resources=ResourceManager(gateway, resource_registry),
            artifacts=SessionArtifactBuilder(runtime_root, SubprocessRunner()),
            leases=DomainLeaseReadiness(gateway),
            guest_readiness=GuestReadiness(executor),
            guest_executor=executor,
        )
        provisioned = service.provision(
            scenario_id="live-base-smoke",
            host_name="node1",
            base_image=base_image,
        )
        try:
            assert provisioned.state.status is SessionStatus.READY
            assert provisioned.machine.address is not None
            endpoint = GuestEndpoint(
                provisioned.machine.address,
                provisioned.machine.username,
                provisioned.machine.private_key,
            )
            checks = (
                CheckSpec(
                    check_id="rocky-release",
                    kind=CheckKind.COMMAND,
                    target="node1",
                    description="The guest is Rocky Linux 10.2.",
                    parameters={
                        "arguments": ["cat", "/etc/rocky-release"],
                        "stdout_contains": "Rocky Linux release 10.2",
                    },
                ),
                CheckSpec(
                    check_id="release-file",
                    kind=CheckKind.FILE,
                    target="node1",
                    description="The release file has its expected state.",
                    parameters={
                        "path": "/etc/rocky-release",
                        "kind": "file",
                        "owner": "root",
                        "group": "root",
                        "mode": "0644",
                        "contains": "Rocky Linux release 10.2",
                    },
                ),
                CheckSpec(
                    check_id="guest-agent",
                    kind=CheckKind.SERVICE,
                    target="node1",
                    description="The guest agent is active.",
                    parameters={"name": "qemu-guest-agent.service", "active": True},
                ),
                CheckSpec(
                    check_id="deliberate-failure",
                    kind=CheckKind.FILE,
                    target="node1",
                    description="A deliberately absent file proves failure reporting.",
                    required=False,
                    parameters={"path": "/definitely-not-present"},
                ),
            )
            report = CheckEngine(
                (
                    CommandCheckProvider(executor),
                    FileCheckProvider(executor),
                    ServiceCheckProvider(executor),
                )
            ).run(checks, {"node1": endpoint})
            assert report.required_passed
            assert not report.has_errors
            assert [result.observation.passed for result in report.results] == [
                True,
                True,
                True,
                False,
            ]
        finally:
            destroyed = service.destroy(provisioned.state.session_id)

        assert destroyed.status is SessionStatus.DESTROYED
        assert (
            gateway.find(provisioned.machine.identity.kind, provisioned.machine.identity.name)
            is None
        )
        assert machine_repository.list(provisioned.state.session_id) == ()
