"""Application-domain exceptions."""

from __future__ import annotations


class ApplicationError(Exception):
    """Base class for application-domain errors."""


class ApplicationNotFoundError(ApplicationError):
    def __init__(self, application_id: int) -> None:
        self.application_id = application_id
        super().__init__(f"Application {application_id} not found")


class InvalidApplicationStatusTransitionError(ApplicationError):
    """Raised when a CRM status would move backwards in the pipeline."""

    def __init__(self, *, current_status: str, requested_status: str) -> None:
        self.current_status = current_status
        self.requested_status = requested_status
        super().__init__(
            f"Cannot transition application from {current_status} to {requested_status}"
        )
