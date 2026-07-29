"""Email delivery abstraction.

Currently a no-op — the concrete provider (SendGrid / SES / Resend) is
not yet integrated.  When a provider is added, implement the ``send_*``
methods and wire the concrete class through the DI container.

Until then, the production forgot-password flow safely returns a generic
message without leaking the reset token.
"""

from __future__ import annotations

from typing import Protocol


class EmailProvider(Protocol):
    """Protocol for sending transactional emails.

    Implementations should be idempotent (the caller may retry).
    """

    async def send_password_reset(
        self, *, to_email: str, reset_url: str
    ) -> None:
        """Send a password-reset link to *to_email*."""
        ...


class NoOpEmailProvider:
    """No-op provider used when no email provider is configured.

    Logs the reset URL at INFO level in development; silently drops it
    in staging/production (the caller is responsible for NOT returning
    the reset_url to the client in those environments).
    """

    async def send_password_reset(
        self, *, to_email: str, reset_url: str
    ) -> None:
        """No-op: reset URL is not delivered."""
        # The caller (AuthService) handles the dev-vs-prod behaviour.
        return


def get_email_provider() -> EmailProvider:
    """Return the configured email provider (currently always NoOp).

    When an email provider is integrated, this should read the
    EMAIL_PROVIDER setting and return the appropriate implementation.
    """
    return NoOpEmailProvider()
