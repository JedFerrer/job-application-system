"""Extension domain exceptions."""

from __future__ import annotations


class ExtensionError(Exception):
    """Base class for extension-domain errors."""


class PairingCodeExpiredError(ExtensionError):
    def __init__(self) -> None:
        super().__init__("Pairing code has expired")


class PairingCodeInvalidError(ExtensionError):
    def __init__(self) -> None:
        super().__init__("Invalid pairing code")


class ExtensionSessionNotFoundError(ExtensionError):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Extension session {session_id} not found")


class ExtensionSessionRevokedError(ExtensionError):
    def __init__(self) -> None:
        super().__init__("Extension session has been revoked")


class DuplicateJobImportError(ExtensionError):
    """Not raised — just logged. Job dedup is handled silently."""


class EventIdConflictError(ExtensionError):
    """Raised when a client reuses an event id for different event material."""

    def __init__(self) -> None:
        super().__init__("event_id was previously used for a different event")
