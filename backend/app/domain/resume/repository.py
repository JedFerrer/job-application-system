"""Resume/profile persistence. Repositories own all DB access for the module."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.resume.lifecycle import ResumeLifecycleStatus
from app.models.resume import CandidateProfile, Resume, TailoredResumeDraft
from app.models.user import User


class ResumeRepository:
    """Data access for ``resumes`` and ``candidate_profiles``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_user_for_import(self, user_id: int) -> None:
        """Serialize import classification for one authenticated user."""
        result = await self._session.execute(
            select(User.id).where(User.id == user_id).with_for_update()
        )
        if result.scalar_one_or_none() is None:
            raise ValueError(f"User {user_id} does not exist")

    async def deactivate_existing(self, user_id: int) -> None:
        """Mark only current active resumes inactive."""
        await self._session.execute(
            update(Resume)
            .where(Resume.user_id == user_id, Resume.is_active.is_(True))
            .values(
                is_active=False,
                lifecycle_status=ResumeLifecycleStatus.INACTIVE.value,
            )
        )

    async def add_resume(
        self,
        user_id: int,
        filename: str,
        raw_text: str,
        *,
        is_active: bool = True,
        lifecycle_status: ResumeLifecycleStatus = ResumeLifecycleStatus.ACTIVE,
        import_provenance: dict[str, object] | None = None,
    ) -> Resume:
        resume = Resume(
            user_id=user_id,
            filename=filename,
            raw_text=raw_text,
            is_active=is_active,
            lifecycle_status=lifecycle_status.value,
            import_provenance=import_provenance,
        )
        self._session.add(resume)
        await self._session.flush()
        return resume

    async def add_profile(
        self,
        *,
        user_id: int,
        resume_id: int,
        parsed: dict[str, object],
        summary_text: str,
        embedding: list[float],
        extraction_evidence: dict[str, object] | None = None,
    ) -> CandidateProfile:
        profile = CandidateProfile(
            user_id=user_id,
            resume_id=resume_id,
            parsed=parsed,
            extraction_evidence=extraction_evidence,
            summary_text=summary_text,
            embedding=embedding,
        )
        self._session.add(profile)
        await self._session.flush()
        return profile

    async def get_active_profile(self, user_id: int) -> CandidateProfile | None:
        """Return the profile tied to the user's active resume, if any."""
        result = await self._session.execute(
            select(CandidateProfile)
            .join(Resume, Resume.id == CandidateProfile.resume_id)
            .where(CandidateProfile.user_id == user_id, Resume.is_active.is_(True))
            .order_by(CandidateProfile.updated_at.desc())
        )
        return result.scalars().first()

    async def list_import_drafts(
        self, user_id: int
    ) -> list[tuple[Resume, CandidateProfile]]:
        result = await self._session.execute(
            select(Resume, CandidateProfile)
            .join(CandidateProfile, CandidateProfile.resume_id == Resume.id)
            .where(
                Resume.user_id == user_id,
                CandidateProfile.user_id == user_id,
                Resume.is_active.is_(False),
                Resume.lifecycle_status == ResumeLifecycleStatus.DRAFT.value,
            )
            .order_by(Resume.created_at.desc(), Resume.id.desc())
        )
        return list(result.tuples().all())

    async def get_import_draft(
        self, *, user_id: int, resume_id: int
    ) -> tuple[Resume, CandidateProfile] | None:
        result = await self._session.execute(
            select(Resume, CandidateProfile)
            .join(CandidateProfile, CandidateProfile.resume_id == Resume.id)
            .where(
                Resume.id == resume_id,
                Resume.user_id == user_id,
                CandidateProfile.user_id == user_id,
                Resume.is_active.is_(False),
                Resume.lifecycle_status == ResumeLifecycleStatus.DRAFT.value,
            )
        )
        return result.tuples().one_or_none()

    async def get_active_profile_for_resume(
        self, *, user_id: int, resume_id: int
    ) -> CandidateProfile | None:
        """Return an already-approved profile only when it is still active and owned."""
        result = await self._session.execute(
            select(CandidateProfile)
            .join(Resume, Resume.id == CandidateProfile.resume_id)
            .where(
                Resume.id == resume_id,
                Resume.user_id == user_id,
                Resume.is_active.is_(True),
                Resume.lifecycle_status == ResumeLifecycleStatus.ACTIVE.value,
                CandidateProfile.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def _capture_previous_active_source(
        self, *, user_id: int
    ) -> tuple[Resume, CandidateProfile] | None:
        """Capture exactly one valid active source/profile or fail closed."""
        result = await self._session.execute(
            select(Resume).where(
                Resume.user_id == user_id,
                Resume.is_active.is_(True),
            )
        )
        active_resumes = list(result.scalars().all())
        if len(active_resumes) > 1:
            raise ValueError("Multiple active resumes found; refusing broad cleanup")
        if not active_resumes:
            return None

        previous_resume = active_resumes[0]
        if previous_resume.lifecycle_status != ResumeLifecycleStatus.ACTIVE.value:
            raise ValueError("Active resume has an inconsistent lifecycle state")
        profile_result = await self._session.execute(
            select(CandidateProfile).where(
                CandidateProfile.resume_id == previous_resume.id,
            )
        )
        profiles = list(profile_result.scalars().all())
        if len(profiles) != 1 or profiles[0].user_id != user_id:
            raise ValueError(
                "Active resume does not have exactly one owned candidate profile"
            )
        return previous_resume, profiles[0]

    async def approve_import_draft(
        self, *, user_id: int, resume_id: int
    ) -> CandidateProfile | None:
        await self.lock_user_for_import(user_id)
        draft = await self.get_import_draft(user_id=user_id, resume_id=resume_id)
        if draft is None:
            # A client may retry after the transaction committed but before it
            # received the response. Return the already-active target without
            # attempting any additional cleanup.
            return await self.get_active_profile_for_resume(
                user_id=user_id, resume_id=resume_id
            )
        resume, profile = draft
        previous = await self._capture_previous_active_source(user_id=user_id)
        await self._session.execute(
            update(Resume)
            .where(Resume.id == resume.id, Resume.user_id == user_id)
            .values(
                is_active=True,
                lifecycle_status=ResumeLifecycleStatus.ACTIVE.value,
            )
        )
        await self._session.flush()

        if previous is not None:
            previous_resume, previous_profile = previous
            await self._session.delete(previous_profile)
            await self._session.flush()
            await self._session.delete(previous_resume)
            await self._session.flush()
        return profile

    async def reject_import_draft(self, *, user_id: int, resume_id: int) -> bool:
        await self.lock_user_for_import(user_id)
        draft = await self.get_import_draft(user_id=user_id, resume_id=resume_id)
        if draft is None:
            return False
        await self._session.execute(
            update(Resume)
            .where(Resume.id == resume_id, Resume.user_id == user_id)
            .values(
                is_active=False,
                lifecycle_status=ResumeLifecycleStatus.INACTIVE.value,
            )
        )
        await self._session.flush()
        return True

    async def update_profile_parsed(
        self,
        *,
        profile: CandidateProfile,
        parsed: dict[str, object],
        summary_text: str,
        embedding: list[float],
    ) -> CandidateProfile:
        profile.parsed = parsed
        profile.summary_text = summary_text
        profile.embedding = embedding
        await self._session.flush()
        return profile

    async def create_tailored_draft(
        self,
        *,
        user_id: int,
        job_id: int,
        source_profile_id: int,
        job_title: str,
        job_company: str | None,
        content: dict[str, object],
    ) -> TailoredResumeDraft:
        draft = TailoredResumeDraft(
            user_id=user_id,
            job_id=job_id,
            source_profile_id=source_profile_id,
            job_title=job_title,
            job_company=job_company,
            content=content,
        )
        self._session.add(draft)
        await self._session.flush()
        await self._session.refresh(draft)
        return draft

    async def list_tailored_drafts(
        self, *, user_id: int
    ) -> list[TailoredResumeDraft]:
        result = await self._session.execute(
            select(TailoredResumeDraft)
            .where(TailoredResumeDraft.user_id == user_id)
            .order_by(
                TailoredResumeDraft.created_at.desc(),
                TailoredResumeDraft.id.desc(),
            )
        )
        return list(result.scalars().all())

    async def get_tailored_draft(
        self, *, user_id: int, draft_id: int
    ) -> TailoredResumeDraft | None:
        result = await self._session.execute(
            select(TailoredResumeDraft).where(
                TailoredResumeDraft.id == draft_id,
                TailoredResumeDraft.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def delete_tailored_draft(
        self, *, user_id: int, draft_id: int
    ) -> bool:
        result = await self._session.execute(
            delete(TailoredResumeDraft).where(
                TailoredResumeDraft.id == draft_id,
                TailoredResumeDraft.user_id == user_id,
            )
        )
        return bool(cast(CursorResult[Any], result).rowcount)
