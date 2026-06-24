"""FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import (
    applications,
    cover_letters,
    health,
    jobs,
    matches,
    resume,
)
from app.core.config import get_settings
from app.core.lifespan import lifespan, register_exception_handlers
from app.core.logging import configure_logging

configure_logging()
settings = get_settings()

app = FastAPI(
    title="Job Application API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

register_exception_handlers(app)

app.include_router(health.router, prefix="/api/v1")
app.include_router(resume.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(matches.router, prefix="/api/v1")
app.include_router(cover_letters.router, prefix="/api/v1")
app.include_router(applications.router, prefix="/api/v1")
