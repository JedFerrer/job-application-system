"""PostgreSQL-backed authorization tests for extension session routes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.core.security import create_token, decode_token_payload, hash_password
from app.domain.extension.extension_auth_service import ExtensionAuthService
from app.domain.extension.repository import ExtensionRepository
from app.infrastructure.database import engine as app_engine
from app.infrastructure.redis_client import close_redis, init_redis
from app.main import app
from app.models.extension_session import ExtensionSession
from app.models.user import User

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


@pytest.fixture(autouse=True)
async def clean_db(engine: AsyncEngine) -> AsyncIterator[None]:
    await app_engine.dispose()
    await init_redis(get_settings().redis_url)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        await db.execute(text("TRUNCATE TABLE web_refresh_sessions CASCADE"))
        await db.execute(text("TRUNCATE TABLE users CASCADE"))
        await db.commit()
    yield
    await app_engine.dispose()
    await close_redis()


async def _create_user(db: AsyncSession, user_id: int, email: str) -> User:
    user = User(
        id=user_id,
        email=email,
        name="Integration User",
        hashed_password=hash_password("password123"),
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _pair(
    db: AsyncSession, settings: Settings, user_id: int
) -> Any:
    service = ExtensionAuthService(repository=ExtensionRepository(db), settings=settings)
    code, _ = await service.generate_pairing_code(user_id=user_id)
    pair = await service.complete_pairing(code)
    await db.commit()
    return pair


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_web_owner_can_revoke_and_cross_user_is_nondisclosing(
    engine: AsyncEngine,
) -> None:
    settings = get_settings()
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        owner = await _create_user(db, 1, "owner@example.com")
        other = await _create_user(db, 2, "other@example.com")
        pair = await _pair(db, settings, owner.id)
        cross_user_target = await _pair(db, settings, owner.id)
        owner_token = create_token(
            settings=settings, subject=owner.id, token_type="access"
        )
        other_token = create_token(
            settings=settings, subject=other.id, token_type="access"
        )

        async with _client() as client:
            response = await client.delete(
                f"/api/v1/extension/sessions/{cross_user_target.session_id}",
                headers=_bearer(other_token),
            )
            assert response.status_code == 204

            response = await client.delete(
                f"/api/v1/extension/sessions/{pair.session_id}",
                headers=_bearer(owner_token),
            )
            assert response.status_code == 204

        session = await db.get(ExtensionSession, pair.session_id)
        cross_user_session = await db.get(ExtensionSession, cross_user_target.session_id)
        assert session is not None
        assert session.revoked_at is not None
        assert cross_user_session is not None
        assert cross_user_session.revoked_at is None


@pytest.mark.asyncio
async def test_extension_token_cannot_use_dashboard_owner_route(
    engine: AsyncEngine,
) -> None:
    settings = get_settings()
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "owner@example.com")
        own_pair = await _pair(db, settings, user.id)
        target_pair = await _pair(db, settings, user.id)

        async with _client() as client:
            for target_id in (own_pair.session_id, target_pair.session_id):
                response = await client.delete(
                    f"/api/v1/extension/sessions/{target_id}",
                    headers=_bearer(own_pair.access_token),
                )
                assert response.status_code == 401

        sessions = (
            await db.execute(
                select(ExtensionSession).where(ExtensionSession.user_id == user.id)
            )
        ).scalars().all()
        assert all(session.revoked_at is None for session in sessions)


@pytest.mark.asyncio
async def test_owner_route_rejects_missing_malformed_expired_and_wrong_tokens(
    engine: AsyncEngine,
) -> None:
    settings = get_settings()
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "owner@example.com")
        pair = await _pair(db, settings, user.id)
        now = datetime.now(UTC)
        expired = jwt.encode(
            {
                "sub": str(user.id),
                "type": "access",
                "iss": settings.jwt_issuer,
                "aud": settings.jwt_audience,
                "iat": int((now - timedelta(minutes=20)).timestamp()),
                "exp": int((now - timedelta(minutes=1)).timestamp()),
            },
            settings.jwt_secret.get_secret_value(),
            algorithm=settings.jwt_algorithm,
        )
        wrong_type = create_token(settings=settings, subject=user.id, token_type="reset")

        async with _client() as client:
            cases = [None, "not-a-jwt", expired, wrong_type]
            for token in cases:
                headers = {} if token is None else _bearer(token)
                response = await client.delete(
                    f"/api/v1/extension/sessions/{pair.session_id}",
                    headers=headers,
                )
                assert response.status_code == 401

        session = await db.get(ExtensionSession, pair.session_id)
        assert session is not None
        assert session.revoked_at is None


@pytest.mark.asyncio
async def test_owner_route_rejects_inactive_user(engine: AsyncEngine) -> None:
    settings = get_settings()
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "inactive@example.com")
        pair = await _pair(db, settings, user.id)
        token = create_token(settings=settings, subject=user.id, token_type="access")
        user.is_active = False
        await db.commit()

        async with _client() as client:
            response = await client.delete(
                f"/api/v1/extension/sessions/{pair.session_id}",
                headers=_bearer(token),
            )
            assert response.status_code == 401

        session = await db.get(ExtensionSession, pair.session_id)
        assert session is not None
        assert session.revoked_at is None


@pytest.mark.asyncio
async def test_self_disconnect_uses_claim_and_protected_revocation(
    engine: AsyncEngine,
) -> None:
    settings = get_settings()
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "owner@example.com")
        own_pair = await _pair(db, settings, user.id)
        other_pair = await _pair(db, settings, user.id)

        async with _client() as client:
            response = await client.post(
                "/api/v1/extension/auth/logout",
                headers=_bearer(own_pair.access_token),
                json={"session_id": other_pair.session_id},
            )
            assert response.status_code == 204

        own = await db.get(ExtensionSession, own_pair.session_id)
        other = await db.get(ExtensionSession, other_pair.session_id)
        assert own is not None and own.revoked_at is not None
        assert other is not None and other.revoked_at is None

        async with _client() as client:
            response = await client.get(
                "/api/v1/extension/profile/autofill",
                headers=_bearer(own_pair.access_token),
            )
            assert response.status_code == 401


@pytest.mark.asyncio
async def test_password_reset_revokes_only_users_extension_sessions(
    engine: AsyncEngine,
) -> None:
    settings = get_settings()
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "reset-owner@example.com")
        other = await _create_user(db, 2, "reset-other@example.com")
        own_pair = await _pair(db, settings, user.id)
        other_pair = await _pair(db, settings, other.id)
        reset_token = create_token(settings=settings, subject=user.id, token_type="reset")

        async with _client() as client:
            response = await client.post(
                "/api/v1/auth/reset-password",
                json={"token": reset_token, "password": "newpassword456"},
            )
            assert response.status_code == 204

            revoked_response = await client.get(
                "/api/v1/extension/profile/autofill",
                headers=_bearer(own_pair.access_token),
            )
            active_response = await client.get(
                "/api/v1/extension/profile/autofill",
                headers=_bearer(other_pair.access_token),
            )
            assert revoked_response.status_code == 401
            assert active_response.status_code == 200

        own_session = await db.get(ExtensionSession, own_pair.session_id)
        other_session = await db.get(ExtensionSession, other_pair.session_id)
        assert own_session is not None and own_session.revoked_at is not None
        assert other_session is not None and other_session.revoked_at is None


@pytest.mark.asyncio
async def test_extension_session_claim_validation_and_user_binding(
    engine: AsyncEngine,
) -> None:
    settings = get_settings()
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "owner@example.com")
        other = await _create_user(db, 2, "other@example.com")
        pair = await _pair(db, settings, user.id)
        other_pair = await _pair(db, settings, other.id)
        base_payload = decode_token_payload(
            settings=settings, token=pair.access_token, expected_type="access"
        )

        async with _client() as client:
            valid = await client.get(
                "/api/v1/extension/profile/autofill",
                headers=_bearer(pair.access_token),
            )
            assert valid.status_code == 200

            claim_values: tuple[object, ...] = (
                None,
                123,
                True,
                "",
                "   ",
                "not-a-uuid",
                [],
                {},
            )
            for value in claim_values:
                payload = dict(base_payload)
                if value is None:
                    payload.pop("session_id", None)
                else:
                    payload["session_id"] = value
                token = jwt.encode(
                    payload,
                    settings.jwt_secret.get_secret_value(),
                    algorithm=settings.jwt_algorithm,
                )
                response = await client.get(
                    "/api/v1/extension/profile/autofill",
                    headers=_bearer(token),
                )
                assert response.status_code == 401

            mismatch = create_token(
                settings=settings,
                subject=user.id,
                token_type="access",
                extra_claims={"session_id": other_pair.session_id},
            )
            response = await client.get(
                "/api/v1/extension/profile/autofill",
                headers=_bearer(mismatch),
            )
            assert response.status_code == 401
