from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Track(StrEnum):
    LINUX_CORE = "linux-core"
    LFCS = "lfcs"
    RHCSA_10 = "rhcsa-10"
    PROFESSIONAL = "professional"


class SourceKind(StrEnum):
    SCOPE = "scope"
    UPSTREAM = "upstream"
    DISTRIBUTION = "distribution"
    MAN_PAGE = "man-page"
    INSPIRATION = "inspiration"


class CheckKind(StrEnum):
    COMMAND = "command"
    FILE = "file"
    SERVICE = "service"


class ScenarioStatus(StrEnum):
    DRAFT = "draft"
    VERIFIED = "verified"


class ScenarioTaskType(StrEnum):
    FIX = "fix"
    CONFIGURE = "configure"
    TROUBLESHOOT = "troubleshoot"
    BUILD = "build"


class ScenarioDifficulty(StrEnum):
    FOUNDATION = "foundation"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXAM = "exam"

    @property
    def rank(self) -> int:
        return list(ScenarioDifficulty).index(self)


class ObjectiveRef(StrictModel):
    track: Track
    version: str = Field(min_length=1)
    objective_id: str = Field(min_length=1)
    title: str = Field(min_length=1)


class SourceRef(StrictModel):
    kind: SourceKind
    title: str = Field(min_length=1)
    url: HttpUrl | None = None
    man_page: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.+:-]+\([0-9a-z]+\)$")

    @model_validator(mode="after")
    def require_locator(self) -> Self:
        if self.url is None and self.man_page is None:
            raise ValueError("a source requires either url or man_page")
        return self


class DiskSpec(StrictModel):
    name: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    size_mib: int = Field(ge=64)
    role: str = Field(min_length=1)


INTERFACE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]{0,14}$")


class NicSpec(StrictModel):
    """An extra NIC on a scenario network, named predictably inside the guest."""

    network: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    name: str = Field(pattern=INTERFACE_NAME_PATTERN.pattern)

    @model_validator(mode="after")
    def avoid_kernel_names(self) -> Self:
        if self.name.startswith(("eth", "en", "wl", "lo")):
            raise ValueError(f"interface name {self.name} collides with kernel naming schemes")
        return self


class HostSpec(StrictModel):
    name: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    image: str = Field(min_length=1)
    memory_mib: int = Field(default=1024, ge=1024)
    vcpus: int = Field(default=1, ge=1)
    nics: tuple[NicSpec, ...] = ()
    disks: tuple[DiskSpec, ...] = ()

    @model_validator(mode="after")
    def unique_devices(self) -> Self:
        nic_names = [nic.name for nic in self.nics]
        if len(nic_names) != len(set(nic_names)):
            raise ValueError(f"interface names on {self.name} must be unique")
        disk_names = [disk.name for disk in self.disks]
        if len(disk_names) != len(set(disk_names)):
            raise ValueError(f"disk names on {self.name} must be unique")
        return self


class NetworkSpec(StrictModel):
    """An isolated layer-2 segment: no host address, no DHCP, no route off the segment."""

    name: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    cidr: str | None = None


class TopologySpec(StrictModel):
    hosts: tuple[HostSpec, ...] = Field(min_length=1)
    networks: tuple[NetworkSpec, ...] = ()

    @model_validator(mode="after")
    def references_declared_networks(self) -> Self:
        names = [host.name for host in self.hosts]
        if len(names) != len(set(names)):
            raise ValueError("topology host names must be unique")
        network_names = [network.name for network in self.networks]
        if len(network_names) != len(set(network_names)):
            raise ValueError("topology network names must be unique")
        declared = set(network_names)
        referenced = {nic.network for host in self.hosts for nic in host.nics}
        missing = sorted(referenced - declared)
        if missing:
            raise ValueError(f"undeclared topology networks: {', '.join(missing)}")
        return self


class CheckSpec(StrictModel):
    check_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    kind: CheckKind
    target: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    description: str = Field(min_length=1)
    required: bool = True
    weight: int = Field(default=1, ge=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class PersistenceSpec(StrictModel):
    reboot: bool = False
    hosts: tuple[str, ...] = ()


class ScenarioManifest(StrictModel):
    schema_version: int = Field(default=1, ge=1)
    scenario_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    version: int = Field(ge=1)
    status: ScenarioStatus = ScenarioStatus.DRAFT
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    estimated_minutes: int = Field(ge=5, le=180)
    task_type: ScenarioTaskType
    difficulty: ScenarioDifficulty
    task: str = Field(min_length=1)
    requirements: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()
    debrief: str | None = Field(default=None, min_length=1)
    objectives: tuple[ObjectiveRef, ...] = Field(min_length=1)
    sources: tuple[SourceRef, ...] = Field(min_length=1)
    topology: TopologySpec
    checks: tuple[CheckSpec, ...] = Field(min_length=1)
    persistence: PersistenceSpec = PersistenceSpec()
    setup: str = Field(min_length=1)
    reference_solution: str = Field(min_length=1)
    alternate_solutions: tuple[str, ...] = ()
    rejected_solutions: tuple[str, ...] = ()

    @property
    def integrated(self) -> bool:
        """Exam-difficulty scenarios are multi-competency incidents rather than focused drills."""
        return self.difficulty is ScenarioDifficulty.EXAM

    @property
    def primary_objective(self) -> ObjectiveRef:
        return next(
            (objective for objective in self.objectives if objective.track is Track.LFCS),
            self.objectives[0],
        )

    @property
    def reboot_hosts(self) -> tuple[str, ...]:
        """Hosts whose persistence is proven by rebooting them, in topology order."""
        if not self.persistence.reboot:
            return ()
        return self.persistence.hosts or tuple(host.name for host in self.topology.hosts)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        host_names = {host.name for host in self.topology.hosts}
        unknown_targets = sorted({check.target for check in self.checks} - host_names)
        if unknown_targets:
            raise ValueError(f"check targets are not topology hosts: {', '.join(unknown_targets)}")

        check_ids = [check.check_id for check in self.checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("check identifiers must be unique")

        if self.persistence.reboot:
            reboot_hosts = set(self.persistence.hosts) or host_names
            unknown_reboot_hosts = sorted(reboot_hosts - host_names)
            if unknown_reboot_hosts:
                raise ValueError(
                    f"persistence hosts are not topology hosts: {', '.join(unknown_reboot_hosts)}"
                )
        elif self.persistence.hosts:
            raise ValueError("persistence hosts require reboot=true")

        return self
