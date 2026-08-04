"""Password-reset email delivery providers."""

from __future__ import annotations

from typing import Protocol

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger("email")


class EmailDeliveryError(Exception):
    """Safe provider failure with no request or response data attached."""

    def __init__(self) -> None:
        super().__init__("Password reset delivery failed")


class EmailProvider(Protocol):
    """Protocol for sending transactional emails.

    Implementations should be idempotent (the caller may retry).
    """

    async def send_password_reset(
        self,
        *,
        to_email: str,
        reset_url: str,
        user_id: int,
        reset_version: int,
    ) -> None:
        """Send a password-reset link to *to_email*."""
        ...


class MockEmailProvider:
    """Test-controlled provider that never performs network I/O."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def send_password_reset(
        self,
        *,
        to_email: str,
        reset_url: str,
        user_id: int,
        reset_version: int,
    ) -> None:
        self.calls.append(
            {
                "to_email": to_email,
                "reset_url": reset_url,
                "user_id": user_id,
                "reset_version": reset_version,
            }
        )


class ConsoleEmailProvider(MockEmailProvider):
    """Development provider; delivery is represented by the local reset URL."""


class _TransientResendError(Exception):
    pass


class _PermanentResendError(Exception):
    pass


class ResendEmailProvider:
    """Resend HTTP provider with bounded retries and safe logging."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @retry(
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((httpx.RequestError, _TransientResendError)),
        reraise=True,
    )
    async def _send_once(
        self, *, payload: dict[str, object], idempotency_key: str
    ) -> None:
        async with httpx.AsyncClient(
            timeout=self._settings.email_provider_timeout_seconds
        ) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": (
                        f"Bearer {self._settings.resend_api_key.get_secret_value()}"
                    ),
                    "User-Agent": "job-application-system-password-reset/1.0",
                    "Idempotency-Key": idempotency_key,
                },
                json=payload,
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise _TransientResendError(f"HTTP {response.status_code}")
        if response.status_code >= 400:
            raise _PermanentResendError(f"HTTP {response.status_code}")

    async def send_password_reset(
        self,
        *,
        to_email: str,
        reset_url: str,
        user_id: int,
        reset_version: int,
    ) -> None:
        idempotency_key = f"password-reset/{user_id}/{reset_version}"
        payload: dict[str, object] = {
            "from": f"{self._settings.resend_from_name} <{self._settings.resend_from_email}>",
            "to": [to_email],
            "subject": "Reset your password",
            "text": (
                "Reset your password\n\n"
                f"Use this link to reset your password: {reset_url}\n\n"
                "This link expires in 15 minutes. If you did not request a password reset, "
                "you can ignore this email."
            ),
            "html": (
                "<p>Reset your password</p>"
                f'<p><a href="{reset_url}">Reset your password</a></p>'
                "<p>This link expires in 15 minutes. If you did not request a password "
                "reset, you can ignore this email.</p>"
            ),
        }
        if self._settings.resend_reply_to:
            payload["reply_to"] = self._settings.resend_reply_to

        logger.info("password_reset_delivery_attempted", mode="resend")
        try:
            await self._send_once(payload=payload, idempotency_key=idempotency_key)
        except _PermanentResendError:
            logger.warning("password_reset_delivery_permanent_failure", mode="resend")
            raise EmailDeliveryError from None
        except (httpx.RequestError, _TransientResendError):
            logger.warning("password_reset_delivery_retries_exhausted", mode="resend")
            raise EmailDeliveryError from None
        except Exception:
            # Keep provider/configuration failures generic at the API boundary.
            # Never serialize the exception: HTTP clients may retain request data.
            logger.warning("password_reset_delivery_provider_failure", mode="resend")
            raise EmailDeliveryError from None
        else:
            logger.info("password_reset_delivery_succeeded", mode="resend")


def get_email_provider(settings: Settings) -> EmailProvider:
    """Select a provider without creating an outbound client at import time."""
    if settings.environment == "test" or settings.email_delivery_mode == "mock":
        return MockEmailProvider()
    if settings.email_delivery_mode == "console":
        return ConsoleEmailProvider()
    if not settings.resend_api_key.get_secret_value():
        raise RuntimeError("RESEND_API_KEY is required when EMAIL_DELIVERY_MODE=resend")
    if not settings.resend_from_email:
        raise RuntimeError("RESEND_FROM_EMAIL is required when EMAIL_DELIVERY_MODE=resend")
    if not settings.resend_from_name:
        raise RuntimeError("RESEND_FROM_NAME is required when EMAIL_DELIVERY_MODE=resend")
    if not settings.password_reset_url:
        raise RuntimeError("PASSWORD_RESET_URL is required when EMAIL_DELIVERY_MODE=resend")
    return ResendEmailProvider(settings)
