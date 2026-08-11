from __future__ import annotations

from pydantic import Field

from sysadmin_lab.domain.models import IDENTIFIER_PATTERN, StrictModel


class GuestAction(StrictModel):
    action_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    target: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    arguments: tuple[str, ...] = Field(min_length=1)
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    expected_exit_codes: tuple[int, ...] = (0,)


class ActionManifest(StrictModel):
    schema_version: int = Field(default=1, ge=1)
    actions: tuple[GuestAction, ...] = Field(min_length=1)
