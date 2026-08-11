from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

import pytest

from sysadmin_lab.adapters.ssh_guest import BoundedSubprocessRunner, SshGuestExecutor
from sysadmin_lab.application.actions import ActionRunner
from sysadmin_lab.application.guest_execution import GuestEndpoint
from sysadmin_lab.catalog import load_action_manifest, load_catalog
from sysadmin_lab.composition import open_vm_runtime
from sysadmin_lab.domain.sessions import SessionStatus

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    os.environ.get("LAL_RUN_SCENARIO_LIVE") != "1",
    reason="set LAL_RUN_SCENARIO_LIVE=1 and LAL_BASE_IMAGE to replay a disposable scenario",
)
def test_persistent_kernel_tuning_broken_solved_rebooted_reset_contract() -> None:
    base_image_value = os.environ.get("LAL_BASE_IMAGE")
    if not base_image_value:
        pytest.fail("LAL_BASE_IMAGE must identify the verified Rocky cloud image")
    root = Path(__file__).parents[2]
    base_image = Path(base_image_value).resolve()
    manifest = next(
        item
        for item in load_catalog(root / "scenarios")
        if item.scenario_id == "persistent-kernel-tuning"
    )
    setup = load_action_manifest(root / "scenarios" / manifest.setup)
    solution = load_action_manifest(root / "scenarios" / manifest.reference_solution)
    alternate = load_action_manifest(root / "scenarios" / manifest.alternate_solutions[0])
    executor = SshGuestExecutor(BoundedSubprocessRunner())
    actions = ActionRunner(executor)
    active_session: UUID | None = None

    with open_vm_runtime(root / "runtime" / "scenario-replay") as runtime:
        try:
            first = runtime.vm_sessions.provision(
                scenario_id=manifest.scenario_id,
                host_name="node2",
                base_image=base_image,
                memory_mib=manifest.topology.hosts[0].memory_mib,
                vcpus=manifest.topology.hosts[0].vcpus,
            )
            active_session = first.state.session_id
            assert first.machine.address is not None
            endpoint = GuestEndpoint(
                first.machine.address,
                first.machine.username,
                first.machine.private_key,
            )
            actions.run(setup, {"node2": endpoint})
            initial = runtime.checks.run(active_session, manifest)
            assert not initial.required_passed
            assert not initial.has_errors

            actions.run(solution, {"node2": endpoint})
            solved = runtime.checks.run(active_session, manifest)
            assert solved.required_passed
            assert not solved.has_errors

            runtime.vm_sessions.reboot(active_session, ("node2",))
            rebooted = runtime.checks.run(active_session, manifest)
            assert rebooted.required_passed
            assert not rebooted.has_errors

            assert runtime.vm_sessions.destroy(active_session).status is SessionStatus.DESTROYED
            active_session = None

            reset = runtime.vm_sessions.provision(
                scenario_id=manifest.scenario_id,
                host_name="node2",
                base_image=base_image,
                memory_mib=manifest.topology.hosts[0].memory_mib,
                vcpus=manifest.topology.hosts[0].vcpus,
            )
            active_session = reset.state.session_id
            assert reset.machine.address is not None
            reset_endpoint = GuestEndpoint(
                reset.machine.address,
                reset.machine.username,
                reset.machine.private_key,
            )
            actions.run(setup, {"node2": reset_endpoint})
            reset_report = runtime.checks.run(active_session, manifest)
            assert not reset_report.required_passed
            assert not reset_report.has_errors

            actions.run(alternate, {"node2": reset_endpoint})
            alternate_solved = runtime.checks.run(active_session, manifest)
            assert alternate_solved.required_passed
            assert not alternate_solved.has_errors

            runtime.vm_sessions.reboot(active_session, ("node2",))
            alternate_rebooted = runtime.checks.run(active_session, manifest)
            assert alternate_rebooted.required_passed
            assert not alternate_rebooted.has_errors
        finally:
            if active_session is not None:
                state = runtime.sessions.get(active_session)
                if state.status in {SessionStatus.READY, SessionStatus.FAILED}:
                    runtime.vm_sessions.destroy(active_session)
