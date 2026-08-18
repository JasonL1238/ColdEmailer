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

import threading
from typing import Any, Optional, Protocol


class JobStore(Protocol):
    """The slice of `Database` these helpers need."""

    def get_job(self, job_id: str) -> Optional[dict]: ...
    def update_job(self, job_id: str, **fields: Any) -> Any: ...
    def finish_job(self, job_id: str, **fields: Any) -> Any: ...
    def create_job(self, job_type: str, payload: Any) -> dict: ...
    def list_jobs(self, job_type: str, limit: int = 20) -> list: ...


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


class SingleSlotJob:
    """One-at-a-time background job: the slot discipline and the crash wrapper.

    `person_finder` and `deep_research` each ran one job at a time and had each
    written this out: claim an in-process slot, refuse a second start, hold the
    slot until the worker thread exits, and finish the row on an unhandled
    exception. Two copies meant two chances to get the cancel/finish race wrong.

    The slot is deliberately held past a cancel. `cancel_atomically` flips the
    database row, but the worker is still scraping until its `finally` runs —
    releasing early lets a second run start alongside it.

    Subclasses set `JOB_TYPE` and `JOB_LABEL`, assign `self.db`, and call
    `_init_slot()` from `__init__`.

    `discovery` and `generation` deliberately do NOT use this: they print a
    crash line, do not truncate the error, and omit `only_if_running`, so they
    would overwrite a row a cancel had already finished. Moving them here is a
    behaviour change to the cancel race, not a dedup.
    """

    JOB_TYPE: str = ""
    JOB_LABEL: str = "job"
    LIST_LIMIT: int = 20

    # Supplied by the concrete service. Declared so the contract this mixin
    # depends on is visible here rather than only at the two subclasses.
    db: "JobStore"
    _lock: threading.Lock
    _running_job: Optional[str]

    def _init_slot(self) -> None:
        self._lock = threading.Lock()
        self._running_job: Optional[str] = None

    def _claim_slot(self, payload: dict) -> dict:
        """Create the job row and take the slot, or raise RuntimeError."""
        with self._lock:
            if self._running_job is not None:
                raise RuntimeError(
                    f"A {self.JOB_LABEL} is already running or winding down. "
                    "Cancel it or wait.")
            if any(j.get("status") == "running"
                   for j in self.db.list_jobs(self.JOB_TYPE, limit=5)):
                raise RuntimeError(
                    f"A {self.JOB_LABEL} is already running. Cancel it or wait.")
            job = self.db.create_job(self.JOB_TYPE, payload)
            self._running_job = job["id"]
            return job

    def run_guarded(self, job_id: str, run, *args, **kwargs) -> None:
        """Thread target: run the worker, fail the row loudly, free the slot."""
        try:
            run(job_id, *args, **kwargs)
        except Exception as exc:  # pragma: no cover
            self.db.finish_job(job_id, status="failed", error=str(exc)[:500],
                               only_if_running=True)
        finally:
            with self._lock:
                if self._running_job == job_id:
                    self._running_job = None

    def cancel(self, job_id: str) -> bool:
        return cancel_atomically(self.db, job_id, self.JOB_TYPE)

    def list_jobs(self, limit: Optional[int] = None):
        return self.db.list_jobs(job_type=self.JOB_TYPE,
                                 limit=self.LIST_LIMIT if limit is None else limit)

    def get_job(self, job_id: str) -> Optional[dict]:
        job = self.db.get_job(job_id)
        if job and job.get("type") != self.JOB_TYPE:
            return None
        return job

    def _cancelled(self, job_id: str) -> bool:
        """Broader than `is_cancelled` on purpose: a run that already finished
        or failed must also stop doing work."""
        return is_finished(self.db, job_id)
