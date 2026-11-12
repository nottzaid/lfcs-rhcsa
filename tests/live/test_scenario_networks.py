from __future__ import annotations

import os
from pathlib import Path

import pytest

from sysadmin_lab.application.verification import ScenarioVerifier, VerificationPhase
from sysadmin_lab.application.vm_verification import VmScenarioDriver
from sysadmin_lab.catalog import load_catalog
from sysadmin_lab.composition import open_vm_runtime
from sysadmin_lab.domain.resources import ResourceKind

pytestmark = pytest.mark.live
ROOT = Path(__file__).parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "network-probe"


@pytest.mark.skipif(
    os.environ.get("LAL_RUN_SCENARIO_LIVE") != "1",
    reason="set LAL_RUN_SCENARIO_LIVE=1 and LAL_BASE_IMAGE to boot disposable machines",
)
def test_isolated_networks_named_nics_and_rejected_solutions_on_real_machines() -> None:
    """Two guests on an isolated segment, graded by the real checker, rebooted, and reset.

    Proves that scenario NICs get their manifest names on first and later boots without
    NetworkManager claiming them, that guests reach each other only through that segment,
    that the host-owned check library works on the guest, that a runtime-only fix is
    exposed by the reboot, and that teardown leaves no network behind.
    """
    base_image = Path(os.environ["LAL_BASE_IMAGE"]).resolve()
    manifest = load_catalog(FIXTURES)[0]
    runtime_root = ROOT / "runtime" / "platform-replay"
    with open_vm_runtime(runtime_root) as runtime:
        driver = VmScenarioDriver(
            scenario_directory=FIXTURES,
            base_images={manifest.topology.hosts[0].image: base_image},
            sessions=runtime.sessions,
            machines=runtime.machines,
            vm_sessions=runtime.vm_sessions,
            checks=runtime.checks,
            scenarios=runtime.scenarios,
            actions=runtime.actions,
        )
        report = ScenarioVerifier(driver).verify(manifest)
        sessions = runtime.sessions.list_all(limit=10)
        leftovers = [
            identity
            for state in sessions
            for identity in runtime.vm_sessions.owned_networks(state.session_id)
        ]

    failures = [
        (phase.phase.value, phase.solution, observation.check_id, observation.message)
        for phase in report.phases
        if not phase.accepted
        for observation in phase.observations
        if observation.error or phase.expected is not observation.passed
    ]
    assert report.passed, failures
    assert VerificationPhase.REJECTED_REBOOTED in {phase.phase for phase in report.phases}
    assert leftovers == []
    assert all(identity.kind is ResourceKind.NETWORK for identity in leftovers)
