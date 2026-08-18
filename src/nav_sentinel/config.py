"""Central configuration. Every module reads settings from here, never from os.environ."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    project: str = Field("all-things-agentic-hack-fp", alias="GOOGLE_CLOUD_PROJECT")

    # Two locations, because Google serves these families from different places and
    # conflating them produces a 404 that reads like a permissions problem.
    #
    #   model_location -- Gemini 3.x is served ONLY from "global" on Vertex. Every 3.x id
    #     returns 404 NOT_FOUND in us-central1, where only the 2.5 family resolves.
    #     The google-genai SDK and ADK both read this from GOOGLE_CLOUD_LOCATION.
    #   region -- regional services: Model Armor (regional endpoint only), Cloud Run,
    #     Firestore, Pub/Sub. "global" is not valid for any of them.
    model_location: str = Field("global", alias="GOOGLE_CLOUD_LOCATION")
    region: str = Field("us-central1", alias="NAV_REGION")

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

    @property
    def model_armor_endpoint(self) -> str:
        """Model Armor is reachable only on its regional endpoint. Calls to the global
        endpoint return PERMISSION_DENIED even for a project owner, which is a
        misleading error for what is really a wrong-host problem."""
        return f"modelarmor.{self.region}.rep.googleapis.com"


@lru_cache
def settings() -> Settings:
    return Settings()


def configure_sdk_environment() -> dict[str, str]:
    """Publish settings into os.environ for the Google SDKs.

    pydantic-settings reads `.env` into this object; google-genai and ADK read os.environ
    directly and never see it. Without this bridge the SDK client is constructed with
    location=None, which surfaces as an opaque agent failure rather than a config error.

    Called at the entry point of anything that invokes a model.
    """
    import os

    s = settings()
    env = {
        "GOOGLE_CLOUD_PROJECT": s.project,
        "GOOGLE_CLOUD_LOCATION": s.model_location,
        "GOOGLE_GENAI_USE_VERTEXAI": "true" if s.use_vertexai else "false",
    }
    os.environ.update(env)
    return env
