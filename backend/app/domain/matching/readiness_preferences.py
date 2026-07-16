"""Persistence and API contract for explicit readiness preferences."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_readiness_preference import UserReadinessPreference


class ReadinessPreferencePayload(BaseModel):
    minimum_annual_salary: float | None = Field(default=None, ge=0)
    salary_currency: str | None = Field(default=None, min_length=3, max_length=8)
    remote_only: bool | None = None
    accepted_job_types: list[str] = Field(default_factory=list, max_length=10)
    preferred_locations: list[str] = Field(default_factory=list, max_length=20)
    blocked_keywords: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _salary_requires_currency(self) -> ReadinessPreferencePayload:
        if self.minimum_annual_salary is not None and not self.salary_currency:
            raise ValueError("salary_currency is required with minimum_annual_salary")
        return self


class ReadinessPreferenceResponse(ReadinessPreferencePayload):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime


class ReadinessPreferenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_user(self, user_id: int) -> UserReadinessPreference | None:
        result = await self._session.execute(
            select(UserReadinessPreference).where(
                UserReadinessPreference.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self, user_id: int, values: dict[str, object]
    ) -> UserReadinessPreference:
        row = UserReadinessPreference(user_id=user_id, **values)
        self._session.add(row)
        await self._session.flush()
        return row

    async def update(
        self, row: UserReadinessPreference, values: dict[str, object]
    ) -> UserReadinessPreference:
        await self._session.execute(
            update(UserReadinessPreference)
            .where(UserReadinessPreference.id == row.id)
            .values(**values)
        )
        await self._session.refresh(row)
        return row


class ReadinessPreferenceService:
    def __init__(self, repository: ReadinessPreferenceRepository) -> None:
        self._repo = repository

    async def get(self, user_id: int) -> ReadinessPreferenceResponse:
        row = await self._repo.get_for_user(user_id)
        if row is None:
            row = await self._repo.create(user_id, ReadinessPreferencePayload().model_dump())
        return ReadinessPreferenceResponse.model_validate(row)

    async def upsert(
        self, user_id: int, payload: ReadinessPreferencePayload
    ) -> ReadinessPreferenceResponse:
        values = payload.model_dump()
        row = await self._repo.get_for_user(user_id)
        row = (
            await self._repo.create(user_id, values)
            if row is None
            else await self._repo.update(row, values)
        )
        return ReadinessPreferenceResponse.model_validate(row)
