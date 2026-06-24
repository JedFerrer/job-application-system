"""Job domain exceptions."""

from __future__ import annotations


class JobError(Exception):
    """Base class for job-domain errors."""


class JobNotFoundError(JobError):
    def __init__(self, job_id: int) -> None:
        self.job_id = job_id
        super().__init__(f"Job {job_id} not found")
