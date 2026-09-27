"""Seed one synthetic evaluator in an independently verified disposable database."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.verify_unit_1c_validation_env import main as verify_database  # noqa: E402

if TYPE_CHECKING:
    from app.domain.resume.schemas import SourceResumeDocument

EMAIL = "evaluator@example.com"
PASSWORD = "PortfolioEval2026!"  # noqa: S105 - published disposable evaluator credential
JOB_URL = "http://127.0.0.1:18201/portfolio-evaluation-job"


def deterministic_source() -> tuple[dict[str, object], SourceResumeDocument]:
    """Give the existing verified fixture stable IDs for reproducible packages."""
    from app.domain.resume.schemas import SourceResumeDocument
    from tests.unit.resume_qa_support import source_backed_wrap_source_payload

    payload, document = source_backed_wrap_source_payload()
    encoded = json.dumps(
        {"payload": payload, "document": document.model_dump(mode="json")},
        sort_keys=True,
    )
    identifiers: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        found = match.group()
        if found not in identifiers:
            identifiers[found] = str(uuid5(NAMESPACE_URL, f"portfolio-source-{len(identifiers)}"))
        return identifiers[found]

    normalized = json.loads(
        re.sub(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", replace, encoded)
    )
    return normalized["payload"], SourceResumeDocument.model_validate(normalized["document"])


async def seed() -> dict[str, object]:
    # This must run before importing app modules, which initialize the DB engine.
    if (
        os.environ.get("ENVIRONMENT") != "test"
        or os.environ.get("PORTFOLIO_SYNTHETIC_PROVIDERS") != "true"
    ):
        raise RuntimeError("portfolio seed requires the explicit isolated test configuration")
    verify_database()

    from sqlalchemy import func, select

    from app.core.config import get_settings
    from app.core.security import hash_password
    from app.domain.auth.repository import UserRepository
    from app.domain.jobs.repository import JobRepository
    from app.domain.jobs.schemas import JobImportRequest
    from app.domain.jobs.service import JobService
    from app.domain.resume.provenance import build_resume_import_provenance
    from app.domain.resume.schemas import ParsedProfile
    from app.infrastructure.database import async_session_factory
    from app.models.job import Job
    from app.models.resume import CandidateProfile, Resume, SourceResumeContent, TailoredResumeDraft
    from app.models.user import User
    from tests.unit.resume_qa_support import load_wrap_resume_text, source_backed_wrap_content

    if not get_settings().portfolio_synthetic_providers_enabled:
        raise RuntimeError("synthetic provider is not enabled")
    async with async_session_factory() as db:
        if await db.scalar(select(func.count()).select_from(User)) or await db.scalar(
            select(func.count()).select_from(Job)
        ):
            raise RuntimeError("portfolio seed requires an empty freshly migrated database")
        user = await UserRepository(db).create(
            email=EMAIL,
            name="Jordan Example",
            hashed_password=hash_password(PASSWORD),
        )
        job = await JobService(JobRepository(db), external_enrichment_enabled=False).import_job(
            JobImportRequest(
                url=JOB_URL,
                title="Synthetic Platform Engineer",
                company="Orbit Workshop (fictional)",
                description=(
                    "Fictional local evaluation role. Build Python and FastAPI services, "
                    "review operational dashboards, and discuss reliable release workflows."
                ),
            ),
            user_id=user.id,
        )
        resume = Resume(
            user_id=user.id,
            filename="jordan-example-synthetic.pdf",
            raw_text=load_wrap_resume_text(),
            is_active=True,
            import_provenance=build_resume_import_provenance(
                "jordan-example-synthetic.pdf", []
            ).model_dump(mode="json"),
        )
        db.add(resume)
        await db.flush()
        source_payload, source_document = deterministic_source()
        source = SourceResumeContent(
            resume_id=resume.id,
            schema_version="source-resume-v5",
            content=source_payload,
            validation_status="verified",
            validation_errors=[],
        )
        profile = CandidateProfile(
            user_id=user.id,
            resume_id=resume.id,
            parsed=ParsedProfile.model_validate(
                {
                    "full_name": "Jordan Example",
                    "email": EMAIL,
                    "headline": "Synthetic platform engineer",
                    "skills": ["Python", "FastAPI", "Release operations"],
                }
            ).model_dump(mode="json"),
            summary_text="Synthetic platform engineer with Python and FastAPI experience.",
            embedding=[0.0] * get_settings().embedding_dim,
        )
        db.add_all([source, profile])
        await db.flush()
        content = source_backed_wrap_content(
            source_document,
            source,
            job_description=job.description or "Synthetic platform role",
        ).model_copy(update={"job_id": job.id})
        db.add(
            TailoredResumeDraft(
                user_id=user.id,
                job_id=job.id,
                source_profile_id=profile.id,
                source_resume_content_id=source.id,
                job_title=job.title,
                job_company=job.company,
                content=content.model_dump(mode="json"),
            )
        )
        await db.commit()
        return {
            "email": EMAIL,
            "job_id": job.id,
            "job_title": job.title,
            "user_id": user.id,
            "resume": resume.filename,
            "profile_skills": ["Python", "FastAPI", "Release operations"],
            "application_seeded": False,
            "provider": "portfolio-synthetic-v1",
        }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(seed()), sort_keys=True))
