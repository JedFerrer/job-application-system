"""Application configuration loaded from environment variables.

All settings are validated on startup via pydantic-settings. Secrets use
``SecretStr`` so they are never accidentally logged.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["openai", "deepseek", "anthropic"]

# Base URLs for OpenAI-compatible providers used by the fallback chain.
_PROVIDER_BASE_URLS: dict[str, str | None] = {
    "openai": None,  # SDK default
    "deepseek": "https://api.deepseek.com",
    "anthropic": "https://api.anthropic.com/v1/",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application ---
    environment: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"

    # --- Database ---
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/job_app"
    )

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- CORS ---
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- Single seeded user (no auth in MVP) ---
    seed_user_email: str = "me@example.com"
    seed_user_name: str = "Me"

    # --- LLM providers ---
    llm_provider: LLMProvider = "openai"
    llm_model: str = "gpt-4o-mini"
    openai_api_key: SecretStr = SecretStr("")
    deepseek_api_key: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")

    # --- Embeddings ---
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # --- Optional job source fallback ---
    jsearch_api_key: SecretStr = SecretStr("")
    enable_jsearch: bool = False

    # --- Job ingestion (Phase 2) ---
    enabled_job_sources: list[str] = Field(default_factory=lambda: ["indeed"])
    job_search_country: str = "USA"
    job_results_wanted: int = 50
    proxy_list: list[str] = Field(default_factory=list)

    # --- Shift detection ---
    # IANA timezone name for the job seeker.  Used to convert employer-side
    # clock times ("8 AM CST") to the worker's local time before classifying
    # day / mid / night.  Configurable so the platform can support users in
    # any region in the future.
    user_timezone: str = "Asia/Manila"

    @field_validator("allowed_origins", "enabled_job_sources", "proxy_list", mode="before")
    @classmethod
    def _parse_list(cls, value: object) -> object:
        """Accept a JSON array string or a comma-separated string from env."""
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("["):
                return json.loads(text)
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    @property
    def sync_database_url(self) -> str:
        """Synchronous DSN for Alembic (psycopg)."""
        return self.database_url.replace("+asyncpg", "+psycopg")

    def _api_key_for(self, provider: str) -> str:
        keys = {
            "openai": self.openai_api_key,
            "deepseek": self.deepseek_api_key,
            "anthropic": self.anthropic_api_key,
        }
        return keys[provider].get_secret_value()

    def get_fallback_chain(self) -> list[dict[str, str | None]]:
        """Ordered provider configs: primary first, then any other configured.

        Each entry: ``{"name", "api_key", "base_url", "model"}``. Providers
        without a configured key are skipped.
        """
        ordered = [self.llm_provider] + [
            p for p in ("openai", "deepseek", "anthropic") if p != self.llm_provider
        ]
        chain: list[dict[str, str | None]] = []
        for provider in ordered:
            api_key = self._api_key_for(provider)
            if not api_key:
                continue
            chain.append(
                {
                    "name": provider,
                    "api_key": api_key,
                    "base_url": _PROVIDER_BASE_URLS[provider],
                    "model": self.llm_model if provider == self.llm_provider else None,
                }
            )
        return chain


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
