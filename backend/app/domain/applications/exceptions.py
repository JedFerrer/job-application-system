"""Application-domain exceptions."""

from __future__ import annotations


class ApplicationError(Exception):
    """Base class for application-domain errors."""


class ApplicationNotFoundError(ApplicationError):
    def __init__(self, application_id: int) -> None:
        self.application_id = application_id
        super().__init__(f"Application {application_id} not found")


class ApplicationJobNotActionableError(ApplicationError):
    """Raised when a visible job cannot be mutated through an application."""

    def __init__(self) -> None:
        super().__init__("Job is no longer actionable")


class InvalidApplicationStatusTransitionError(ApplicationError):
    """Raised when a CRM status would move backwards in the pipeline."""

    def __init__(self, *, current_status: str, requested_status: str) -> None:
        self.current_status = current_status
        self.requested_status = requested_status
        super().__init__(
            f"Cannot transition application from {current_status} to {requested_status}"
        )


class PreparationVersionConflictError(ApplicationError):
    """Raised when a preparation write is based on an obsolete version."""

    def __init__(self) -> None:
        super().__init__("Preparation has changed; reload and try again")


class PreparationSelectionInvalidError(ApplicationError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class PackageIdempotencyConflictError(ApplicationError):
    """Raised when an idempotency key is reused for different material."""

    def __init__(self) -> None:
        super().__init__("Idempotency key was already used for different package content")


class PackageApprovalConflictError(ApplicationError):
    """Raised when a package no longer represents current preparation."""

    def __init__(self) -> None:
        super().__init__("Package no longer matches the current preparation")


class PreparationDiscardConfirmationRequiredError(ApplicationError):
    """Raised before deleting application preparation/history without confirmation."""

    def __init__(self) -> None:
        super().__init__("Confirm discarding preparation and package history before unsaving")
