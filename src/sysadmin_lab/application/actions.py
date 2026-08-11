from __future__ import annotations

import re
from dataclasses import dataclass

from sysadmin_lab.application.guest_execution import GuestEndpoint, GuestExecutor
from sysadmin_lab.domain.actions import ActionManifest


class ActionExecutionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ActionResult:
    action_id: str
    exit_code: int


class ActionRunner:
    """Runs host-owned setup and reference actions; never learner commands."""

    def __init__(self, executor: GuestExecutor) -> None:
        self._executor = executor

    def run(
        self,
        manifest: ActionManifest,
        endpoints: dict[str, GuestEndpoint],
    ) -> tuple[ActionResult, ...]:
        completed: list[ActionResult] = []
        for action in manifest.actions:
            endpoint = endpoints.get(action.target)
            if endpoint is None:
                raise ActionExecutionError(
                    f"action {action.action_id} has no endpoint for {action.target}"
                )
            result = self._executor.run(
                endpoint,
                self._render_arguments(action.arguments, endpoints),
                timeout_seconds=action.timeout_seconds,
            )
            if result.stdout_truncated or result.stderr_truncated:
                raise ActionExecutionError(f"action {action.action_id} exceeded its output limit")
            if result.exit_code not in action.expected_exit_codes:
                detail = result.stderr.strip() or result.stdout.strip()
                raise ActionExecutionError(
                    f"action {action.action_id} exited {result.exit_code}: {detail}"
                )
            completed.append(ActionResult(action.action_id, result.exit_code))
        return tuple(completed)

    @staticmethod
    def _render_arguments(
        arguments: tuple[str, ...], endpoints: dict[str, GuestEndpoint]
    ) -> tuple[str, ...]:
        pattern = re.compile(r"\{\{host\.([a-z][a-z0-9-]*)\.address\}\}")

        def replace(match: re.Match[str]) -> str:
            host = match.group(1)
            try:
                return endpoints[host].host
            except KeyError as exc:
                raise ActionExecutionError(f"action references unknown host {host}") from exc

        return tuple(pattern.sub(replace, argument) for argument in arguments)
