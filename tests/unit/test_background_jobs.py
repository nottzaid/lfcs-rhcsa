from __future__ import annotations

from threading import Event

import pytest

from sysadmin_lab.application.background_jobs import (
    BackgroundJobQueue,
    JobConflictError,
    JobNotFoundError,
    JobStatus,
)


def test_background_job_records_success_and_failure() -> None:
    queue = BackgroundJobQueue()
    release = Event()
    succeeded = queue.submit(
        kind="start", resource_key="scenario:test", operation=lambda: {"ready": True}
    )
    assert release.wait(0.01) is False
    queue.close()
    assert queue.get(succeeded.job_id).status is JobStatus.SUCCEEDED
    assert queue.get(succeeded.job_id).result == {"ready": True}

    failed_queue = BackgroundJobQueue()

    def fail() -> dict[str, object]:
        raise RuntimeError("guest did not boot")

    failed = failed_queue.submit(kind="start", resource_key="scenario:bad", operation=fail)
    failed_queue.close()
    assert failed_queue.get(failed.job_id).status is JobStatus.FAILED
    assert failed_queue.get(failed.job_id).error == "guest did not boot"


def test_background_job_serializes_operations_and_rejects_same_resource() -> None:
    queue = BackgroundJobQueue()
    entered = Event()
    release = Event()

    def block() -> dict[str, object]:
        entered.set()
        release.wait(2)
        return {}

    active = queue.submit(kind="reset", resource_key="session:one", operation=block)
    assert entered.wait(1)
    with pytest.raises(JobConflictError) as conflict:
        queue.submit(kind="destroy", resource_key="session:one", operation=lambda: {})
    assert conflict.value.job_id == active.job_id
    release.set()
    queue.close()

    with pytest.raises(RuntimeError, match="closed"):
        queue.submit(kind="check", resource_key="session:two", operation=lambda: {})
    with pytest.raises(JobNotFoundError):
        queue.get(active.job_id.__class__(int=0))
