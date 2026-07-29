"""Authentication API schemas.

Passwords use ``SecretStr`` so they never appear in logs or tracebacks.
Separate request/response models keep the password hash out of API output.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr


class RegisterRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    name: str = Field(min_length=1, max_length=255)
    password: SecretStr = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=128)


class GoogleAuthRequest(BaseModel):
    """Body for ``POST /auth/google`` — the Google ID token from the browser."""

    id_token: SecretStr


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    name: str
    avatar_url: str | None = None
    created_at: datetime


class TokenResponse(BaseModel):
    """Access token returned in the body; refresh token is set as a cookie."""

    access_token: str
    token_type: str = "bearer"  # noqa: S105 - token kind, not a credential
    expires_in: int
    user: UserResponse


class ForgotPasswordRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    token: str = Field(min_length=1)
    password: SecretStr = Field(min_length=8, max_length=128)
