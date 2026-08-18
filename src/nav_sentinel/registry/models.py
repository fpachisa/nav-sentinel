"""Agent Registry schema.

A manifest is the single authoritative declaration of what an agent is and what it may do.
Three things read it, which is what keeps it honest rather than decorative:

  * Triage, at runtime, to discover which specialist handles a given break category.
  * The Agent Gateway, on every tool call, to deny anything outside `allowed_tools`.
  * Deployment, to mint one service account per agent with exactly these data scopes.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from nav_sentinel.domain.models import BreakCategory

MANIFEST_DIR = Path(__file__).parent / "manifests"


class Authority(BaseModel):
    may_propose_remediation: bool = False
    may_post_entries: bool = False
    max_autonomous_bps: float = 0.0


class DataScopes(BaseModel):
    read: list[str] = Field(default_factory=list)
    write: list[str] = Field(default_factory=list)


class Sla(BaseModel):
    target_latency_seconds: int = 60


class AgentManifest(BaseModel):
    agent_id: str
    version: str
    display_name: str
    owner: str
    description: str
    handles_categories: list[BreakCategory] = Field(default_factory=list)
    model: str
    allowed_tools: list[str] = Field(default_factory=list)
    data_scopes: DataScopes = Field(default_factory=DataScopes)
    authority: Authority = Field(default_factory=Authority)
    untrusted_inputs: bool = False
    requires_model_armor: bool = False
    sla: Sla = Field(default_factory=Sla)

    @property
    def ref(self) -> str:
        """Stable, version-pinned reference recorded in every audit trail."""
        return f"{self.agent_id}@{self.version}"

    @property
    def service_account_id(self) -> str:
        """Per-agent identity. Truncated to Google's 30-character limit for account ids."""
        return f"nav-{self.agent_id}"[:30].rstrip("-")


def load_manifests(directory: Path | None = None) -> list[AgentManifest]:
    directory = directory or MANIFEST_DIR
    out: list[AgentManifest] = []
    for path in sorted(directory.glob("*.yaml")):
        out.append(AgentManifest(**yaml.safe_load(path.read_text())))
    return out


def load_manifest(agent_id: str, directory: Path | None = None) -> AgentManifest:
    for m in load_manifests(directory):
        if m.agent_id == agent_id:
            return m
    raise KeyError(f"no manifest for agent_id={agent_id!r}")
