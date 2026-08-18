"""Model Armor screening for untrusted external content.

The corporate-actions investigator reads issuer filings from the public internet. That
content is authored by someone other than us and lands directly in a model's context, which
makes it the fleet's primary prompt-injection and tool-poisoning surface.

Screening happens here, at the boundary, before the text reaches any model. The design is
fail-closed: if the screening service cannot be reached, untrusted content is refused rather
than admitted unscreened. A guardrail that degrades to "allow" under load is not a guardrail.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from nav_sentinel.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArmorVerdict:
    blocked: bool
    verdict: str
    matched_filters: tuple[str, ...] = ()
    detail: str = ""

    @property
    def summary(self) -> str:
        if not self.blocked:
            return f"cleared ({self.verdict})"
        return f"BLOCKED ({self.verdict}): {', '.join(self.matched_filters) or self.detail}"


class ContentBlocked(RuntimeError):
    """Raised when untrusted content fails screening. Never caught to allow the content."""

    def __init__(self, verdict: ArmorVerdict, source_uri: str | None = None) -> None:
        super().__init__(f"Model Armor blocked content from {source_uri or 'external source'}: "
                         f"{verdict.summary}")
        self.verdict = verdict
        self.source_uri = source_uri


def template_path() -> str:
    s = settings()
    return f"projects/{s.project}/locations/{s.region}/templates/{s.model_armor_template}"


def screen(text: str, *, source_uri: str | None = None) -> ArmorVerdict:
    """Screen untrusted text. Raises ContentBlocked on a match or on screening failure."""
    from google.cloud import modelarmor_v1 as ma

    s = settings()
    client = ma.ModelArmorClient(client_options={"api_endpoint": s.model_armor_endpoint})

    try:
        response = client.sanitize_user_prompt(
            request=ma.SanitizeUserPromptRequest(
                name=template_path(),
                user_prompt_data=ma.DataItem(text=text),
            )
        )
    except Exception as exc:
        # Fail closed. Untrusted content is not admitted because the screener is down.
        verdict = ArmorVerdict(
            blocked=True,
            verdict="screening_unavailable",
            detail=f"{type(exc).__name__}: {exc}",
        )
        logger.error("Model Armor unavailable; refusing untrusted content: %s", exc)
        raise ContentBlocked(verdict, source_uri) from exc

    result = response.sanitization_result
    state = ma.FilterMatchState(result.filter_match_state).name
    matched = tuple(
        name
        for name, fr in (result.filter_results or {}).items()
        if _filter_matched(fr, ma)
    )

    if state == "MATCH_FOUND":
        verdict = ArmorVerdict(blocked=True, verdict=state, matched_filters=matched)
        raise ContentBlocked(verdict, source_uri)

    return ArmorVerdict(blocked=False, verdict=state, matched_filters=matched)


def _filter_matched(filter_result, ma) -> bool:
    """Filter results are a oneof across detector types; check whichever is populated."""
    for attr in (
        "pi_and_jailbreak_filter_result",
        "sdp_filter_result",
        "rai_filter_result",
        "malicious_uri_filter_result",
        "csam_filter_result",
        "virus_scan_filter_result",
    ):
        inner = getattr(filter_result, attr, None)
        if inner is None:
            continue
        state = getattr(inner, "match_state", None)
        if state is not None and ma.FilterMatchState(state).name == "MATCH_FOUND":
            return True
        # SDP returns nested inspect/deidentify results
        for nested_attr in ("inspect_result", "deidentify_result"):
            nested = getattr(inner, nested_attr, None)
            if nested is not None:
                nstate = getattr(nested, "match_state", None)
                if nstate is not None and ma.FilterMatchState(nstate).name == "MATCH_FOUND":
                    return True
    return False
