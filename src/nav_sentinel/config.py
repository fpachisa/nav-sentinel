"""Central configuration. Every module reads settings from here, never from os.environ."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    project: str = Field("all-things-agentic-hack-fp", alias="GOOGLE_CLOUD_PROJECT")
    location: str = Field("us-central1", alias="GOOGLE_CLOUD_LOCATION")
    use_vertexai: bool = Field(True, alias="GOOGLE_GENAI_USE_VERTEXAI")

    # Two model tiers. Reasoning work goes to Flash; high-volume classification of every
    # single break goes to Flash-Lite, which is where the token spend would otherwise run away.
    model_reasoning: str = Field("gemini-3.7-flash", alias="NAV_MODEL_REASONING")
    model_classify: str = Field("gemini-3.5-flash-lite", alias="NAV_MODEL_CLASSIFY")

    model_armor_template: str = Field(
        "nav-sentinel-untrusted-ingest", alias="NAV_MODEL_ARMOR_TEMPLATE"
    )
    firestore_database: str = Field("(default)", alias="NAV_FIRESTORE_DATABASE")
    pubsub_topic_exceptions: str = Field("nav-exceptions", alias="NAV_PUBSUB_TOPIC_EXCEPTIONS")
    enable_tracing: bool = Field(True, alias="NAV_ENABLE_TRACING")

    # SEC requires a contact address in the User-Agent of automated requests.
    # Left blank by default so no address is ever transmitted implicitly.
    sec_contact: str = Field("", alias="NAV_SEC_CONTACT")

    # Governance thresholds, in basis points of fund NAV. These are the numbers the
    # Agent Gateway enforces; they are deliberately configuration, not code.
    auto_clear_max_bps: float = 0.25
    four_eyes_min_bps: float = 1.0
    escalate_cio_min_bps: float = 5.0

    @property
    def firestore_collection_prefix(self) -> str:
        return "nav_sentinel"


@lru_cache
def settings() -> Settings:
    return Settings()
