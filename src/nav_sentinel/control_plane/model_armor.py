"""Model Armor screening for untrusted external content.

The corporate-actions investigator reads issuer filings from the public internet. That
content is authored by someone other than us and lands directly in a model's context, which
makes it the fleet's primary prompt-injection and tool-poisoning surface.

Screening happens here, before the text reaches any model, and fails closed: if the service
cannot be reached, untrusted content is refused rather than admitted unscreened. A guardrail that
degrades to "allow" under load is not a guardrail.

It is not, however, the boundary. See below.

Three measured properties of the service shape this module, none of them documented.

**An undocumented size cliff.** Bisected to between 40,827 and 41,329 bytes, above which the
prompt-injection filter returns `execution_state: EXECUTION_SKIPPED` and `invocation_result:
PARTIAL` while `filter_match_state` still reads `NO_MATCH_FOUND`. Code reading only the match
state -- as this module originally did -- cannot tell a skipped scan from a clean one. Confirmed
end to end: 152,066 bytes admitted with the injection intact.

**Detection is content-sensitive, not size-sensitive.** The same 636-byte injection block is
matched 4/4 alone, and 2/2 with up to 400 bytes of benign filler appended (61% concentration).
But bundled with one particular 157-byte filing paragraph -- 792 bytes total, 80% concentration --
it is missed **0/8**. Deterministically, not flakily. Concentration and placement both looked
causal in earlier testing and neither is: what changes the verdict is *which* benign text shares
the payload.

**So there is no window size that makes screening reliable.** You cannot know an injection's
length in advance, and the same injection at the same concentration flips on its neighbour's
content. Windowing is still worth doing -- it catches a great deal that a whole-document screen
misses, at a bounded cost -- but it is defence in depth, not a boundary.

The boundary is structural, and it is the quarantined extractor in `extraction.py`: untrusted
prose never reaches a context that holds tools, identity or authority. This module's job is to
reduce what gets through, and to fail closed and audibly when it cannot tell.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from nav_sentinel.config import settings

logger = logging.getLogger(__name__)


#: Window size and overlap, in bytes. The window must approach the size of the injection for the
#: filter to see it; the overlap prevents one straddling a boundary and being halved into
#: invisibility. Measured, not guessed: 4KB windows miss what 1KB windows catch.
WINDOW_BYTES = 1024
OVERLAP_BYTES = 512

#: Anything larger than one window is windowed. There is no safe single-screen size above the
#: injection's own length: a 1,008-byte injection at the head of a 5,220-byte document was
#: admitted by a whole-document screen -- eight times *below* the size cliff. The cliff and the
#: placement failure are separate defects, and only the cliff has a threshold.
MAX_SINGLE_SCREEN_BYTES = WINDOW_BYTES

#: The filter this module exists for. Its execution state is checked explicitly, because a skipped
#: scan reports the same match state as a clean one.
PRIMARY_FILTER = "pi_and_jailbreak"

#: Refuse rather than screen beyond this. Screening is one API call per window, so a document
#: large enough to need hundreds is a cost and latency event, not a routine ingest -- and silently
#: spending them is worse than refusing. A caller with a genuinely large document must section it
#: before ingestion, which is what a corporate-actions notice or a single exhibit already is.
MAX_WINDOWS = 96


@dataclass(frozen=True)
class ArmorVerdict:
    blocked: bool
    verdict: str
    matched_filters: tuple[str, ...] = ()
    detail: str = ""
    #: How many windows were screened, and which one triggered. A verdict that cannot say where it
    #: looked is not auditable.
    windows_screened: int = 1
    offending_window: int | None = None

    @property
    def summary(self) -> str:
        where = "" if self.offending_window is None else f" in window {self.offending_window}"
        scope = "" if self.windows_screened == 1 else f" across {self.windows_screened} windows"
        if not self.blocked:
            return f"cleared ({self.verdict}){scope}"
        return (
            f"BLOCKED ({self.verdict}){where}: "
            f"{', '.join(self.matched_filters) or self.detail}"
        )


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


def windows(text: str, *, size: int = WINDOW_BYTES, overlap: int = OVERLAP_BYTES) -> list[str]:
    """Split text into screenable windows on structural boundaries.

    Paragraph-first, not a byte-wise slide. An injection has to read as prose to work on a model,
    so it occupies whole blocks -- the poisoned notice in the fixtures is a single 635-byte
    paragraph. Splitting on blocks keeps it intact in one window, and costs roughly one call per
    paragraph instead of one per `size - overlap` bytes.

    That difference is not cosmetic. A byte-wise slide over a 200KB filing is about 390 sanitize
    calls, which is neither affordable against the project's credit nor acceptable as request
    latency inside a NAV window. Paragraph blocks bring the same document to a few dozen.

    Blocks longer than one window are split with overlap, which is the only place a slide is
    needed and the only place a straddle is possible.
    """
    step = size - overlap
    if step <= 0:
        raise ValueError(f"overlap {overlap} must be smaller than window {size}")
    if len(text.encode()) <= size:
        return [text]

    out: list[str] = []
    for block in _blocks(text):
        if len(block.encode()) <= size:
            out.append(block)
            continue
        for start in range(0, len(block), step):
            chunk = block[start : start + size]
            if chunk.strip():
                out.append(chunk)
            if start + size >= len(block):
                break
    return out or [text]


def _blocks(text: str) -> list[str]:
    """Structural blocks, coarsest separator that actually divides the text.

    Filings are inconsistently formatted: some use blank lines, some are one long run of single
    newlines, some are a single line of stripped HTML. Falling straight through to a byte slide
    for the last case is correct, and is what the caller handles.
    """
    for separator in ("\n\n", "\n", ". "):
        parts = [p.strip() for p in text.split(separator)]
        parts = [p for p in parts if p]
        if len(parts) > 1:
            return parts
    return [text.strip()]


def _screen_once(text: str, ma, client) -> tuple[bool, str, tuple[str, ...], str]:
    """One call. Returns (clean, verdict, matched_filters, detail).

    Clean means affirmatively clean: the invocation succeeded, the prompt-injection filter
    actually ran, and nothing matched. `NO_MATCH_FOUND` on its own is not evidence -- it is what a
    skipped scan reports too.
    """
    response = client.sanitize_user_prompt(
        request=ma.SanitizeUserPromptRequest(
            name=template_path(), user_prompt_data=ma.DataItem(text=text)
        )
    )
    result = response.sanitization_result
    state = ma.FilterMatchState(result.filter_match_state).name
    matched = tuple(
        name for name, fr in (result.filter_results or {}).items() if _filter_matched(fr, ma)
    )
    if state == "MATCH_FOUND":
        return False, state, matched, ""

    invocation = getattr(result, "invocation_result", None)
    invocation_name = getattr(invocation, "name", str(invocation))
    if invocation_name != "SUCCESS":
        # PARTIAL means at least one configured filter did not run. Which one is not always
        # reported, so this is treated as unscreened rather than clean.
        return False, "invocation_incomplete", matched, f"invocation_result={invocation_name}"

    primary = (result.filter_results or {}).get(PRIMARY_FILTER)
    if primary is None:
        return False, "primary_filter_absent", matched, (
            f"{PRIMARY_FILTER} is not configured on template {settings().model_armor_template!r}"
        )
    which = primary._pb.WhichOneof("filter_result")
    inner = getattr(primary, which) if which else None
    execution = getattr(inner, "execution_state", None) if inner is not None else None
    execution_name = getattr(execution, "name", str(execution))
    if execution_name != "EXECUTION_SUCCESS":
        return False, "primary_filter_skipped", matched, (
            f"{PRIMARY_FILTER} execution_state={execution_name}"
        )

    return True, state, matched, ""


def screen(text: str, *, source_uri: str | None = None) -> ArmorVerdict:
    """Screen untrusted text in overlapping windows. Raises ContentBlocked on any failure.

    Fails closed four ways, and the distinction between them is recorded because an auditor asks
    which: a filter matched, the invocation was incomplete, the prompt-injection filter did not
    run, or the service could not be reached.
    """
    from google.cloud import modelarmor_v1 as ma

    s = settings()
    client = ma.ModelArmorClient(client_options={"api_endpoint": s.model_armor_endpoint})

    parts = windows(text)
    if len(parts) > MAX_WINDOWS:
        blocked = ArmorVerdict(
            blocked=True,
            verdict="too_large_to_screen",
            detail=(
                f"{len(text.encode()):,} bytes needs {len(parts)} windows, over the "
                f"{MAX_WINDOWS} limit. Section the document before ingestion; screening it whole "
                f"would cost one API call per window and admitting it unscreened is not an option."
            ),
            windows_screened=0,
        )
        logger.error("refusing to ingest %d bytes: %s", len(text.encode()), blocked.detail)
        raise ContentBlocked(blocked, source_uri)

    # Windows are independent, so they are screened concurrently. This is a latency fix, not a
    # cost one: the call count is set by the windowing, and the windowing is what keeps it bounded.
    def screen_window(item: tuple[int, str]):
        index, part = item
        return index, _screen_once(part, ma, client)

    if len(parts) > 1:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(screen_window, item) for item in enumerate(parts)]
            results = []
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    for f in futures:
                        f.cancel()
                    blocked = ArmorVerdict(
                        blocked=True,
                        verdict="screening_unavailable",
                        detail=f"{type(exc).__name__}: {exc}",
                        windows_screened=len(parts),
                    )
                    logger.error("Model Armor unavailable; refusing untrusted content: %s", exc)
                    raise ContentBlocked(blocked, source_uri) from exc
        for index, (clean, verdict, matched, detail) in sorted(results):
            if not clean:
                raise ContentBlocked(
                    ArmorVerdict(
                        blocked=True, verdict=verdict, matched_filters=matched, detail=detail,
                        windows_screened=len(parts), offending_window=index,
                    ),
                    source_uri,
                )
        return ArmorVerdict(
            blocked=False, verdict="NO_MATCH_FOUND", windows_screened=len(parts)
        )

    for index, part in enumerate(parts):
        try:
            clean, verdict, matched, detail = _screen_once(part, ma, client)
        except Exception as exc:
            # Fail closed. Untrusted content is not admitted because the screener is down.
            blocked = ArmorVerdict(
                blocked=True,
                verdict="screening_unavailable",
                detail=f"{type(exc).__name__}: {exc}",
                windows_screened=len(parts),
                offending_window=index,
            )
            logger.error("Model Armor unavailable; refusing untrusted content: %s", exc)
            raise ContentBlocked(blocked, source_uri) from exc

        if not clean:
            blocked = ArmorVerdict(
                blocked=True,
                verdict=verdict,
                matched_filters=matched,
                detail=detail,
                windows_screened=len(parts),
                offending_window=index,
            )
            raise ContentBlocked(blocked, source_uri)

    return ArmorVerdict(
        blocked=False, verdict="NO_MATCH_FOUND", windows_screened=len(parts)
    )


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
