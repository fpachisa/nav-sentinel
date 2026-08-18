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

#: The rules require Gemini 3.5 or newer. Held here rather than in a test, because the
#: probe is the gate and a gate that does not enforce its own threshold is decoration.
MINIMUM_MODEL_VERSION = (3, 5)


def parse_model_version(model: str) -> tuple[int, ...]:
    """Extract a comparable version from a Gemini model id.

    Parsed as a tuple of integers, not a float: float("3.10") is 3.1, so a future
    gemini-3.10-flash would compare below gemini-3.5-flash and be wrongly rejected.
    """
    parts = model.split("-")
    for part in parts[1:]:
        if part[0].isdigit():
            return tuple(int(x) for x in part.split(".") if x.isdigit())
    raise ComplianceFailure(f"cannot determine a version from model id {model!r}")


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


def check_gcloud_project() -> str:
    """Confirm the gcloud CLI targets the same project as this process.

    Separate from the rest of preflight because it shells out, and therefore cannot run in an
    offline or container environment. `infra/bootstrap.sh` and the deploy scripts carry
    gcloud's own active configuration, entirely independent of these settings: a mismatch
    provisions the wrong project and surfaces much later as a permissions error against
    resources that were never created.
    """
    s = settings()
    try:
        cli = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True, text=True, timeout=20, check=False,
        ).stdout.strip()
    except Exception as exc:  # pragma: no cover - gcloud absent
        raise ComplianceFailure(f"gcloud is required for deployment but is unusable: {exc}") from exc

    if cli != s.project:
        raise ComplianceFailure(
            f"gcloud active project is {cli!r} but GOOGLE_CLOUD_PROJECT is {s.project!r}. "
            f"Deploy scripts shell out to gcloud and would target the wrong project. "
            f"Fix with: gcloud config set project {s.project}"
        )
    return f"gcloud project matches settings ({cli})"


def preflight(*, check_cli: bool = True) -> list[str]:
    """Check the environment agrees with itself before anything is deployed or invoked.

    Every condition here is fatal. Nothing warns and proceeds: each one is either a
    qualifying requirement for the entry or a misconfiguration that produces a misleading
    error much further downstream.
    """
    s = settings()
    notes: list[str] = []

    if not s.use_vertexai:
        raise ComplianceFailure(
            "GOOGLE_GENAI_USE_VERTEXAI is false. The rules require the model to be reached "
            "through Vertex AI or the Gemini API; this project commits to Vertex."
        )
    notes.append("Vertex AI transport enabled")

    if s.model_location != "global":
        # Fatal, not advisory. The single most expensive discovery in this project: every
        # Gemini 3.x id returns 404 NOT_FOUND in a regional location, and the error reads
        # like a permissions problem.
        raise ComplianceFailure(
            f"model_location is {s.model_location!r}. Gemini 3.x is served only from 'global'; "
            f"3.x ids return 404 NOT_FOUND regionally. Set GOOGLE_CLOUD_LOCATION=global and "
            f"keep NAV_REGION for regional services."
        )
    notes.append("model_location is 'global' (required for the Gemini 3.x family)")

    if s.region == "global":
        raise ComplianceFailure(
            "NAV_REGION is 'global', which is not a valid location for Model Armor, Cloud Run, "
            "Firestore or Pub/Sub."
        )
    notes.append(f"regional services target {s.region}")

    for tier, model in (("reasoning", s.model_reasoning), ("classify", s.model_classify)):
        version = parse_model_version(model)
        if version < MINIMUM_MODEL_VERSION:
            raise ComplianceFailure(
                f"configured {tier} model {model!r} is below the Gemini "
                f"{'.'.join(map(str, MINIMUM_MODEL_VERSION))} floor the rules require."
            )
    notes.append(
        f"configured models meet the Gemini "
        f"{'.'.join(map(str, MINIMUM_MODEL_VERSION))} floor"
    )

    if check_cli:
        notes.append(check_gcloud_project())
    return notes


async def probe_async(model_id: str) -> ModelProbe:
    """Drive one ADK agent against one model and record the evidence on a span."""
    from google import adk
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

        # Check the RETURNED version, not the configured id. Vertex may serve a different
        # build than the alias requested, and the requirement is about what actually ran.
        version = parse_model_version(returned)
        if version < MINIMUM_MODEL_VERSION:
            raise ComplianceFailure(
                f"{returned!r} is version {'.'.join(map(str, version))}, below the required "
                f"Gemini {'.'.join(map(str, MINIMUM_MODEL_VERSION))} floor. The entry would not "
                f"qualify. Configured id was {model_id!r}."
            )

        if not s.use_vertexai:
            raise ComplianceFailure(
                f"{returned!r} was reached without the Vertex AI transport. The probe must "
                f"demonstrate the transport the entry commits to, not merely that a model "
                f"replied."
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


def run(*, strict: bool = True) -> tuple[list[str], list[ModelProbe]]:
    """Preflight, then prove both model tiers.

    Raises on the first failure: a partial pass is a fail, because every one of these is a
    qualifying condition for the whole entry. `strict` additionally treats a preflight
    WARNING as fatal -- the wrong model_location warns rather than errors in isolation, but
    a deploy must not proceed on it.
    """
    notes = preflight(check_cli=strict)
    s = settings()
    return notes, [probe(m) for m in (s.model_reasoning, s.model_classify)]


def main() -> int:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    try:
        notes, probes = run()
    except ComplianceFailure as exc:
        console.print(f"[red]COMPLIANCE FAILURE:[/red] {exc}")
        return 1

    console.print("[bold]Preflight[/bold]")
    for note in notes:
        console.print(f"  [green]•[/green] {note}")

    console.print("\n[bold]Model probes (via ADK on Vertex AI)[/bold]")
    table = Table(header_style="bold")
    for col in ("Requested", "Returned version", "Floor", "Reply", "Framework", "Trace"):
        table.add_column(col)
    for p in probes:
        version = ".".join(map(str, parse_model_version(p.returned_version or "0")))
        table.add_row(
            p.requested, p.returned_version or "-", f"{version} >= 3.5",
            p.reply or "-", f"{p.framework} {p.framework_version}", (p.trace_id or "-")[:16],
        )
    console.print(table)
    console.print("\n[green]All qualifying requirements demonstrated.[/green]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
