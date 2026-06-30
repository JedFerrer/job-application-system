"""Extension auth API endpoints.

Pairing-code flow:
  1. Web app calls auth/start → gets pairing code
  2. User enters code in extension popup
  3. Extension calls auth/complete → exchanges code for tokens
  4. Extension calls auth/refresh → rotates tokens
  5. Extension calls auth/logout → revokes session
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.api.deps import CurrentUser, DbSession, ExtensionAuthServiceDep
from app.core.rate_limit import limiter
from app.domain.extension.repository import ExtensionRepository
from app.domain.extension.schemas import (
    ExtensionLogoutRequest,
    ExtensionPairingCompleteRequest,
    ExtensionPairingCompleteResponse,
    ExtensionPairingStartResponse,
    ExtensionRefreshRequest,
    ExtensionRefreshResponse,
    ExtensionSessionItem,
    ExtensionSessionListResponse,
)

router = APIRouter(prefix="/extension/auth", tags=["extension-auth"])

# Also mount session management under /extension for the web dashboard
web_router = APIRouter(prefix="/extension", tags=["extension-sessions"])


@router.post("/start", response_model=ExtensionPairingStartResponse)
async def start_pairing(
    current_user: CurrentUser,
    service: ExtensionAuthServiceDep,
) -> ExtensionPairingStartResponse:
    """Generate a one-time pairing code for the authenticated user.

    Called from the web app at /extension/connect.
    """
    code, expires_in = await service.generate_pairing_code(user_id=current_user.id)
    return ExtensionPairingStartResponse(
        pairing_code=code,
        expires_in=expires_in,
    )


@router.post("/complete", response_model=ExtensionPairingCompleteResponse)
@limiter.limit("5/minute")
async def complete_pairing(
    request: Request,
    payload: ExtensionPairingCompleteRequest,
    service: ExtensionAuthServiceDep,
    db: DbSession,
) -> ExtensionPairingCompleteResponse:
    """Exchange a pairing code for extension tokens.

    Called from the extension popup after the user enters the code.
    Does not require authentication (the pairing code IS the auth).
    """
    from app.models.user import User

    response = await service.complete_pairing(payload.pairing_code)

    # Populate user info
    user = await db.get(User, int(response.user.id)) if response.user.id else None
    if user:
        response.user.id = user.id
        response.user.email = user.email
        response.user.name = user.name

    return response


@router.post("/refresh", response_model=ExtensionRefreshResponse)
@limiter.limit("30/minute")
async def refresh_extension_token(
    request: Request,
    payload: ExtensionRefreshRequest,
    service: ExtensionAuthServiceDep,
) -> ExtensionRefreshResponse:
    """Refresh an extension access token using a refresh token.

    Called from the extension service worker when the access token expires.
    Does not require authentication (refresh token IS the auth).
    """
    return await service.refresh(payload.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_extension(
    payload: ExtensionLogoutRequest,
    service: ExtensionAuthServiceDep,
) -> None:
    """Revoke an extension session.

    Called from the extension when the user disconnects.
    """
    await service.logout(payload.session_id)


# ── Web Dashboard Endpoints ────────────────────────────────────────────────


@web_router.get("/sessions", response_model=ExtensionSessionListResponse)
async def list_extension_sessions(
    current_user: CurrentUser,
    db: DbSession,
) -> ExtensionSessionListResponse:
    """List all extension sessions for the current user.

    Used by the web dashboard settings page.
    """
    repo = ExtensionRepository(db)
    sessions = await repo.list_user_sessions(current_user.id)

    items = [
        ExtensionSessionItem(
            id=str(s.id),
            device_name=s.device_name,
            browser=s.browser,
            created_at=s.created_at,
            last_used_at=s.last_used_at,
            revoked=s.revoked_at is not None,
        )
        for s in sessions
    ]

    return ExtensionSessionListResponse(sessions=items)
