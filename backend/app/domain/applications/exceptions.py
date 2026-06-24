"""Application-domain exceptions."""

from __future__ import annotations


class ApplicationError(Exception):
    """Base class for application-domain errors."""


class ApplicationNotFoundError(ApplicationError):
    def __init__(self, application_id: int) -> None:
        self.application_id = application_id
        super().__init__(f"Application {application_id} not found")
