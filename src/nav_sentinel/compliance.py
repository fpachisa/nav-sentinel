"""Stack compliance proof (S0a).

The hackathon's qualifying requirements are a Gemini 3.5-or-newer model, reached through
Vertex AI, driven by a Google agent framework. Those are pass/fail conditions for the whole
entry, so they are proven by a running probe that records its evidence on a trace rather
than by assertion in a README.

The probe deliberately goes through ADK's Runner rather than calling the GenAI SDK directly.
Invoking the model without the framework would satisfy neither the letter nor the point of
the requirement.
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass, field

from nav_sentinel.config import configure_sdk_environment, settings
from nav_sentinel.control_plane import telemetry

APP_NAME = "nav-sentinel-compliance"


class ComplianceFailure(RuntimeError):
    """A qualifying requirement could not be demonstrated."""


@dataclass(frozen=True)
class ModelProbe:
    requested: str
    returned_version: str | None
    reply: str
    framework: str
    framework_version: str
    location: str
    vertexai: bool
    trace_id: str | None = None
    attributes: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.returned_version)


def preflight() -> list[str]:
    """Check the environment agrees with itself before anything is deployed.

    The gcloud check exists because `infra/bootstrap.sh` and the deploy scripts shell out to
    gcloud, which carries its own active configuration entirely independent of this process's
    settings. A mismatch silently provisions the wrong project, and the failure surfaces much
    later as a permissions error against resources that were never created.
    """
    s = settings()
    notes: list[str] = []

    try:
        cli = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()
    except Exception as exc:  # pragma: no cover - gcloud absent
        raise ComplianceFailure(f"gcloud is required for deployment but is unusable: {exc}") from exc

    if cli != s.project:
        raise ComplianceFailure(
            f"gcloud active project is {cli!r} but GOOGLE_CLOUD_PROJECT is {s.project!r}. "
            f"Deploy scripts shell out to gcloud and would target the wrong project. "
            f"Fix with: gcloud config set project {s.project}"
        )
    notes.append(f"gcloud project matches settings ({cli})")

    if not s.use_vertexai:
        raise ComplianceFailure(
            "GOOGLE_GENAI_USE_VERTEXAI is false. The rules require the model to be reached "
            "through Vertex AI or the Gemini API; this project commits to Vertex."
        )
    notes.append("Vertex AI transport enabled")

    if s.model_location != "global":
        notes.append(
            f"WARNING: model_location is {s.model_location!r}. Gemini 3.x is served only from "
            f"'global'; 3.x model ids return 404 NOT_FOUND in regional locations."
        )
    else:
        notes.append("model_location is 'global' (required for the Gemini 3.x family)")

    notes.append(f"regional services target {s.region}")
    return notes


async def probe_async(model_id: str) -> ModelProbe:
    """Drive one ADK agent against one model and record the evidence on a span."""
    import google.adk as adk
    from google.adk.agents import Agent
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    configure_sdk_environment()
    s = settings()
    agent = Agent(
        name="compliance_probe",
        model=model_id,
        instruction="Reply with exactly one word: COMPLIANT",
    )
    runner = InMemoryRunner(agent, app_name=APP_NAME)

    attributes = {
        "nav.compliance.framework": "google-adk",
        "nav.compliance.framework_version": adk.__version__,
        "nav.compliance.model_requested": model_id,
        "nav.compliance.transport": "vertex-ai" if s.use_vertexai else "gemini-api",
        "nav.compliance.model_location": s.model_location,
        "nav.compliance.project": s.project,
    }

    with telemetry.span("nav_sentinel.compliance_probe", **attributes) as sp:
        session = await runner.session_service.create_session(app_name=APP_NAME, user_id="probe")
        returned: str | None = None
        reply = ""
        try:
            async for event in runner.run_async(
                user_id="probe",
                session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text="ping")]),
            ):
                version = getattr(event, "model_version", None)
                if version:
                    returned = version
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                            reply += part.text
        except Exception as exc:
            raise ComplianceFailure(
                f"ADK could not reach {model_id!r} on Vertex at location "
                f"{s.model_location!r}: {exc}"
            ) from exc
        finally:
            await runner.close()

        if not returned:
            raise ComplianceFailure(
                f"{model_id!r} produced no model_version. The requirement is evidenced by the "
                f"version the service returns, not by the id we asked for."
            )

        # This is the attribute the acceptance criterion is asserted against.
        sp.set_attribute("nav.compliance.model_version", returned)
        attributes["nav.compliance.model_version"] = returned
        trace_id = telemetry.current_trace_id()

    return ModelProbe(
        requested=model_id,
        returned_version=returned,
        reply=reply.strip(),
        framework="google-adk",
        framework_version=adk.__version__,
        location=s.model_location,
        vertexai=s.use_vertexai,
        trace_id=trace_id,
        attributes=attributes,
    )


def probe(model_id: str) -> ModelProbe:
    return asyncio.run(probe_async(model_id))


def run() -> list[ModelProbe]:
    """Prove both model tiers. Raises on the first failure -- a partial pass is a fail."""
    s = settings()
    return [probe(m) for m in (s.model_reasoning, s.model_classify)]


def main() -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print("[bold]Preflight[/bold]")
    for note in preflight():
        style = "yellow" if note.startswith("WARNING") else "green"
        console.print(f"  [{style}]•[/{style}] {note}")

    console.print("\n[bold]Model probes (via ADK on Vertex AI)[/bold]")
    table = Table(header_style="bold")
    for col in ("Requested", "Returned version", "Reply", "Framework", "Location", "Trace"):
        table.add_column(col)
    for p in run():
        table.add_row(
            p.requested, p.returned_version or "-", p.reply or "-",
            f"{p.framework} {p.framework_version}", p.location, (p.trace_id or "-")[:16],
        )
    console.print(table)
    console.print("\n[green]All qualifying requirements demonstrated.[/green]")


if __name__ == "__main__":
    main()
