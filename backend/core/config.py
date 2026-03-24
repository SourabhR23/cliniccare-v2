"""
backend/core/config.py — Phase 3

CHANGE: Added openai_base_url field.
  Set OPENAI_BASE_URL=https://api.euron.one/api/v1/euri in .env to use EURI.
  Leave blank to use real OpenAI (sk-... key).
"""

from functools import lru_cache
from typing import Optional
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── APP ──────────────────────────────────────────────────
    app_name: str = "ClinicCare"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = True

    # ── SECURITY ─────────────────────────────────────────────
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 480
    webhook_secret: str = "change-me-in-production"

    # ── MONGODB ──────────────────────────────────────────────
    mongodb_url: str
    mongodb_db_name: str = "cliniccare"
    mongodb_max_pool_size: int = 10
    mongodb_min_pool_size: int = 2
    mongodb_connect_timeout_ms: int = 5000

    # ── CHROMADB ─────────────────────────────────────────────────
    chroma_collection_name: str = "clinic_visits"
    chroma_host: str                        # e.g. "api.trychroma.com"
    chroma_port: int = 443
    chroma_api_key: str                     # from Chroma Cloud dashboard
    chroma_tenant: str                      # your tenant name
    chroma_database: str = "cliniccare"  

    # ── OPENAI ───────────────────────────────────────────────
    openai_api_key: str
    openai_chat_model: str = "gpt-4o-mini"
    openai_fast_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_max_tokens: int = 1000
    openai_embedding_dimensions: int = 1536

    # EURI / custom base URL
    # Set to https://api.euron.one/api/v1/euri to use EURI API keys.
    # Leave blank (or omit from .env) to use real OpenAI.
    openai_base_url: Optional[str] = None

    # ── SUPABASE ─────────────────────────────────────────────
    supabase_db_url: str

    # ── REDIS ────────────────────────────────────────────────
    redis_url: str
    redis_ttl_session: int = 86400
    redis_ttl_drug_interaction: int = 604800
    redis_ttl_rag_query: int = 3600

    # ── EMAIL ────────────────────────────────────────────────
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str
    smtp_password: str
    smtp_from_name: str = "ClinicCare"
    smtp_from_email: str

    # ── COHERE ───────────────────────────────────────────────
    cohere_api_key: Optional[str] = None          # for Cohere Rerank API
    cohere_rerank_model: str = "rerank-english-v3.0"

    # ── OPENFDA ──────────────────────────────────────────────
    openfda_api_key: Optional[str] = None
    openfda_base_url: str = "https://api.fda.gov/drug"

    # ── CELERY ───────────────────────────────────────────────
    celery_broker_url: Optional[str] = None
    celery_result_backend: Optional[str] = None
    embedding_digest_hour: int = 8
    embedding_digest_minute: int = 0
    scheduling_reminder_check_interval_minutes: int = 60

    # ── AGENT SETTINGS ───────────────────────────────────────
    agent_max_tool_calls: int = 5
    agent_confidence_threshold: float = 0.70

    # ── CORS ─────────────────────────────────────────────────
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    @field_validator("app_env")
    @classmethod
    def validate_env(cls, v: str) -> str:
        allowed = {"development", "staging", "production"}
        if v not in allowed:
            raise ValueError(f"app_env must be one of {allowed}, got '{v}'")
        return v

    @field_validator("secret_key")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters.")
        return v

    @model_validator(mode="after")
    def set_celery_urls(self) -> "Settings":
        if not self.celery_broker_url:
            self.celery_broker_url = self.redis_url
        if not self.celery_result_backend:
            self.celery_result_backend = self.redis_url
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()