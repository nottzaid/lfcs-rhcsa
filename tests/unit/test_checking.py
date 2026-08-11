from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from sysadmin_lab.application.checking import CheckEngine, CheckProviderError
from sysadmin_lab.application.guest_execution import GuestEndpoint
from sysadmin_lab.application.ports import CheckObservation
from sysadmin_lab.domain.models import CheckKind, CheckSpec


def check(check_id: str, *, required: bool = True, weight: int = 1) -> CheckSpec:
    return CheckSpec(
        check_id=check_id,
        kind=CheckKind.COMMAND,
        target="node1",
        description="state",
        required=required,
        weight=weight,
        parameters={"arguments": ["true"]},
    )


@dataclass
class Provider:
    kind = CheckKind.COMMAND
    passed: bool = True
    error: bool = False

    def evaluate(self, spec: CheckSpec, endpoint: GuestEndpoint) -> CheckObservation:
        return CheckObservation(spec.check_id, self.passed, "result", self.error)


def endpoint(tmp_path: Path) -> GuestEndpoint:
    return GuestEndpoint("192.0.2.10", "labadmin", (tmp_path / "key").resolve())


def test_engine_scores_and_requires_only_required_checks(tmp_path: Path) -> None:
    engine = CheckEngine((Provider(),))
    report = engine.run(
        (check("required", weight=3), check("optional", required=False, weight=2)),
        {"node1": endpoint(tmp_path)},
    )
    assert report.required_passed
    assert not report.has_errors
    assert (report.earned_weight, report.available_weight) == (5, 5)


def test_engine_reports_missing_target_provider_and_provider_errors(tmp_path: Path) -> None:
    missing_target = CheckEngine((Provider(),)).run((check("one"),), {})
    assert missing_target.has_errors and not missing_target.required_passed

    missing_provider_check = check("two").model_copy(update={"kind": CheckKind.FILE})
    missing_provider = CheckEngine((Provider(),)).run(
        (missing_provider_check,), {"node1": endpoint(tmp_path)}
    )
    assert missing_provider.has_errors

    class BrokenProvider(Provider):
        def evaluate(self, spec: CheckSpec, endpoint: GuestEndpoint) -> CheckObservation:
            raise RuntimeError("provider broke")

    broken = CheckEngine((BrokenProvider(),)).run((check("three"),), {"node1": endpoint(tmp_path)})
    assert broken.has_errors and "provider broke" in broken.results[0].observation.message


def test_engine_rejects_duplicate_or_wrong_observation_provider(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        CheckEngine((Provider(), Provider()))

    class WrongProvider(Provider):
        def evaluate(self, spec: CheckSpec, endpoint: GuestEndpoint) -> CheckObservation:
            return CheckObservation("wrong", True, "wrong")

    with pytest.raises(CheckProviderError, match="returned wrong"):
        CheckEngine((WrongProvider(),)).run((check("expected"),), {"node1": endpoint(tmp_path)})
