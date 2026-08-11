from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from sysadmin_lab.domain.session_machines import SessionMachine


class SessionMachineConflictError(RuntimeError):
    pass


class SessionMachineRepository(Protocol):
    def add(self, machine: SessionMachine) -> None: ...

    def get(self, session_id: UUID, host_name: str) -> SessionMachine | None: ...

    def list(self, session_id: UUID) -> tuple[SessionMachine, ...]: ...

    def update_address(self, machine: SessionMachine, address: str) -> SessionMachine: ...

    def remove(self, machine: SessionMachine) -> None: ...


class DomainAddressSource(Protocol):
    def domain_ipv4_addresses(self, name: str) -> tuple[str, ...]: ...


class DomainLeaseTimeout(TimeoutError):
    pass


class DomainLeaseReadiness:
    def __init__(
        self,
        source: DomainAddressSource,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._source = source
        self._sleep = sleep

    def wait(
        self,
        domain_name: str,
        *,
        attempts: int = 60,
        interval_seconds: float = 2.0,
    ) -> str:
        if attempts < 1:
            raise ValueError("lease attempts must be positive")
        for attempt in range(attempts):
            addresses = self._source.domain_ipv4_addresses(domain_name)
            if addresses:
                return addresses[0]
            if attempt + 1 < attempts:
                self._sleep(interval_seconds)
        raise DomainLeaseTimeout(
            f"domain received no IPv4 DHCP lease after {attempts} attempts: {domain_name}"
        )
