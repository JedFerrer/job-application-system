"""Refresh-token session security regression tests.

Verifies that the web refresh-token session storage correctly handles:
- Session creation on login/register
- Token rotation on refresh
- Session revocation on logout
- Token reuse detection
- Password reset revokes all sessions
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
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
from app.core.security import create_token, hash_password
from app.domain.auth.exceptions import InvalidTokenError
from app.domain.auth.repository import UserRepository
from app.domain.auth.service import AuthService
from app.domain.auth.web_session_repository import WebSessionRepository
from app.domain.extension.exceptions import ExtensionSessionRevokedError
from app.domain.extension.extension_auth_service import ExtensionAuthService
from app.domain.extension.repository import ExtensionRepository
from app.infrastructure.database import engine as app_engine
from app.main import app
from app.models.extension_refresh_token import ExtensionRefreshToken
from app.models.extension_session import ExtensionSession
from app.models.user import User
from app.models.web_refresh_session import WebRefreshSession

pytestmark = pytest.mark.integration

# ── Helpers ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(settings.database_url, poolclass=NullPool, echo=False)


@pytest.fixture(autouse=True)
async def clean_db(engine: AsyncEngine) -> None:
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        await db.execute(text("TRUNCATE TABLE web_refresh_sessions CASCADE"))
        await db.execute(text("TRUNCATE TABLE users CASCADE"))
        await db.commit()


@pytest.fixture
def settings() -> Settings:
    # Use a fixed secret for deterministic token generation in tests.
    from pydantic import SecretStr
    return Settings(
        environment="development",
        jwt_secret=SecretStr("test-secret-for-testing-at-least-32-chars!!"),
    )


async def _create_user(db: AsyncSession, user_id: int, email: str) -> User:
    user = User(id=user_id, email=email, name="Test User", is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _make_svc(db: AsyncSession, settings: Settings) -> AuthService:
    return AuthService(
        repository=UserRepository(db),
        settings=settings,
        web_sessions=WebSessionRepository(db),
        extension_sessions=ExtensionRepository(db),
    )


# ── Tests ─────────────────────────────────────────────────────────────────


async def test_login_creates_session(engine: AsyncEngine, settings: Settings) -> None:
    """Login creates a web refresh session with a hashed token."""
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "a@test.com")
        user.hashed_password = hash_password("password123")
        await db.commit()

        svc = _make_svc(db, settings)
        tokens = await svc.authenticate(email="a@test.com", password="password123")
        await db.commit()

    # Verify session was created.
    async with sf() as db:
        result = await db.execute(select(WebRefreshSession))
        sessions = result.scalars().all()
        assert len(sessions) == 1
        s = sessions[0]
        assert s.user_id == 1
        assert s.refresh_token_hash == hashlib.sha256(
            tokens.refresh_token.encode()
        ).hexdigest()
        assert s.revoked_at is None
        assert s.token_family is not None


async def test_refresh_rotates_token(engine: AsyncEngine, settings: Settings) -> None:
    """Refresh rotates the token: old session revoked, new session created."""
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "a@test.com")
        user.hashed_password = hash_password("password123")
        await db.commit()

        svc = _make_svc(db, settings)
        tokens1 = await svc.authenticate(email="a@test.com", password="password123")
        old_token = tokens1.refresh_token

        # Refresh.
        tokens2 = await svc.refresh(refresh_token=old_token)
        new_token = tokens2.refresh_token
        await db.commit()

    # Verify old session revoked, new session created, same family.
    async with sf() as db:
        result = await db.execute(
            select(WebRefreshSession).order_by(WebRefreshSession.created_at)
        )
        sessions = result.scalars().all()
        assert len(sessions) == 2
        old_s, new_s = sessions[0], sessions[1]
        assert old_s.revoked_at is not None
        assert new_s.revoked_at is None
        assert old_s.token_family == new_s.token_family
        assert new_s.refresh_token_hash == hashlib.sha256(
            new_token.encode()
        ).hexdigest()


async def test_old_token_reuse_revokes_family(engine: AsyncEngine, settings: Settings) -> None:
    """Using an already-rotated token revokes the entire token family."""
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "a@test.com")
        user.hashed_password = hash_password("password123")
        await db.commit()

        svc = _make_svc(db, settings)
        tokens1 = await svc.authenticate(email="a@test.com", password="password123")

        # First refresh — rotates token.
        rotated_tokens = await svc.refresh(refresh_token=tokens1.refresh_token)

        old_session = (
            await db.execute(
                select(WebRefreshSession).where(
                    WebRefreshSession.refresh_token_hash
                    == hashlib.sha256(tokens1.refresh_token.encode()).hexdigest()
                )
            )
        ).scalar_one()
        old_session.last_used_at = datetime.now(UTC) - timedelta(
            seconds=settings.web_refresh_reuse_grace_seconds + 1
        )
        await db.flush()

        # Reuse the old token outside the grace window.
        with pytest.raises(InvalidTokenError):
            await svc.refresh(refresh_token=tokens1.refresh_token)

    # Verify ALL sessions in the family are revoked.
    async with sf() as db:
        result = await db.execute(select(WebRefreshSession))
        sessions = result.scalars().all()
        for s in sessions:
            assert s.revoked_at is not None
    async with sf() as verify_db:
        with pytest.raises(InvalidTokenError):
            await _make_svc(verify_db, settings).refresh(
                refresh_token=rotated_tokens.refresh_token
            )


async def test_logout_revokes_session(engine: AsyncEngine, settings: Settings) -> None:
    """Logout revokes the current refresh session."""
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "a@test.com")
        user.hashed_password = hash_password("password123")
        await db.commit()

        svc = _make_svc(db, settings)
        tokens = await svc.authenticate(email="a@test.com", password="password123")

        # Logout.
        await svc.logout(refresh_token=tokens.refresh_token)
        await db.commit()

    # Verify session revoked.
    async with sf() as db:
        result = await db.execute(select(WebRefreshSession))
        sessions = result.scalars().all()
        assert len(sessions) == 1
        assert sessions[0].revoked_at is not None


async def test_password_reset_revokes_all_sessions(
    engine: AsyncEngine, settings: Settings
) -> None:
    """Password reset revokes all active web refresh sessions for the user."""
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "a@test.com")
        from app.core.security import hash_password
        user.hashed_password = hash_password("password123")
        await db.commit()

        svc = _make_svc(db, settings)

        other_user = await _create_user(db, 2, "b@test.com")
        extension_session = ExtensionSession(
            user_id=other_user.id, refresh_token_hash="extension-hash"
        )
        db.add(extension_session)
        await db.commit()

        extension_service = ExtensionAuthService(
            repository=ExtensionRepository(db), settings=settings
        )
        pairing_code, _ = await extension_service.generate_pairing_code(user_id=user.id)
        extension_pair = await extension_service.complete_pairing(pairing_code)
        await db.commit()

        # Create two sessions (simulating two logins).
        await svc.authenticate(email="a@test.com", password="password123")
        await svc.authenticate(email="a@test.com", password="password123")

        # Generate a reset token and reset the password.
        reset_token = create_token(
            settings=settings, subject=user.id, token_type="reset"
        )
        await svc.reset_password(
            token_str=reset_token, new_password="newpassword456"
        )
        await db.commit()

    # Verify all sessions revoked.
    async with sf() as db:
        result = await db.execute(select(WebRefreshSession))
        sessions = result.scalars().all()
        assert len(sessions) == 2
    for s in sessions:
        assert s.revoked_at is not None

    async with sf() as db:
        extension = await db.get(ExtensionSession, extension_session.id)
        assert extension is not None
        assert extension.revoked_at is None

    async with sf() as db:
        user_session = await db.get(ExtensionSession, extension_pair.session_id)
        assert user_session is not None
        assert user_session.revoked_at is not None
        revoked_service = ExtensionAuthService(
            repository=ExtensionRepository(db), settings=settings
        )
        with pytest.raises(ExtensionSessionRevokedError):
            await revoked_service.refresh(extension_pair.refresh_token)


async def test_delayed_web_reuse_api_persists_family_revocation(
    engine: AsyncEngine,
) -> None:
    """The real API rollback path preserves delayed family revocation."""
    api_settings = get_settings()
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "a@test.com")
        user.hashed_password = hash_password("password123")
        await db.commit()
        svc = _make_svc(db, api_settings)
        tokens = await svc.authenticate(email="a@test.com", password="password123")
        rotated = await svc.refresh(refresh_token=tokens.refresh_token)
        await db.commit()

    async with sf() as db:
        old = (
            await db.execute(
                select(WebRefreshSession).where(
                    WebRefreshSession.refresh_token_hash
                    == hashlib.sha256(tokens.refresh_token.encode()).hexdigest()
                )
            )
        ).scalar_one()
        old.last_used_at = datetime.now(UTC) - timedelta(
            seconds=api_settings.web_refresh_reuse_grace_seconds + 1
        )
        await db.commit()

    await app_engine.dispose()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/auth/refresh",
            cookies={api_settings.refresh_cookie_name: tokens.refresh_token},
        )
    assert response.status_code == 401

    async with sf() as db:
        rows = (await db.execute(select(WebRefreshSession))).scalars().all()
        assert rows
        assert all(row.revoked_at is not None for row in rows)
        with pytest.raises(InvalidTokenError):
            await _make_svc(db, api_settings).refresh(
                refresh_token=rotated.refresh_token
            )


async def test_legacy_extension_refresh_backfill_preserves_family_rotation(
    engine: AsyncEngine, settings: Settings
) -> None:
    """A migrated legacy hash refreshes once and creates a same-family successor."""
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "a@test.com")
        legacy_token = create_token(settings=settings, subject=user.id, token_type="refresh")
        session = ExtensionSession(
            user_id=user.id,
            refresh_token_hash=hashlib.sha256(legacy_token.encode()).hexdigest(),
        )
        db.add(session)
        await db.flush()
        family = str(uuid4())
        db.add(
            ExtensionRefreshToken(
                extension_session_id=session.id,
                token_hash=hashlib.sha256(legacy_token.encode()).hexdigest(),
                token_family=family,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        await db.commit()

        service = ExtensionAuthService(
            repository=ExtensionRepository(db), settings=settings
        )
        refreshed = await service.refresh(legacy_token)
        await db.commit()

        rows = (
            await db.execute(
                select(ExtensionRefreshToken)
                .where(ExtensionRefreshToken.extension_session_id == session.id)
                .order_by(ExtensionRefreshToken.created_at)
            )
        ).scalars().all()
        assert len(rows) == 2
        assert rows[0].used_at is not None
        assert rows[1].token_family == family
        assert hashlib.sha256(refreshed.refresh_token.encode()).hexdigest() == rows[1].token_hash
        assert refreshed.refresh_token not in {row.token_hash for row in rows}


async def test_migration_backfills_active_and_revoked_legacy_sessions(
    engine: AsyncEngine, settings: Settings
) -> None:
    """The real 0029->0030 upgrade preserves legacy session state."""
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().sync_database_url)
    command.downgrade(config, "0029_resume_import_provenance")
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    active_token = create_token(settings=settings, subject=1, token_type="refresh")
    revoked_token = create_token(settings=settings, subject=1, token_type="refresh")
    source_created_at = datetime.now(UTC) - timedelta(hours=2)
    source_revoked_at = datetime.now(UTC) - timedelta(hours=1)

    try:
        async with sf() as db:
            user = await _create_user(db, 1, "a@test.com")
            active = ExtensionSession(
                user_id=user.id,
                refresh_token_hash=hashlib.sha256(active_token.encode()).hexdigest(),
                created_at=source_created_at,
            )
            revoked = ExtensionSession(
                user_id=user.id,
                refresh_token_hash=hashlib.sha256(revoked_token.encode()).hexdigest(),
                created_at=source_created_at,
                revoked_at=source_revoked_at,
            )
            db.add_all([active, revoked])
            await db.commit()
            active_id, revoked_id = active.id, revoked.id

        command.upgrade(config, "0030_ext_refresh_token_history")

        async with sf() as db:
            rows = (
                await db.execute(
                    select(ExtensionRefreshToken).where(
                        ExtensionRefreshToken.extension_session_id.in_(
                            [active_id, revoked_id]
                        )
                    )
                )
            ).scalars().all()
            assert len(rows) == 2
            by_session = {row.extension_session_id: row for row in rows}
            active_row = by_session[active_id]
            revoked_row = by_session[revoked_id]
            assert active_row.revoked_at is None
            assert revoked_row.revoked_at == source_revoked_at
            assert active_row.created_at == source_created_at
            assert revoked_row.created_at == source_created_at
            assert active_row.token_hash == hashlib.sha256(active_token.encode()).hexdigest()
            assert revoked_row.token_hash == hashlib.sha256(revoked_token.encode()).hexdigest()
            assert active_token not in {row.token_hash for row in rows}
            assert revoked_token not in {row.token_hash for row in rows}
            assert active_row.token_family
            assert revoked_row.token_family

            service = ExtensionAuthService(
                repository=ExtensionRepository(db), settings=settings
            )
            refreshed = await service.refresh(active_token)
            assert refreshed.refresh_token
            await db.commit()
            with pytest.raises(ExtensionSessionRevokedError):
                await service.refresh(revoked_token)
    finally:
        command.upgrade(config, "head")


async def test_concurrent_extension_refresh_has_one_success(
    engine: AsyncEngine, settings: Settings
) -> None:
    """PostgreSQL row locking permits only one concurrent refresh winner."""
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "a@test.com")
        service = ExtensionAuthService(
            repository=ExtensionRepository(db), settings=settings
        )
        code, _ = await service.generate_pairing_code(user_id=user.id)
        pair = await service.complete_pairing(code)
        await db.commit()

    async def attempt() -> str:
        async with sf() as db:
            worker = ExtensionAuthService(
                repository=ExtensionRepository(db), settings=settings
            )
            try:
                await worker.refresh(pair.refresh_token)
                await db.commit()
                return "success"
            except ExtensionSessionRevokedError:
                await db.commit()
                return "revoked"

    outcomes = await asyncio.gather(attempt(), attempt())
    assert sorted(outcomes) == ["revoked", "success"]


async def test_concurrent_web_refresh_has_one_success_without_family_revocation(
    engine: AsyncEngine, settings: Settings
) -> None:
    """Concurrent web refreshes have one winner and a safe grace loser."""
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "a@test.com")
        user.hashed_password = hash_password("password123")
        await db.commit()
        tokens = await _make_svc(db, settings).authenticate(
            email="a@test.com", password="password123"
        )
        await db.commit()

    async def attempt() -> str:
        async with sf() as db:
            try:
                await _make_svc(db, settings).refresh(
                    refresh_token=tokens.refresh_token
                )
                await db.commit()
                return "success"
            except InvalidTokenError:
                await db.commit()
                return "invalid"

    outcomes = await asyncio.gather(attempt(), attempt())
    assert sorted(outcomes) == ["invalid", "success"]

    async with sf() as db:
        rows = (
            await db.execute(
                select(WebRefreshSession).order_by(WebRefreshSession.created_at)
            )
        ).scalars().all()
        assert len(rows) == 2
        assert sum(row.revoked_at is None for row in rows) == 1
        assert len({row.token_family for row in rows}) == 1


async def test_recent_web_reuse_within_grace_does_not_revoke_family(
    engine: AsyncEngine, settings: Settings
) -> None:
    """A stale token inside the configured grace window is rejected safely."""
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "a@test.com")
        user.hashed_password = hash_password("password123")
        await db.commit()
        svc = _make_svc(db, settings)
        tokens = await svc.authenticate(email="a@test.com", password="password123")
        await svc.refresh(refresh_token=tokens.refresh_token)
        old = (
            await db.execute(
                select(WebRefreshSession).where(
                    WebRefreshSession.refresh_token_hash
                    == hashlib.sha256(tokens.refresh_token.encode()).hexdigest()
                )
            )
        ).scalar_one()
        old.last_used_at = datetime.now(UTC) - timedelta(
            seconds=max(settings.web_refresh_reuse_grace_seconds - 1, 0)
        )
        await db.flush()
        with pytest.raises(InvalidTokenError):
            await svc.refresh(refresh_token=tokens.refresh_token)
        await db.commit()

    async with sf() as db:
        rows = (await db.execute(select(WebRefreshSession))).scalars().all()
        assert sum(row.revoked_at is None for row in rows) == 1


async def test_refresh_with_revoked_token_fails(
    engine: AsyncEngine, settings: Settings
) -> None:
    """A logged-out token cannot be used to refresh."""
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "a@test.com")
        from app.core.security import hash_password
        user.hashed_password = hash_password("password123")
        await db.commit()

        svc = _make_svc(db, settings)
        tokens = await svc.authenticate(email="a@test.com", password="password123")
        await svc.logout(refresh_token=tokens.refresh_token)
        await db.commit()

        from app.domain.auth.exceptions import InvalidTokenError
        with pytest.raises(InvalidTokenError):
            await svc.refresh(refresh_token=tokens.refresh_token)


async def test_refresh_with_inactive_user_fails(
    engine: AsyncEngine, settings: Settings
) -> None:
    """A valid refresh token cannot revive an inactive account."""
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "a@test.com")
        user.hashed_password = hash_password("password123")
        await db.commit()

        svc = _make_svc(db, settings)
        tokens = await svc.authenticate(email="a@test.com", password="password123")
        user.is_active = False
        await db.commit()

        with pytest.raises(InvalidTokenError):
            await svc.refresh(refresh_token=tokens.refresh_token)


# ── Cryptographic JWT validation in session-based path ────────────────────


async def test_expired_refresh_token_rejected(
    engine: AsyncEngine, settings: Settings
) -> None:
    """An expired JWT is rejected by decode_token() before the hash lookup."""
    from datetime import UTC, datetime, timedelta

    import jwt as pyjwt

    # Build an expired refresh token manually.
    now = datetime.now(UTC)
    payload = {
        "sub": "1",
        "type": "refresh",
        "jti": "expired-jti",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int((now - timedelta(days=30)).timestamp()),
        "exp": int((now - timedelta(days=1)).timestamp()),
    }
    expired_token = pyjwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )

    # Insert the expired token's hash into the DB so the hash lookup would
    # succeed if decode_token() were not called first.
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "a@test.com")
        sess = WebRefreshSession(
            user_id=user.id,
            refresh_token_hash=hashlib.sha256(expired_token.encode()).hexdigest(),
            token_family="expired-family",
        )
        db.add(sess)
        await db.commit()

        svc = _make_svc(db, settings)
        from app.domain.auth.exceptions import InvalidTokenError
        with pytest.raises(InvalidTokenError):
            await svc.refresh(refresh_token=expired_token)


async def test_access_token_used_as_refresh_rejected(
    engine: AsyncEngine, settings: Settings
) -> None:
    """An access token presented as a refresh token is rejected (type check)."""
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "a@test.com")
        from app.core.security import hash_password
        user.hashed_password = hash_password("password123")
        await db.commit()

        svc = _make_svc(db, settings)
        tokens = await svc.authenticate(email="a@test.com", password="password123")
        # tokens.access_token is of type "access", not "refresh".
        access = tokens.access_token

        from app.domain.auth.exceptions import InvalidTokenError
        with pytest.raises(InvalidTokenError):
            await svc.refresh(refresh_token=access)


async def test_invalid_signature_token_rejected(
    engine: AsyncEngine, settings: Settings
) -> None:
    """A token signed with a different secret is rejected."""
    import jwt as pyjwt

    forged = pyjwt.encode(
        {"sub": "1", "type": "refresh", "exp": 9999999999, "iat": 1},
        "wrong-secret-that-does-not-match-the-real-one",
        algorithm=settings.jwt_algorithm,
    )

    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        await _create_user(db, 1, "a@test.com")
        await db.commit()

        svc = _make_svc(db, settings)
        from app.domain.auth.exceptions import InvalidTokenError
        with pytest.raises(InvalidTokenError):
            await svc.refresh(refresh_token=forged)


async def test_wrong_audience_token_rejected(
    engine: AsyncEngine, settings: Settings
) -> None:
    """A token with the wrong audience claim is rejected."""
    from datetime import UTC, datetime, timedelta

    import jwt as pyjwt

    # Build a token with a deliberately wrong audience.
    now = datetime.now(UTC)
    payload = {
        "sub": "1",
        "type": "refresh",
        "jti": "wrong-aud-jti",
        "iss": settings.jwt_issuer,
        "aud": "wrong-audience",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=1)).timestamp()),
    }
    wrong_aud_token = pyjwt.encode(
        payload, settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )

    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "a@test.com")
        # Insert hash so session lookup would find it.
        sess = WebRefreshSession(
            user_id=user.id,
            refresh_token_hash=hashlib.sha256(wrong_aud_token.encode()).hexdigest(),
            token_family="wrong-aud-family",
        )
        db.add(sess)
        await db.commit()

        svc = _make_svc(db, settings)
        from app.domain.auth.exceptions import InvalidTokenError
        with pytest.raises(InvalidTokenError):
            await svc.refresh(refresh_token=wrong_aud_token)


async def test_wrong_issuer_token_rejected(
    engine: AsyncEngine, settings: Settings
) -> None:
    """A token with the wrong issuer claim is rejected."""
    from datetime import UTC, datetime, timedelta

    import jwt as pyjwt

    # Build a token with a deliberately wrong issuer.
    now = datetime.now(UTC)
    payload = {
        "sub": "1",
        "type": "refresh",
        "jti": "wrong-iss-jti",
        "iss": "wrong-issuer",
        "aud": settings.jwt_audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=1)).timestamp()),
    }
    wrong_iss_token = pyjwt.encode(
        payload, settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )

    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "a@test.com")
        sess = WebRefreshSession(
            user_id=user.id,
            refresh_token_hash=hashlib.sha256(wrong_iss_token.encode()).hexdigest(),
            token_family="wrong-iss-family",
        )
        db.add(sess)
        await db.commit()

        svc = _make_svc(db, settings)
        from app.domain.auth.exceptions import InvalidTokenError
        with pytest.raises(InvalidTokenError):
            await svc.refresh(refresh_token=wrong_iss_token)


async def test_jwt_user_id_mismatch_session_rejected(
    engine: AsyncEngine, settings: Settings
) -> None:
    """JWT decodes to user A but session row belongs to user B — rejected."""
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user_a = await _create_user(db, 1, "a@test.com")
        user_b = await _create_user(db, 2, "b@test.com")
        await db.commit()

        # Create a valid refresh token for user A.
        token_for_a = create_token(
            settings=settings, subject=user_a.id, token_type="refresh",
        )

        # But insert the hash into a session that points to user B.
        sess = WebRefreshSession(
            user_id=user_b.id,
            refresh_token_hash=hashlib.sha256(token_for_a.encode()).hexdigest(),
            token_family="mismatch-family",
        )
        db.add(sess)
        await db.commit()

        svc = _make_svc(db, settings)
        from app.domain.auth.exceptions import InvalidTokenError
        with pytest.raises(InvalidTokenError):
            await svc.refresh(refresh_token=token_for_a)


# ── last_used_at tracking ─────────────────────────────────────────────────


async def test_last_used_at_updated_on_refresh(
    engine: AsyncEngine, settings: Settings
) -> None:
    """After a successful refresh, the old session has last_used_at set."""
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = await _create_user(db, 1, "a@test.com")
        from app.core.security import hash_password
        user.hashed_password = hash_password("password123")
        await db.commit()

        svc = _make_svc(db, settings)
        tokens = await svc.authenticate(email="a@test.com", password="password123")

        # Refresh.
        await svc.refresh(refresh_token=tokens.refresh_token)
        await db.commit()

    async with sf() as db:
        result = await db.execute(
            select(WebRefreshSession).order_by(WebRefreshSession.created_at)
        )
        sessions = result.scalars().all()
        assert len(sessions) == 2
        # The first (old) session should have last_used_at set.
        old_s = sessions[0]
        assert old_s.last_used_at is not None
        assert old_s.revoked_at is not None
        # The new session should not have been used yet.
        new_s = sessions[1]
        assert new_s.last_used_at is None
        assert new_s.revoked_at is None


# ── Password Reset Production Safety Tests ──────────────────────────────────


async def test_forgot_password_dev_returns_reset_url(engine: AsyncEngine) -> None:
    """In development, forgot_password returns reset_url for convenience."""
    from pydantic import SecretStr

    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    dev_settings = Settings(
        environment="development",
        jwt_secret=SecretStr("test-secret-for-testing-at-least-32-chars!!"),
    )

    async with sf() as db:
        user = await _create_user(db, 1, "a@test.com")
        from app.core.security import hash_password
        user.hashed_password = hash_password("password123")
        await db.commit()

        svc = _make_svc(db, dev_settings)
        result = await svc.forgot_password(email="a@test.com")
        assert "reset_url" in result
        assert result["message"] == "If the email is registered, a reset link has been sent."


async def test_forgot_password_prod_excludes_reset_url(engine: AsyncEngine) -> None:
    """In production, forgot_password does NOT leak reset_url."""
    from pydantic import SecretStr

    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    prod_settings = Settings(
        environment="production",
        jwt_secret=SecretStr("a-strong-production-secret-at-least-32-chars!!"),
        refresh_cookie_secure=True,
        job_refresh_cooldown_minutes=15,
    )

    async with sf() as db:
        user = await _create_user(db, 2, "b@test.com")
        from app.core.security import hash_password
        user.hashed_password = hash_password("password123")
        await db.commit()

        svc = _make_svc(db, prod_settings)
        result = await svc.forgot_password(email="b@test.com")
        assert "reset_url" not in result
        assert result["message"] == "If the email is registered, a reset link has been sent."


async def test_forgot_password_unknown_email_no_leak(engine: AsyncEngine) -> None:
    """forgot_password returns generic message for unknown email (no user enum)."""
    from pydantic import SecretStr

    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    dev_settings = Settings(
        environment="development",
        jwt_secret=SecretStr("test-secret-for-testing-at-least-32-chars!!"),
    )

    async with sf() as db:
        svc = _make_svc(db, dev_settings)
        result = await svc.forgot_password(email="nonexistent@test.com")
        assert "reset_url" not in result
        assert result["message"] == "If the email is registered, a reset link has been sent."


class _FakeEmailProvider:
    """Records calls for test verification."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def send_password_reset(self, *, to_email: str, reset_url: str) -> None:
        self.calls.append({"to_email": to_email, "reset_url": reset_url})


async def test_forgot_password_prod_calls_email_provider(engine: AsyncEngine) -> None:
    """In production with email provider, forgot_password calls the provider."""
    from pydantic import SecretStr

    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    prod_settings = Settings(
        environment="production",
        jwt_secret=SecretStr("a-strong-production-secret-at-least-32-chars!!"),
        refresh_cookie_secure=True,
        job_refresh_cooldown_minutes=15,
    )

    async with sf() as db:
        user = await _create_user(db, 3, "c@test.com")
        from app.core.security import hash_password
        user.hashed_password = hash_password("password123")
        await db.commit()

        svc = _make_svc(db, prod_settings)
        provider = _FakeEmailProvider()
        result = await svc.forgot_password(
            email="c@test.com", email_provider=provider
        )
        # Response must never leak reset_url in production
        assert "reset_url" not in result
        # Provider must have been called
        assert len(provider.calls) == 1
        assert provider.calls[0]["to_email"] == "c@test.com"
        assert "/reset-password?token=" in provider.calls[0]["reset_url"]


async def test_forgot_password_no_provider_no_leak(engine: AsyncEngine) -> None:
    """In production without email provider, no reset_url is leaked."""
    from pydantic import SecretStr

    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    prod_settings = Settings(
        environment="production",
        jwt_secret=SecretStr("a-strong-production-secret-at-least-32-chars!!"),
        refresh_cookie_secure=True,
        job_refresh_cooldown_minutes=15,
    )

    async with sf() as db:
        user = await _create_user(db, 4, "d@test.com")
        from app.core.security import hash_password
        user.hashed_password = hash_password("password123")
        await db.commit()

        svc = _make_svc(db, prod_settings)
        # No email_provider passed
        result = await svc.forgot_password(email="d@test.com")
        assert "reset_url" not in result
        assert result["message"] == "If the email is registered, a reset link has been sent."
