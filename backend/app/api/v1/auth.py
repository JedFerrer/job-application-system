"""Authentication endpoints: register, login, Google, refresh, logout, me.

Tokens:
* The **access token** is returned in the JSON body (the SPA holds it in
  memory and sends it as ``Authorization: Bearer``).
* The **refresh token** is set as an httpOnly, Secure, SameSite cookie scoped
  to ``/api/v1/auth`` so it is only ever sent to the refresh/logout routes and
  is never readable by JavaScript (XSS-safe).
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.api.deps import AuthServiceDep, CurrentUser, SettingsDep
from app.core.config import Settings
from app.core.rate_limit import limiter
from app.domain.auth.exceptions import InvalidTokenError
from app.domain.auth.schemas import (
    ForgotPasswordRequest,
    GoogleAuthRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserResponse,
)
from app.domain.auth.service import IssuedTokens

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_refresh_cookie(
    response: Response, tokens: IssuedTokens, settings: Settings
) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=tokens.refresh_token,
        max_age=settings.refresh_token_expire_days * 24 * 3600,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
        path=settings.refresh_cookie_path,
    )


def _to_token_response(tokens: IssuedTokens) -> TokenResponse:
    return TokenResponse(
        access_token=tokens.access_token,
        expires_in=tokens.expires_in,
        user=UserResponse.model_validate(tokens.user),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(
    request: Request,
    response: Response,
    payload: RegisterRequest,
    service: AuthServiceDep,
    settings: SettingsDep,
) -> TokenResponse:
    tokens = await service.register(
        email=payload.email,
        name=payload.name,
        password=payload.password.get_secret_value(),
    )
    _set_refresh_cookie(response, tokens, settings)
    return _to_token_response(tokens)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    service: AuthServiceDep,
    settings: SettingsDep,
) -> TokenResponse:
    tokens = await service.authenticate(
        email=payload.email,
        password=payload.password.get_secret_value(),
    )
    _set_refresh_cookie(response, tokens, settings)
    return _to_token_response(tokens)


@router.post("/google", response_model=TokenResponse)
@limiter.limit("10/minute")
async def google_login(
    request: Request,
    response: Response,
    payload: GoogleAuthRequest,
    service: AuthServiceDep,
    settings: SettingsDep,
) -> TokenResponse:
    tokens = await service.google_auth(id_token_str=payload.id_token.get_secret_value())
    _set_refresh_cookie(response, tokens, settings)
    return _to_token_response(tokens)


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("30/minute")
async def refresh(
    request: Request,
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
) -> TokenResponse:
    token = request.cookies.get(settings.refresh_cookie_name)
    if not token:
        raise InvalidTokenError
    tokens = await service.refresh(refresh_token=token)
    _set_refresh_cookie(response, tokens, settings)
    return _to_token_response(tokens)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, settings: SettingsDep) -> Response:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserResponse)
async def me(current_user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.post("/forgot-password")
@limiter.limit("3/minute")
async def forgot_password(
    request: Request,
    payload: ForgotPasswordRequest,
    service: AuthServiceDep,
) -> dict[str, str]:
    """Request a password-reset link. The token is returned in the response
    for development; in production it would be sent via email."""
    return await service.forgot_password(email=payload.email)


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def reset_password(
    request: Request,
    payload: ResetPasswordRequest,
    service: AuthServiceDep,
) -> Response:
    """Reset a password using a valid reset token."""
    await service.reset_password(
        token_str=payload.token,
        new_password=payload.password.get_secret_value(),
    )
    response = Response()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
