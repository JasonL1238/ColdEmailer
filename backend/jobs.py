"""The cancellation contract every background service shares.

A job id arrives from the URL, so a cancel route that trusts it can kill a job
of an entirely different kind — the Cancel button on a discovery run stopping a
send in progress. Each service used to carry its own copy of the type check,
which is exactly the guard that goes missing when a fourth service is added.

Two different questions get asked about a running job, and conflating them is
its own bug: discovery and generation ask "did the user cancel me", where a
job that finished normally is not cancelled; deep research asks "should I stop
doing work", where any non-running status means yes.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol


class JobStore(Protocol):
    """The slice of `Database` these helpers need."""

    def get_job(self, job_id: str) -> Optional[dict]: ...
    def update_job(self, job_id: str, **fields: Any) -> Any: ...
    def finish_job(self, job_id: str, **fields: Any) -> Any: ...


def _job_of_type(db: JobStore, job_id: str, job_type: str) -> Optional[dict]:
    job = db.get_job(job_id)
    if not job or job.get("type") != job_type or job.get("status") != "running":
        return None
    return job


def cancel(db: JobStore, job_id: str, job_type: str) -> bool:
    """Flip a running job of this type to cancelled. False if it is not one."""
    if not _job_of_type(db, job_id, job_type):
        return False
    db.update_job(job_id, status="cancelled")
    return True


def cancel_atomically(db: JobStore, job_id: str, job_type: str,
                      reason: str = "Cancelled by user") -> bool:
    """Cancel via compare-and-set, for workers that finish their own job row.

    `finish_job(only_if_running=True)` will not overwrite a worker that already
    reached done or failed in the gap between the check and the write.
    """
    if not _job_of_type(db, job_id, job_type):
        return False
    return bool(db.finish_job(job_id, status="cancelled", error=reason,
                              only_if_running=True))


def is_cancelled(db: JobStore, job_id: str) -> bool:
    """The user asked to stop. A vanished job counts — nothing owns it now."""
    job = db.get_job(job_id)
    return not job or job.get("status") == "cancelled"


def is_finished(db: JobStore, job_id: str) -> bool:
    """Anything other than still-running: cancelled, done, failed, or gone."""
    job = db.get_job(job_id)
    return not job or job.get("status") != "running"
