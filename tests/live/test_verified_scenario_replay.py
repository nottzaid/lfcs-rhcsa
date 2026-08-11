from __future__ import annotations

import os
from pathlib import Path

import pytest

from sysadmin_lab.application.verification import ScenarioVerifier
from sysadmin_lab.application.vm_verification import VmScenarioDriver
from sysadmin_lab.catalog import load_catalog
from sysadmin_lab.composition import open_vm_runtime
from sysadmin_lab.domain.models import ScenarioStatus

pytestmark = pytest.mark.live
ROOT = Path(__file__).parents[2]
ALL_VERIFIED_SCENARIOS = tuple(
    manifest.scenario_id
    for manifest in load_catalog(ROOT / "scenarios")
    if manifest.status is ScenarioStatus.VERIFIED
)
SHARD_TOTAL = int(os.environ.get("LAL_LIVE_SHARD_TOTAL", "1"))
SHARD_INDEX = int(os.environ.get("LAL_LIVE_SHARD_INDEX", "0"))
if SHARD_TOTAL < 1 or not 0 <= SHARD_INDEX < SHARD_TOTAL:
    raise ValueError("live shard settings require 0 <= LAL_LIVE_SHARD_INDEX < LAL_LIVE_SHARD_TOTAL")
VERIFIED_SCENARIOS = tuple(
    scenario_id
    for index, scenario_id in enumerate(ALL_VERIFIED_SCENARIOS)
    if index % SHARD_TOTAL == SHARD_INDEX
)


@pytest.mark.skipif(
    os.environ.get("LAL_RUN_SCENARIO_LIVE") != "1",
    reason="set LAL_RUN_SCENARIO_LIVE=1 and LAL_BASE_IMAGE to replay disposable scenarios",
)
@pytest.mark.parametrize("scenario_id", VERIFIED_SCENARIOS)
def test_verified_scenario_acceptance_contract(scenario_id: str) -> None:
    base_image_value = os.environ.get("LAL_BASE_IMAGE")
    if not base_image_value:
        pytest.fail("LAL_BASE_IMAGE must identify the verified Rocky cloud image")
    manifest = next(
        item for item in load_catalog(ROOT / "scenarios") if item.scenario_id == scenario_id
    )
    images = {manifest.topology.hosts[0].image: Path(base_image_value).resolve()}

    runtime_root = ROOT / "runtime" / "scenario-replay" / f"shard-{SHARD_INDEX}"
    with open_vm_runtime(runtime_root) as runtime:
        driver = VmScenarioDriver(
            scenario_directory=ROOT / "scenarios",
            base_images=images,
            sessions=runtime.sessions,
            machines=runtime.machines,
            vm_sessions=runtime.vm_sessions,
            checks=runtime.checks,
            scenarios=runtime.scenarios,
            actions=runtime.actions,
        )
        report = ScenarioVerifier(driver).verify(manifest)

    assert report.passed
