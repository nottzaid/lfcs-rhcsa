from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from sysadmin_lab.domain.resources import NAME_COMPONENT, ResourceIdentity, ResourceKind


@dataclass(frozen=True, slots=True)
class SessionMachine:
    session_id: UUID
    host_name: str
    identity: ResourceIdentity
    username: str
    password: str = field(repr=False)
    private_key: Path
    address: str | None = None

    def __post_init__(self) -> None:
        if not NAME_COMPONENT.fullmatch(self.host_name):
            raise ValueError(f"invalid host_name: {self.host_name}")
        if self.identity.kind is not ResourceKind.DOMAIN:
            raise ValueError("session machines require a domain identity")
        if self.identity.session_id != self.session_id:
            raise ValueError("machine and resource session identifiers differ")
        if self.identity.role != self.host_name:
            raise ValueError("machine host name and resource role differ")
        if not self.username or not self.password:
            raise ValueError("machine access credentials must not be empty")
        if not self.private_key.is_absolute():
            raise ValueError("machine private key path must be absolute")
        if self.address is not None and (
            not self.address or any(character.isspace() for character in self.address)
        ):
            raise ValueError("machine address is invalid")
