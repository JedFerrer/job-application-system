"""Authentication endpoints: register, login, Google, refresh, logout, me.

Tokens:
* The **access token** is returned in the JSON body (the SPA holds it in
  memory and sends it as ``Authorization: Bearer``).
* The **refresh token** is set as an httpOnly, Secure, SameSite cookie scoped
  to ``/api/v1/auth`` so it is only ever sent to the refresh/logout routes and
  is never readable by JavaScript (XSS-safe).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.deps import (
    AuthServiceDep,
    CurrentUser,
    EmailProviderDep,
    OptionalRedisDep,
    SettingsDep,
)
from app.core.config import Settings
from app.core.rate_limit import (
    RateLimitBackendUnavailableError,
    enforce,
    identifier_bucket,
    ip_bucket,
    normalize_email,
)
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
from app.infrastructure.email_service import EmailDeliveryError

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
async def register(
    request: Request,
    response: Response,
    payload: RegisterRequest,
    service: AuthServiceDep,
    settings: SettingsDep,
    redis: OptionalRedisDep,
) -> TokenResponse:
    await enforce(
        request,
        settings,
        redis,
        [
            ip_bucket(request, settings, "register", 10, 3600),
            identifier_bucket("register:email", normalize_email(payload.email), 3, 3600),
        ],
    )
    tokens = await service.register(
        email=payload.email,
        name=payload.name,
        password=payload.password.get_secret_value(),
    )
    _set_refresh_cookie(response, tokens, settings)
    return _to_token_response(tokens)


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    service: AuthServiceDep,
    settings: SettingsDep,
    redis: OptionalRedisDep,
) -> TokenResponse:
    limiter = await enforce(
        request,
        settings,
        redis,
        [ip_bucket(request, settings, "login", 20, 600)],
    )
    assert limiter is not None
    # This bucket counts every request that reaches the limiter, not only
    # failures, so Argon2 work is bounded before authentication executes.
    email_bucket = identifier_bucket("login:email", normalize_email(payload.email), 5, 900)
    email_count = await limiter.reserve(email_bucket)
    email_snapshot = limiter.snapshot_for_count(email_bucket, email_count)
    tokens = await service.authenticate(
        email=payload.email,
        password=payload.password.get_secret_value(),
    )
    try:
        await limiter.clear_if_unchanged(email_snapshot)
    except RateLimitBackendUnavailableError:
        # Authentication already succeeded; cleanup must not turn it into a
        # false failure. The next request remains protected by its TTL.
        pass
    _set_refresh_cookie(response, tokens, settings)
    return _to_token_response(tokens)


@router.post("/google", response_model=TokenResponse)
async def google_login(
    request: Request,
    response: Response,
    payload: GoogleAuthRequest,
    service: AuthServiceDep,
    settings: SettingsDep,
    redis: OptionalRedisDep,
) -> TokenResponse:
    await enforce(
        request, settings, redis, [ip_bucket(request, settings, "google", 10, 60)]
    )
    tokens = await service.google_auth(id_token_str=payload.id_token.get_secret_value())
    _set_refresh_cookie(response, tokens, settings)
    return _to_token_response(tokens)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
    redis: OptionalRedisDep,
) -> TokenResponse:
    await enforce(
        request,
        settings,
        redis,
        [ip_bucket(request, settings, "refresh", 30, 60)],
        fail_open=True,
    )
    token = request.cookies.get(settings.refresh_cookie_name)
    if not token:
        raise InvalidTokenError
    tokens = await service.refresh(refresh_token=token)
    _set_refresh_cookie(response, tokens, settings)
    return _to_token_response(tokens)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
) -> Response:
    token = request.cookies.get(settings.refresh_cookie_name)
    if token:
        await service.logout(refresh_token=token)
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
async def forgot_password(
    request: Request,
    payload: ForgotPasswordRequest,
    service: AuthServiceDep,
    email_provider: EmailProviderDep,
    settings: SettingsDep,
    redis: OptionalRedisDep,
) -> dict[str, str]:
    """Request a password-reset link. The token is returned in the response
    for development; in production it would be sent via email."""
    await enforce(
        request,
        settings,
        redis,
        [
            ip_bucket(request, settings, "forgot-password", 20, 3600),
            identifier_bucket("forgot-password:email", normalize_email(payload.email), 3, 900),
        ],
    )
    try:
        return await service.forgot_password(
            email=payload.email, email_provider=email_provider, cooldown_redis=redis
        )
    except EmailDeliveryError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="If the email is registered, a reset link has been sent.",
        ) from None


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    request: Request,
    payload: ResetPasswordRequest,
    service: AuthServiceDep,
    settings: SettingsDep,
    redis: OptionalRedisDep,
) -> Response:
    """Reset a password using a valid reset token."""
    await enforce(
        request,
        settings,
        redis,
        [
            ip_bucket(request, settings, "reset-password", 10, 600),
            identifier_bucket("reset-password:token", payload.token, 5, 900),
        ],
    )
    await service.reset_password(
        token_str=payload.token,
        new_password=payload.password.get_secret_value(),
    )
    response = Response()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
