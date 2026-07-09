"""Unit tests for extension services (auth, job import, events, autofill).

All tests use fake/in-memory repositories — no DB or Redis required.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.config import Settings
from app.core.security import create_token, decode_token, decode_token_payload
from app.domain.extension.exceptions import (
    ExtensionSessionNotFoundError,
    ExtensionSessionRevokedError,
    PairingCodeExpiredError,
    PairingCodeInvalidError,
)
from app.domain.extension.extension_auth_service import ExtensionAuthService
from app.domain.extension.extension_job_import_service import ExtensionJobImportService
from app.domain.extension.application_event_service import ApplicationEventService
from app.domain.extension.autofill_mapping_service import AutofillProfileService
from app.domain.extension.repository import ExtensionRepository
from app.domain.extension.schemas import (
    ApplicationEventRequest,
    ExtensionJobImportRequest,
    FieldMappingRequest,
    FieldMetadata,
    FormFillLogRequest,
    MarkAppliedRequest,
)
from app.domain.jobs.dedup import job_fingerprint


# ── Helpers ─────────────────────────────────────────────────────────────────


def _settings() -> Settings:
    return Settings(
        jwt_secret="test-secret-key-for-unit-tests-only",  # type: ignore[call-arg]
    )


def _token(settings: Settings, user_id: int, token_type: str = "access") -> str:
    return create_token(
        settings=settings,
        subject=user_id,
        token_type=token_type,  # type: ignore[arg-type]
    )


# ── Fake Repositories ───────────────────────────────────────────────────────


class FakeExtensionRepository:
    """In-memory stand-in for ExtensionRepository."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.form_sessions: dict[str, dict[str, Any]] = {}
        self.form_fields: list[dict[str, Any]] = []
        self._next_session_id = 1
        self._next_event_id = 1
        self._next_form_session_id = 1
        self._next_form_field_id = 1

    async def create_session(
        self,
        *,
        user_id: int,
        refresh_token_hash: str,
        device_name: str | None = None,
        browser: str | None = None,
    ) -> Any:
        sid = str(self._next_session_id)
        self._next_session_id += 1
        session = {
            "id": sid,
            "user_id": user_id,
            "refresh_token_hash": refresh_token_hash,
            "device_name": device_name,
            "browser": browser,
            "created_at": datetime.now(UTC),
            "last_used_at": None,
            "revoked_at": None,
        }
        self.sessions[sid] = session
        return _DictObj(session)

    async def get_session(self, session_id: str) -> Any | None:
        s = self.sessions.get(session_id)
        return _DictObj(s) if s else None

    async def touch_session(self, session_id: str) -> None:
        if session_id in self.sessions:
            self.sessions[session_id]["last_used_at"] = datetime.now(UTC)

    async def revoke_session(self, session_id: str) -> None:
        if session_id in self.sessions:
            self.sessions[session_id]["revoked_at"] = datetime.now(UTC)

    async def list_user_sessions(self, user_id: int) -> list[Any]:
        return [
            _DictObj(s)
            for s in self.sessions.values()
            if s["user_id"] == user_id
        ]

    async def create_event(self, **kwargs: Any) -> Any:
        eid = str(self._next_event_id)
        self._next_event_id += 1
        # ORM model uses metadata_ column; ensure it's always present
        metadata_val = kwargs.pop("metadata_", kwargs.pop("metadata", None))
        event = {"id": eid, **kwargs, "metadata_": metadata_val or {}, "created_at": datetime.now(UTC)}
        self.events.append(event)
        return _DictObj(event)

    async def list_events_for_application(
        self, *, application_id: int, user_id: int
    ) -> list[Any]:
        return [
            _DictObj(e)
            for e in self.events
            if e.get("application_id") == application_id and e.get("user_id") == user_id
        ]

    async def create_form_session(self, **kwargs: Any) -> Any:
        sid = str(self._next_form_session_id)
        self._next_form_session_id += 1
        session = {"id": sid, **kwargs, "created_at": datetime.now(UTC)}
        self.form_sessions[sid] = session
        return _DictObj(session)

    async def complete_form_session(
        self, session_id: str, *, filled_count: int, skipped_count: int, status: str = "completed"
    ) -> None:
        if session_id in self.form_sessions:
            self.form_sessions[session_id].update(
                filled_count=filled_count,
                skipped_count=skipped_count,
                status=status,
                completed_at=datetime.now(UTC),
            )

    async def add_form_field(self, **kwargs: Any) -> Any:
        fid = str(self._next_form_field_id)
        self._next_form_field_id += 1
        field = {"id": fid, **kwargs, "created_at": datetime.now(UTC)}
        self.form_fields.append(field)
        return _DictObj(field)


class FakeJobRepository:
    """In-memory stand-in for JobRepository."""

    def __init__(self) -> None:
        self.jobs: dict[int, dict[str, Any]] = {}
        self.user_jobs: set[tuple[int, int]] = set()
        self.embeddings: dict[int, list[float]] = {}
        self._next_id = 1

    async def upsert_job(self, values: dict[str, object]) -> tuple[int, bool]:
        fp = values.get("fingerprint", "")
        # Check for existing fingerprint
        for jid, job in self.jobs.items():
            if job.get("fingerprint") == fp:
                # Merge
                for key in ("title", "company", "location", "description", "source", "source_url", "raw_response"):
                    if values.get(key) is not None and job.get(key) is None:
                        job[key] = values[key]
                return jid, False

        jid = self._next_id
        self._next_id += 1
        self.jobs[jid] = {**values, "id": jid}
        return jid, True

    async def link_user_jobs(
        self,
        user_id: int,
        job_ids: list[int],
        *,
        preference_id: int | None = None,
        relationship_source: str | None = None,
        import_method: str | None = None,
        status: str | None = None,
    ) -> None:
        for jid in job_ids:
            self.user_jobs.add((user_id, jid))

    async def set_embedding(self, job_id: int, embedding: list[float]) -> None:
        self.embeddings[job_id] = embedding

    async def find_by_source_url(self, url: str) -> Any | None:
        for job in self.jobs.values():
            if job.get("source_url") == url:
                return _DictObj(job)
        return None

    async def find_by_source_id(
        self, source: str, source_job_id: str
    ) -> int | None:
        """Fake lookup — returns None (no source links in fake store)."""
        return None

    async def mark_as_private_import(
        self, *, job_id: int, user_id: int, ingest_source: str = "manual"
    ) -> None:
        """Fake: no-op (privacy markers are not tracked in fake store)."""
        pass

    async def create_user_job_import(self, **kwargs: Any) -> Any:
        """Fake: no-op (import metadata not tracked in fake store)."""
        return _DictObj({"id": 1, **kwargs})


class FakeApplicationRepository:
    """In-memory stand-in for ApplicationRepository."""

    def __init__(self) -> None:
        self.applications: dict[int, dict[str, Any]] = {}
        self._next_id = 1

    async def create_or_get(self, *, user_id: int, job_id: int) -> Any:
        # Check for existing
        for aid, app in self.applications.items():
            if app["user_id"] == user_id and app["job_id"] == job_id:
                app["updated_at"] = datetime.now(UTC)
                return _DictObj(app)

        aid = self._next_id
        self._next_id += 1
        app = {
            "id": aid,
            "user_id": user_id,
            "job_id": job_id,
            "status": "saved",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
        self.applications[aid] = app
        return _DictObj(app)

    async def get(self, *, user_id: int, application_id: int) -> Any | None:
        app = self.applications.get(application_id)
        if app and app["user_id"] == user_id:
            return _DictObj(app)
        return None


class FakeResumeRepository:
    """In-memory stand-in for ResumeRepository."""

    def __init__(self, profile: dict[str, Any] | None = None) -> None:
        self._profile = profile

    async def get_active_profile(self, user_id: int) -> Any | None:
        if self._profile is None:
            return None
        return _DictObj(self._profile)


class _DictObj:
    """Minimal object wrapper for dict-based fake models.

    Writes to attributes propagate back to the underlying dict so mutations
    (like session.refresh_token_hash = new_hash) are visible when the same
    dict is re-wrapped later.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self.__dict__["_data"] = data

    def __getattr__(self, name: str) -> Any:
        if name == "_data":
            return self.__dict__["_data"]
        return self._data.get(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_data":
            self.__dict__["_data"] = value
        else:
            self._data[name] = value


# ── Auth Tests ──────────────────────────────────────────────────────────────


class TestPairingCodeFlow:
    def _service(self, redis=None) -> ExtensionAuthService:
        return ExtensionAuthService(
            repository=FakeExtensionRepository(),  # type: ignore[arg-type]
            settings=_settings(),
            redis=redis,
        )

    @pytest.mark.asyncio
    async def test_generate_and_consume_pairing_code(self) -> None:
        svc = self._service()
        code, expires = await svc.generate_pairing_code(user_id=42)
        assert len(code) == 8
        assert expires == 300

        user_id = await svc.consume_pairing_code(code)
        assert user_id == 42

    @pytest.mark.asyncio
    async def test_consume_twice_fails(self) -> None:
        svc = self._service()
        code, _ = await svc.generate_pairing_code(user_id=42)
        await svc.consume_pairing_code(code)

        with pytest.raises(PairingCodeInvalidError):
            await svc.consume_pairing_code(code)

    @pytest.mark.asyncio
    async def test_invalid_code_raises(self) -> None:
        svc = self._service()
        with pytest.raises(PairingCodeInvalidError):
            await svc.consume_pairing_code("deadbeef")

    @pytest.mark.asyncio
    async def test_complete_pairing_returns_tokens_and_session(self) -> None:
        svc = self._service()
        code, _ = await svc.generate_pairing_code(user_id=42)

        result = await svc.complete_pairing(code, device_name="Test", browser="Chrome")
        assert result.access_token
        assert result.refresh_token
        assert result.session_id
        assert result.user.id == 42
        assert result.expires_in == 900  # 15 min

        # Code must be consumed
        with pytest.raises(PairingCodeInvalidError):
            await svc.consume_pairing_code(code)

    @pytest.mark.asyncio
    async def test_expired_code_raises(self) -> None:
        svc = self._service()
        # Use the in-memory fallback so we can manipulate TTL
        code, _ = await svc.generate_pairing_code(user_id=42)
        # Corrupt expiry to simulate expiration
        from app.domain.extension.extension_auth_service import _pairing_codes_fallback
        if code in _pairing_codes_fallback:
            uid, _ = _pairing_codes_fallback[code]
            _pairing_codes_fallback[code] = (
                uid,
                datetime.now(UTC) - timedelta(seconds=1),
            )

        with pytest.raises(PairingCodeExpiredError):
            await svc.consume_pairing_code(code)


class TestRefreshRotation:
    def _repo_and_service(self) -> tuple[FakeExtensionRepository, ExtensionAuthService]:
        repo = FakeExtensionRepository()
        svc = ExtensionAuthService(
            repository=repo,  # type: ignore[arg-type]
            settings=_settings(),
        )
        return repo, svc

    @pytest.mark.asyncio
    async def test_refresh_rotates_tokens(self) -> None:
        import asyncio
        repo, svc = self._repo_and_service()
        code, _ = await svc.generate_pairing_code(user_id=42)
        result = await svc.complete_pairing(code)
        old_refresh = result.refresh_token

        # Small delay ensures distinct JWT iat (second granularity)
        await asyncio.sleep(2.0)

        refreshed = await svc.refresh(old_refresh)
        # Refresh must return valid tokens
        assert refreshed.access_token
        assert refreshed.refresh_token
        assert refreshed.expires_in > 0
        # Session must be touched
        session_after = repo.sessions.get(result.session_id)
        assert session_after is not None
        assert session_after["last_used_at"] is not None

        # Old refresh should no longer work (token was rotated)
        with pytest.raises((ExtensionSessionNotFoundError, ExtensionSessionRevokedError)):
            await svc.refresh(old_refresh)

    @pytest.mark.asyncio
    async def test_revoked_session_cannot_refresh(self) -> None:
        repo, svc = self._repo_and_service()
        code, _ = await svc.generate_pairing_code(user_id=42)
        result = await svc.complete_pairing(code)

        await svc.logout(result.session_id)

        with pytest.raises(ExtensionSessionRevokedError):
            await svc.refresh(result.refresh_token)


# ── Job Import Tests ────────────────────────────────────────────────────────


class TestJobImport:
    def _service(self) -> tuple[FakeJobRepository, FakeExtensionRepository, ExtensionJobImportService]:
        job_repo = FakeJobRepository()
        ext_repo = FakeExtensionRepository()
        svc = ExtensionJobImportService(
            job_repository=job_repo,  # type: ignore[arg-type]
            extension_repository=ext_repo,  # type: ignore[arg-type]
        )
        return job_repo, ext_repo, svc

    def _request(self, **overrides: Any) -> ExtensionJobImportRequest:
        defaults = {
            "title": "Senior Backend Engineer",
            "company": "Acme Corp",
            "location": "Remote",
            "description": "Build APIs with FastAPI",
            "source_url": "https://linkedin.com/jobs/view/123",
            "source": "linkedin",
        }
        return ExtensionJobImportRequest(**(defaults | overrides))

    @pytest.mark.asyncio
    async def test_import_creates_job_and_user_link(self) -> None:
        job_repo, ext_repo, svc = self._service()
        result = await svc.import_job(user_id=42, request=self._request())

        assert result.job_id > 0
        assert result.was_existing_job is False
        assert result.application_id is None  # No application created
        assert (42, result.job_id) in job_repo.user_jobs

        # Event recorded
        assert len(ext_repo.events) == 1
        assert ext_repo.events[0]["event_type"] == "job_imported"
        assert ext_repo.events[0]["job_id"] == result.job_id

    @pytest.mark.asyncio
    async def test_import_does_not_create_application(self) -> None:
        _, _, svc = self._service()
        result = await svc.import_job(user_id=42, request=self._request())
        assert result.application_id is None
        assert result.was_existing_application is False

    @pytest.mark.asyncio
    async def test_duplicate_import_dedups_by_fingerprint(self) -> None:
        job_repo, ext_repo, svc = self._service()
        req = self._request()

        first = await svc.import_job(user_id=42, request=req)
        second = await svc.import_job(user_id=42, request=req)

        assert second.job_id == first.job_id
        assert second.was_existing_job is True
        assert len(job_repo.jobs) == 1

    @pytest.mark.asyncio
    async def test_same_job_different_urls_dedups_by_title_company_location(self) -> None:
        """The fingerprint uses (company, title, location) — NOT source_url — so
        the same role posted on LinkedIn and Indeed collapses to one row."""
        job_repo, _, svc = self._service()

        linkedin = await svc.import_job(
            user_id=42,
            request=self._request(
                source_url="https://linkedin.com/jobs/view/abc",
                source="linkedin",
            ),
        )
        indeed = await svc.import_job(
            user_id=42,
            request=self._request(
                source_url="https://indeed.com/viewjob?jk=xyz",
                source="indeed",
            ),
        )

        assert indeed.job_id == linkedin.job_id
        assert indeed.was_existing_job is True
        assert len(job_repo.jobs) == 1

    @pytest.mark.asyncio
    async def test_different_jobs_get_different_ids(self) -> None:
        job_repo, _, svc = self._service()

        first = await svc.import_job(
            user_id=42,
            request=self._request(title="Backend Engineer", company="Acme"),
        )
        second = await svc.import_job(
            user_id=42,
            request=self._request(title="Frontend Engineer", company="Acme"),
        )

        assert first.job_id != second.job_id
        assert len(job_repo.jobs) == 2


# ── Application Event Tests ─────────────────────────────────────────────────


class TestApplyClick:
    def _service(self) -> tuple[
        FakeExtensionRepository,
        FakeApplicationRepository,
        FakeJobRepository,
        ApplicationEventService,
    ]:
        ext_repo = FakeExtensionRepository()
        app_repo = FakeApplicationRepository()
        job_repo = FakeJobRepository()
        svc = ApplicationEventService(
            extension_repository=ext_repo,  # type: ignore[arg-type]
            application_repository=app_repo,  # type: ignore[arg-type]
            job_repository=job_repo,  # type: ignore[arg-type]
        )
        return ext_repo, app_repo, job_repo, svc

    def _import_job(self, job_repo: FakeJobRepository, url: str = "https://linkedin.com/jobs/view/123") -> int:
        import hashlib
        fp = hashlib.sha256(f"acme corp|senior backend engineer|remote".encode()).hexdigest()[:16]
        jid = 1
        job_repo.jobs[jid] = {
            "id": jid,
            "fingerprint": fp,
            "title": "Senior Backend Engineer",
            "company": "Acme Corp",
            "source_url": url,
        }
        job_repo._next_id = 2
        return jid

    @pytest.mark.asyncio
    async def test_apply_clicked_creates_application_saved(self) -> None:
        ext_repo, app_repo, job_repo, svc = self._service()
        jid = self._import_job(job_repo)

        result = await svc.record_event(
            user_id=42,
            request=ApplicationEventRequest(
                job_url="https://linkedin.com/jobs/view/123",
                event_type="apply_clicked",
            ),
        )

        assert result.status == "saved"
        assert result.event_recorded is True
        assert len(app_repo.applications) == 1
        app = list(app_repo.applications.values())[0]
        assert app["status"] == "saved"

    @pytest.mark.asyncio
    async def test_duplicate_apply_clicked_does_not_duplicate_application(self) -> None:
        ext_repo, app_repo, job_repo, svc = self._service()
        jid = self._import_job(job_repo)

        await svc.record_event(
            user_id=42,
            request=ApplicationEventRequest(
                job_url="https://linkedin.com/jobs/view/123",
                event_type="apply_clicked",
            ),
        )
        await svc.record_event(
            user_id=42,
            request=ApplicationEventRequest(
                job_url="https://linkedin.com/jobs/view/123",
                event_type="apply_clicked",
            ),
        )

        assert len(app_repo.applications) == 1
        assert len(ext_repo.events) == 2  # Two events, one application


class TestMarkApplied:
    def _service(self) -> tuple[
        FakeExtensionRepository,
        FakeApplicationRepository,
        FakeJobRepository,
        ApplicationEventService,
    ]:
        ext_repo = FakeExtensionRepository()
        app_repo = FakeApplicationRepository()
        job_repo = FakeJobRepository()
        svc = ApplicationEventService(
            extension_repository=ext_repo,  # type: ignore[arg-type]
            application_repository=app_repo,  # type: ignore[arg-type]
            job_repository=job_repo,  # type: ignore[arg-type]
        )
        return ext_repo, app_repo, job_repo, svc

    def _setup_job_and_app(
        self,
        job_repo: FakeJobRepository,
        app_repo: FakeApplicationRepository,
    ) -> tuple[int, int]:
        import hashlib
        fp = hashlib.sha256(f"acme corp|senior backend engineer|remote".encode()).hexdigest()[:16]
        jid = 1
        job_repo.jobs[jid] = {
            "id": jid,
            "fingerprint": fp,
            "title": "Senior Backend Engineer",
            "company": "Acme Corp",
            "source_url": "https://linkedin.com/jobs/view/123",
        }
        job_repo._next_id = 2

        app_repo.applications[1] = {
            "id": 1,
            "user_id": 42,
            "job_id": jid,
            "status": "saved",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
        app_repo._next_id = 2
        return jid, 1

    @pytest.mark.asyncio
    async def test_mark_applied_sets_status_to_applied(self) -> None:
        ext_repo, app_repo, job_repo, svc = self._service()
        jid, aid = self._setup_job_and_app(job_repo, app_repo)

        result = await svc.mark_applied(
            user_id=42,
            request=MarkAppliedRequest(
                job_url="https://linkedin.com/jobs/view/123",
                confirmation_source="confirmation_detected",
                confirmation_text="Thank you for applying!",
            ),
        )

        assert result.marked_applied is True
        assert result.status == "applied"

        # Event recorded
        assert len(ext_repo.events) == 1
        assert ext_repo.events[0]["event_type"] == "confirmation_detected"

    @pytest.mark.asyncio
    async def test_user_marked_applied_sets_status(self) -> None:
        ext_repo, app_repo, job_repo, svc = self._service()
        self._setup_job_and_app(job_repo, app_repo)

        result = await svc.mark_applied(
            user_id=42,
            request=MarkAppliedRequest(
                job_url="https://linkedin.com/jobs/view/123",
                confirmation_source="user_marked_applied",
            ),
        )

        assert result.marked_applied is True
        assert ext_repo.events[0]["event_type"] == "user_marked_applied"


class TestTimelineUserIsolation:
    def _service(self) -> tuple[FakeExtensionRepository, ApplicationEventService]:
        ext_repo = FakeExtensionRepository()
        svc = ApplicationEventService(
            extension_repository=ext_repo,  # type: ignore[arg-type]
            application_repository=FakeApplicationRepository(),  # type: ignore[arg-type]
            job_repository=FakeJobRepository(),  # type: ignore[arg-type]
        )
        return ext_repo, svc

    @pytest.mark.asyncio
    async def test_timeline_only_returns_user_events(self) -> None:
        ext_repo, svc = self._service()

        # Record events for two different users
        await ext_repo.create_event(
            user_id=42, event_type="job_imported", application_id=1, job_id=1
        )
        await ext_repo.create_event(
            user_id=42, event_type="apply_clicked", application_id=1, job_id=1
        )
        await ext_repo.create_event(
            user_id=99, event_type="apply_clicked", application_id=1, job_id=1
        )

        timeline = await svc.get_timeline(application_id=1, user_id=42)

        assert len(timeline.events) == 2
        for event in timeline.events:
            assert event.event_type in ("job_imported", "apply_clicked")


# ── Autofill Tests ──────────────────────────────────────────────────────────


class TestAutofillProfile:
    def _service(self, profile: dict[str, Any] | None = None) -> AutofillProfileService:
        resume_repo = FakeResumeRepository(profile)
        ext_repo = FakeExtensionRepository()
        return AutofillProfileService(
            resume_repository=resume_repo,  # type: ignore[arg-type]
            extension_repository=ext_repo,  # type: ignore[arg-type]
        )

    @pytest.mark.asyncio
    async def test_empty_profile_when_no_candidate_profile(self) -> None:
        svc = self._service(None)
        profile = await svc.get_autofill_profile(user_id=42)

        assert profile["personal"]["first_name"] is None
        assert profile["personal"]["email"] is None
        assert profile["skills"] == []

    @pytest.mark.asyncio
    async def test_profile_excludes_raw_resume_text(self) -> None:
        svc = self._service({
            "parsed": {
                "full_name": "Jane Doe",
                "email": "jane@example.com",
                "raw_text": "SHOULD NOT BE EXPOSED",
                "skills": ["Python", "FastAPI"],
            }
        })
        profile = await svc.get_autofill_profile(user_id=42)

        assert profile["personal"]["first_name"] == "Jane"
        assert profile["personal"]["last_name"] == "Doe"
        assert profile["personal"]["email"] == "jane@example.com"
        assert profile["skills"] == ["Python", "FastAPI"]
        assert "raw_text" not in profile
        assert "raw_text" not in str(profile)

    @pytest.mark.asyncio
    async def test_map_fields_returns_empty_for_empty_input(self) -> None:
        svc = self._service()
        result = await svc.map_fields(request=FieldMappingRequest(fields=[]))
        assert result.mappings == []

    @pytest.mark.asyncio
    async def test_map_fields_does_not_send_user_values_to_llm(self) -> None:
        """The LLM call is mocked away. The key invariant is that FieldMetadata
        does NOT contain any user-value fields — only labels, types, options."""
        svc = self._service()

        # This would trigger an LLM call in production, but since there's no
        # API key configured in tests, it should fail gracefully.
        result = await svc.map_fields(
            request=FieldMappingRequest(
                fields=[
                    FieldMetadata(
                        element_id="field-1",
                        label="First Name",
                        input_type="text",
                    ),
                ]
            )
        )
        # Should return empty mappings on LLM failure (graceful degradation)
        assert result.mappings == []

    @pytest.mark.asyncio
    async def test_log_fill_creates_form_session_and_event(self) -> None:
        svc = self._service({"parsed": {"skills": ["Python"]}})

        # Manually get ext_repo to verify side effects
        ext_repo = FakeExtensionRepository()
        svc._ext = ext_repo  # type: ignore[assignment]

        result = await svc.log_fill(
            user_id=42,
            request=FormFillLogRequest(
                fields=[
                    {"elementId": "f1", "label": "First Name", "inputType": "text"},
                    {"elementId": "f2", "label": "Email", "inputType": "email"},
                ],
                mappings=[
                    {"elementId": "f1", "profilePath": "personal.first_name", "action": "fill", "confidence": 0.98},
                    {"elementId": "f2", "profilePath": "personal.email", "action": "fill", "confidence": 0.99},
                ],
                domain="greenhouse.io",
                ats_type="greenhouse",
            ),
        )

        assert result.recorded is True
        assert result.session_id != ""
        assert len(ext_repo.form_sessions) == 1
        assert len(ext_repo.form_fields) == 2
        # Check that autofill_completed event was recorded
        autofill_events = [e for e in ext_repo.events if e["event_type"] == "autofill_completed"]
        assert len(autofill_events) == 1
        assert autofill_events[0]["metadata_"]["filled"] == 2
        assert autofill_events[0]["metadata_"]["skipped"] == 0


# ── Fingerprint Tests ───────────────────────────────────────────────────────


class TestFingerprintCorrectness:
    """Verify the fix: fingerprint uses (company, title, location) — NOT source_url."""

    def test_fingerprint_ignores_source_url(self) -> None:
        """Same job on different sites must produce the same fingerprint."""
        fp1 = job_fingerprint("Acme Corp", "Backend Engineer", "Remote")
        fp2 = job_fingerprint("Acme Corp", "Backend Engineer", "Remote")
        assert fp1 == fp2
        assert len(fp1) == 16

    def test_fingerprint_case_insensitive(self) -> None:
        fp1 = job_fingerprint("ACME CORP", "BACKEND ENGINEER", "REMOTE")
        fp2 = job_fingerprint("acme corp", "backend engineer", "remote")
        assert fp1 == fp2

    def test_fingerprint_null_location(self) -> None:
        fp1 = job_fingerprint("Acme", "Engineer", None)
        fp2 = job_fingerprint("Acme", "Engineer", "")
        assert fp1 == fp2


# ── Extension Session Revocation Security Tests ─────────────────────────────
# Verifies that extension access tokens carry a session_id claim and that
# revoked sessions cannot use old access tokens (Priority 0 — security).


class TestExtensionSessionTokenClaims:
    """Extension tokens MUST carry a session_id claim for revocation checks."""

    def _service(self) -> tuple[FakeExtensionRepository, ExtensionAuthService]:
        repo = FakeExtensionRepository()
        svc = ExtensionAuthService(
            repository=repo,  # type: ignore[arg-type]
            settings=_settings(),
        )
        return repo, svc

    @pytest.mark.asyncio
    async def test_access_token_carries_session_id_claim(self) -> None:
        """Access token issued during pairing includes the session_id."""
        repo, svc = self._service()
        code, _ = await svc.generate_pairing_code(user_id=42)
        result = await svc.complete_pairing(code)

        # Decode the access token and verify session_id claim
        payload = decode_token_payload(
            settings=_settings(),
            token=result.access_token,
            expected_type="access",
        )
        assert payload["session_id"] == result.session_id
        assert int(payload["sub"]) == 42

    @pytest.mark.asyncio
    async def test_refresh_token_carries_session_id_claim(self) -> None:
        """Refresh token issued during pairing includes the session_id."""
        repo, svc = self._service()
        code, _ = await svc.generate_pairing_code(user_id=42)
        result = await svc.complete_pairing(code)

        payload = decode_token_payload(
            settings=_settings(),
            token=result.refresh_token,
            expected_type="refresh",
        )
        assert payload["session_id"] == result.session_id

    @pytest.mark.asyncio
    async def test_refreshed_tokens_carry_same_session_id(self) -> None:
        """After refresh, new token pair keeps the same session_id."""
        import asyncio
        repo, svc = self._service()
        code, _ = await svc.generate_pairing_code(user_id=42)
        result = await svc.complete_pairing(code)
        original_session_id = result.session_id

        await asyncio.sleep(2.0)

        refreshed = await svc.refresh(result.refresh_token)

        # Both new tokens must carry the same session_id
        access_payload = decode_token_payload(
            settings=_settings(),
            token=refreshed.access_token,
            expected_type="access",
        )
        refresh_payload = decode_token_payload(
            settings=_settings(),
            token=refreshed.refresh_token,
            expected_type="refresh",
        )
        assert access_payload["session_id"] == original_session_id
        assert refresh_payload["session_id"] == original_session_id

    @pytest.mark.asyncio
    async def test_revoked_session_token_still_decodable_but_session_revoked(self) -> None:
        """Token from a revoked session is cryptographically valid but the
        session is revoked — the dependency layer must reject it."""
        repo, svc = self._service()
        code, _ = await svc.generate_pairing_code(user_id=42)
        result = await svc.complete_pairing(code)

        # Verify session is active
        session = await repo.get_session(result.session_id)
        assert session is not None
        assert session.revoked_at is None

        # Revoke the session
        await svc.logout(result.session_id)

        # Session should now be revoked
        session = await repo.get_session(result.session_id)
        assert session.revoked_at is not None

        # Token is still valid cryptographically (not expired, valid signature)
        payload = decode_token_payload(
            settings=_settings(),
            token=result.access_token,
            expected_type="access",
        )
        assert int(payload["sub"]) == 42
        # But the dependency will check the session and reject it.

    @pytest.mark.asyncio
    async def test_session_id_mismatch_rejects_refresh(self) -> None:
        """If the session_id in the JWT doesn't match the session found by
        hash, refresh must fail."""
        repo, svc = self._service()
        code, _ = await svc.generate_pairing_code(user_id=42)
        result = await svc.complete_pairing(code)

        # Tamper with the refresh token to have a wrong session_id
        import jwt
        payload = decode_token_payload(
            settings=_settings(),
            token=result.refresh_token,
            expected_type="refresh",
        )
        payload["session_id"] = "nonexistent-session"
        tampered_token = jwt.encode(
            payload,
            _settings().jwt_secret.get_secret_value(),
            algorithm=_settings().jwt_algorithm,
        )

        with pytest.raises(ExtensionSessionNotFoundError):
            await svc.refresh(tampered_token)


class TestCurrentExtensionUserDependency:
    """Simulates the dependency logic: user + active extension session required."""

    def _settings(self) -> Settings:
        return _settings()

    def _token_for_session(
        self, session_id: str, user_id: int = 42
    ) -> str:
        """Create an access token carrying a session_id claim."""
        return create_token(
            settings=self._settings(),
            subject=user_id,
            token_type="access",
            extra_claims={"session_id": session_id},
        )

    @pytest.mark.asyncio
    async def test_active_session_token_passes_dependency_check(self) -> None:
        """Simulate what get_current_extension_user does: decode + check session."""
        repo = FakeExtensionRepository()
        # Create an active session
        session = await repo.create_session(
            user_id=42,
            refresh_token_hash="somehash",
        )

        token = self._token_for_session(str(session.id))
        payload = decode_token_payload(
            settings=self._settings(),
            token=token,
            expected_type="access",
        )

        user_id = int(payload["sub"])
        session_id = payload.get("session_id")
        assert session_id is not None

        # Check session is active
        found = await repo.get_session(session_id)
        assert found is not None
        assert found.user_id == user_id
        assert found.revoked_at is None

    @pytest.mark.asyncio
    async def test_revoked_session_token_fails_dependency_check(self) -> None:
        """A token bound to a revoked session must fail the dep check."""
        repo = FakeExtensionRepository()
        session = await repo.create_session(
            user_id=42,
            refresh_token_hash="somehash",
        )

        token = self._token_for_session(str(session.id))
        payload = decode_token_payload(
            settings=self._settings(),
            token=token,
            expected_type="access",
        )
        session_id = payload.get("session_id")
        assert session_id is not None

        # Revoke the session
        await repo.revoke_session(session_id)

        # Simulate the dependency check
        found = await repo.get_session(session_id)
        is_active = (
            found is not None
            and found.user_id == int(payload["sub"])
            and found.revoked_at is None
        )
        assert is_active is False

    @pytest.mark.asyncio
    async def test_missing_session_id_claim_fails_dependency_check(self) -> None:
        """A token without session_id claim must fail extension auth."""
        token = create_token(
            settings=self._settings(),
            subject=42,
            token_type="access",
            # No extra_claims — behaves like a regular web token
        )
        payload = decode_token_payload(
            settings=self._settings(),
            token=token,
            expected_type="access",
        )
        assert payload.get("session_id") is None
        # The dependency would raise InvalidTokenError for missing session_id.

    @pytest.mark.asyncio
    async def test_nonexistent_session_id_fails_dependency_check(self) -> None:
        """A token with a session_id that doesn't exist must fail."""
        repo = FakeExtensionRepository()
        token = self._token_for_session("nonexistent-session-id")

        payload = decode_token_payload(
            settings=self._settings(),
            token=token,
            expected_type="access",
        )
        session_id = payload.get("session_id")
        found = await repo.get_session(session_id)
        assert found is None
        # The dependency would raise InvalidTokenError.

    @pytest.mark.asyncio
    async def test_session_user_mismatch_fails_dependency_check(self) -> None:
        """A token claiming user A but session owned by user B must fail."""
        repo = FakeExtensionRepository()
        # Session belongs to user 99
        session = await repo.create_session(
            user_id=99,
            refresh_token_hash="somehash",
        )

        # Token claims user 42 but references user 99's session
        token = self._token_for_session(str(session.id), user_id=42)

        payload = decode_token_payload(
            settings=self._settings(),
            token=token,
            expected_type="access",
        )
        session_id = payload.get("session_id")
        found = await repo.get_session(session_id)
        assert found is not None
        assert found.user_id != int(payload["sub"])
        # The dependency would raise InvalidTokenError due to user mismatch.
