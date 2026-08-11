from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sysadmin_lab.application.guest_execution import GuestEndpoint
from sysadmin_lab.application.ports import CheckObservation
from sysadmin_lab.domain.models import CheckKind, CheckSpec


class CheckProvider(Protocol):
    kind: CheckKind

    def evaluate(self, check: CheckSpec, endpoint: GuestEndpoint) -> CheckObservation: ...


class CheckProviderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CheckResult:
    check: CheckSpec
    observation: CheckObservation


@dataclass(frozen=True, slots=True)
class CheckReport:
    results: tuple[CheckResult, ...]

    @property
    def required_passed(self) -> bool:
        return all(result.observation.passed for result in self.results if result.check.required)

    @property
    def has_errors(self) -> bool:
        return any(result.observation.error for result in self.results)

    @property
    def earned_weight(self) -> int:
        return sum(result.check.weight for result in self.results if result.observation.passed)

    @property
    def available_weight(self) -> int:
        return sum(result.check.weight for result in self.results)


class CheckEngine:
    def __init__(self, providers: tuple[CheckProvider, ...]) -> None:
        self._providers: dict[CheckKind, CheckProvider] = {}
        for provider in providers:
            if provider.kind in self._providers:
                raise ValueError(f"duplicate check provider: {provider.kind}")
            self._providers[provider.kind] = provider

    def run(
        self,
        checks: tuple[CheckSpec, ...],
        endpoints: dict[str, GuestEndpoint],
    ) -> CheckReport:
        results: list[CheckResult] = []
        for check in checks:
            endpoint = endpoints.get(check.target)
            if endpoint is None:
                observation = CheckObservation(
                    check.check_id,
                    False,
                    f"checker has no reachable endpoint for {check.target}",
                    error=True,
                )
            else:
                provider = self._providers.get(check.kind)
                if provider is None:
                    observation = CheckObservation(
                        check.check_id,
                        False,
                        f"no provider is registered for {check.kind}",
                        error=True,
                    )
                else:
                    try:
                        observation = provider.evaluate(check, endpoint)
                    except Exception as exc:
                        observation = CheckObservation(
                            check.check_id,
                            False,
                            f"checker error: {exc}",
                            error=True,
                        )
            if observation.check_id != check.check_id:
                raise CheckProviderError(
                    f"provider returned {observation.check_id} for {check.check_id}"
                )
            results.append(CheckResult(check, observation))
        return CheckReport(tuple(results))
