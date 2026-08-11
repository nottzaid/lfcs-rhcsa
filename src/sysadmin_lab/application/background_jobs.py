from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import Any
from uuid import UUID, uuid4


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    job_id: UUID
    kind: str
    resource_key: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    result: dict[str, Any] | None = None
    error: str | None = None


class JobNotFoundError(LookupError):
    pass


class JobConflictError(RuntimeError):
    def __init__(self, job_id: UUID) -> None:
        super().__init__(f"an operation is already active: {job_id}")
        self.job_id = job_id


class BackgroundJobQueue:
    """One-worker queue protecting local libvirt mutations from accidental races."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lab-job")
        self._lock = Lock()
        self._jobs: dict[UUID, JobSnapshot] = {}
        self._active: dict[str, UUID] = {}
        self._closed = False

    def submit(
        self,
        *,
        kind: str,
        resource_key: str,
        operation: Callable[[], dict[str, Any]],
    ) -> JobSnapshot:
        now = datetime.now(UTC)
        with self._lock:
            if self._closed:
                raise RuntimeError("job queue is closed")
            active_id = self._active.get(resource_key)
            if active_id is not None:
                raise JobConflictError(active_id)
            snapshot = JobSnapshot(
                job_id=uuid4(),
                kind=kind,
                resource_key=resource_key,
                status=JobStatus.QUEUED,
                created_at=now,
                updated_at=now,
            )
            self._jobs[snapshot.job_id] = snapshot
            self._active[resource_key] = snapshot.job_id
            self._executor.submit(self._run, snapshot.job_id, operation)
            return snapshot

    def get(self, job_id: UUID) -> JobSnapshot:
        with self._lock:
            try:
                return self._jobs[job_id]
            except KeyError as exc:
                raise JobNotFoundError(f"job does not exist: {job_id}") from exc

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _run(self, job_id: UUID, operation: Callable[[], dict[str, Any]]) -> None:
        self._update(job_id, status=JobStatus.RUNNING)
        try:
            result = operation()
        except Exception as exc:  # the UI must retain a terminal job state
            self._update(job_id, status=JobStatus.FAILED, error=str(exc))
        else:
            self._update(job_id, status=JobStatus.SUCCEEDED, result=result)
        finally:
            with self._lock:
                snapshot = self._jobs[job_id]
                if self._active.get(snapshot.resource_key) == job_id:
                    del self._active[snapshot.resource_key]

    def _update(
        self,
        job_id: UUID,
        *,
        status: JobStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            current = self._jobs[job_id]
            self._jobs[job_id] = replace(
                current,
                status=status,
                updated_at=datetime.now(UTC),
                result=result,
                error=error,
            )
