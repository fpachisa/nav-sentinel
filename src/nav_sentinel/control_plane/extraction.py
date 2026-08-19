"""The quarantine: untrusted prose in, typed values out.

This is the boundary the architecture actually rests on, and the reason is empirical.
`model_armor.screen` reduces what gets through but cannot be relied on -- the same 636-byte
injection is matched alone and missed 0/8 when it shares 792 bytes with one particular filing
paragraph. There is no window size that fixes that, because the verdict turns on which benign
text happens to be adjacent.

So the design assumes screening fails, and makes that survivable.

An extractor runs in a context that holds **no tools, no bound identity and no memory**. Its only
output is a validated record of typed fields. An instruction that survives screening arrives
somewhere with nothing to instruct: there is no tool to call, no authority to escalate, and
nothing to remember it into the next cycle.

That bounds *instruction* injection. It does nothing about **data** poisoning -- an attacker who
cannot make the extractor act can still make it report a false withholding rate, and the poisoned
fixture does exactly that (`Withholding Tax: 0.00%`). Values are therefore cross-checked against
plausibility bounds and against the fund's own books before anything is drafted, and a human
approval sits behind that. Four layers, each doing one thing, and the module claims no more than
it does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nav_sentinel.control_plane import identity


class QuarantineViolation(RuntimeError):
    """The extractor was invoked from a context that holds privilege.

    Its whole value is the absence of capability. Running it while an identity is bound would let
    a downstream tool call inherit that identity, so the check is here rather than in a comment.
    """


class ExtractionFailed(RuntimeError):
    """The document did not yield a valid record.

    Always a refusal, never a partial result. A half-extracted corporate action is more dangerous
    than none: it looks like evidence.
    """


class ExtractionRejected(RuntimeError):
    """A value was extracted but failed a plausibility or cross-check.

    Distinct from `ExtractionFailed` because the two mean different things to an auditor: one is
    "the document was unreadable", the other "the document said something we do not believe".
    """


@dataclass(frozen=True)
class Bounds:
    """What a field is allowed to say. Attacker-controlled input is checked, not trusted."""

    minimum: Decimal | None = None
    maximum: Decimal | None = None
    note: str = ""

    def check(self, name: str, value: Decimal) -> None:
        if self.minimum is not None and value < self.minimum:
            raise ExtractionRejected(
                f"{name}={value} is below the plausible minimum {self.minimum}. {self.note}"
            )
        if self.maximum is not None and value > self.maximum:
            raise ExtractionRejected(
                f"{name}={value} is above the plausible maximum {self.maximum}. {self.note}"
            )


class CorporateActionRecord(BaseModel):
    """The only thing a filing is allowed to become.

    Frozen, `extra="forbid"`, every field typed. A free-text field would reopen the hole: prose
    that crosses the boundary is prose in a privileged context, whatever it is called.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Every string field is constrained by pattern or by enumeration. `split_ratio` was
    #: `str | None` with no pattern, and `_LABEL` captures the rest of the line -- so 170 bytes of
    #: attacker instruction prose crossed the boundary this class exists to hold, in the one field
    #: nobody had constrained. The docstring above already forbade it; the constraint was missing.
    action_type: Literal["cash_dividend", "stock_split", "merger", "unknown"]
    isin: str = Field(pattern=r"^[A-Z]{2}[A-Z0-9]{9}\d$")
    ex_date: date
    gross_rate: Decimal | None = None
    withholding_pct: Decimal | None = None
    split_ratio: str | None = Field(default=None, pattern=r"^\d{1,6}\s*:\s*\d{1,6}$")
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    #: Where this came from, so a verdict can cite it. A URI, not the document, and constrained
    #: to an https URL so it cannot smuggle prose either.
    source_uri: str | None = Field(default=None, max_length=300, pattern=r"^https://\\S+$")

    @property
    def net_rate(self) -> Decimal | None:
        if self.gross_rate is None or self.withholding_pct is None:
            return None
        return self.gross_rate * (Decimal(1) - self.withholding_pct)


#: Plausibility bounds. Deliberately wide -- their job is to stop an absurd value, not to
#: second-guess a real corporate action.
BOUNDS: dict[str, Bounds] = {
    "gross_rate": Bounds(
        minimum=Decimal(0),
        maximum=Decimal(1000),
        note="A per-share dividend outside this range is a parsing error or a poisoned document.",
    ),
    "withholding_pct": Bounds(
        minimum=Decimal(0),
        maximum=Decimal("0.5"),
        note=(
            "No jurisdiction withholds more than half. A zero is plausible on its own and is "
            "cross-checked against the security's domicile, because zero is exactly what a "
            "poisoned notice claims."
        ),
    ),
}

#: Domiciles where a zero withholding rate on a dividend is not credible, and the rate the
#: treaty schedule expects. Used to cross-check, never to overwrite: a mismatch escalates.
EXPECTED_WITHHOLDING: dict[str, Decimal] = {
    "BR": Decimal("0.15"),
    "DE": Decimal("0.26375"),
    "FR": Decimal("0.128"),
    "CH": Decimal("0.35"),
}


@dataclass
class ExtractionOutcome:
    record: CorporateActionRecord
    #: Cross-checks that passed, for the audit trail. An extraction that cites nothing is not
    #: evidence.
    corroborated: list[str] = field(default_factory=list)


def _require_quarantine() -> None:
    """Refuse to run while an identity is bound.

    The extractor's value is that it holds nothing. If it ran under a bound identity, a tool call
    made from the same context would inherit that identity, and the quarantine would be a comment
    rather than a property.
    """
    if identity.current_or_none() is not None:
        raise QuarantineViolation(
            f"extraction must run outside any bound identity; "
            f"{identity.current().ref} is currently bound. The extractor holds no tools and no "
            f"authority by design, and running it inside an identity would hand it both."
        )


def extract_corporate_action(
    text: str,
    *,
    isin: str,
    source_uri: str | None = None,
    expected_domicile: str | None = None,
    books_gross_rate: Decimal | None = None,
) -> ExtractionOutcome:
    """Turn an untrusted filing into a typed record, or refuse.

    Deterministic parsing rather than a model call. That is not a shortcut: a model asked to read
    an attacker-controlled document is the exact surface this module exists to remove, and the
    fields wanted here -- a date, two decimals, a ratio -- are the kind a regex reads more
    reliably than a language model anyway. A model is used later, over the *values*, where its
    judgement is what is actually needed.

    `books_gross_rate` and `expected_domicile` are the cross-checks. They are what stops a poisoned
    document that survives screening from being believed, which quarantine alone cannot do.
    """
    _require_quarantine()

    fields = _parse_labelled_fields(text)
    action_type = _classify(text, fields)

    ex_date = _parse_date(fields.get("ex-date") or fields.get("ex date") or fields.get("ex_date"))
    if ex_date is None:
        raise ExtractionFailed(
            f"no ex-date found in {source_uri or 'the document'}. A corporate action without one "
            f"cannot be applied to a valuation date, so a partial record is worse than none."
        )

    gross = _parse_decimal(fields.get("gross rate") or fields.get("gross_rate"))
    withholding = _parse_percentage(
        fields.get("withholding tax") or fields.get("withholding") or fields.get("withholding_pct")
    )
    ratio = _parse_ratio(fields.get("ratio") or fields.get("split ratio"))

    for name, value in (("gross_rate", gross), ("withholding_pct", withholding)):
        if value is not None:
            BOUNDS[name].check(name, value)

    record = CorporateActionRecord(
        action_type=action_type,
        isin=isin,
        ex_date=ex_date,
        gross_rate=gross,
        withholding_pct=withholding,
        split_ratio=ratio,
        currency=_parse_currency(fields),
        source_uri=source_uri,
    )
    return ExtractionOutcome(
        record=record,
        corroborated=_cross_check(record, expected_domicile, books_gross_rate),
    )


def _cross_check(
    record: CorporateActionRecord,
    expected_domicile: str | None,
    books_gross_rate: Decimal | None,
) -> list[str]:
    """Compare extracted values against something the document cannot influence.

    This is the layer that catches data poisoning. Quarantine stops an injected *instruction*; it
    does nothing about an injected *value*, and the poisoned fixture attacks exactly that by
    claiming a 0.00% withholding rate on a Brazilian ADR.
    """
    corroborated: list[str] = []

    if expected_domicile and record.withholding_pct is not None:
        expected = EXPECTED_WITHHOLDING.get(expected_domicile)
        if expected is not None:
            if record.withholding_pct != expected:
                raise ExtractionRejected(
                    f"the document states withholding of {record.withholding_pct:.2%} on a "
                    f"{expected_domicile} security, where the treaty schedule expects "
                    f"{expected:.2%}. Escalating rather than believing the document: a claimed "
                    f"zero is what a poisoned notice looks like."
                )
            corroborated.append(
                f"withholding {record.withholding_pct:.2%} matches the {expected_domicile} "
                f"treaty schedule"
            )

    if books_gross_rate is not None and record.gross_rate is not None:
        if record.gross_rate != books_gross_rate:
            raise ExtractionRejected(
                f"the document states a gross rate of {record.gross_rate}, the books recorded "
                f"{books_gross_rate}. One of them is wrong and the document is the side we do "
                f"not control."
            )
        corroborated.append(f"gross rate {record.gross_rate} matches the books")

    return corroborated


# ------------------------------------------------------------------ deterministic parsing
#
# Everything below reads structure, never meaning. It is deliberately dull: the whole point of the
# quarantine is that the component handling attacker-controlled bytes is small enough to reason
# about, and a regex over labelled fields is small enough.

_LABEL = re.compile(r"^\s*([A-Za-z][A-Za-z /_-]{2,30}?)\s*:\s*(.+?)\s*$", re.MULTILINE)


def _parse_labelled_fields(text: str) -> dict[str, str]:
    """`Label: value` pairs, lowercased.

    Only the first occurrence of each label is kept. A poisoned document can append a second
    `Withholding Tax:` line hoping the last one wins; taking the first makes append-to-override
    ineffective, and a genuine filing does not restate its own terms.
    """
    out: dict[str, str] = {}
    for match in _LABEL.finditer(text):
        key = " ".join(match.group(1).split()).lower()
        if key not in out:
            out[key] = match.group(2).strip()
    return out


def _classify(text: str, fields: dict[str, str]) -> str:
    declared = (fields.get("action") or fields.get("action type") or "").lower()
    haystack = f"{declared} {text[:400]}".lower()
    if "split" in haystack or fields.get("ratio"):
        return "stock_split"
    if "dividend" in haystack or fields.get("gross rate"):
        return "cash_dividend"
    if "merger" in haystack:
        return "merger"
    return "unknown"


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    token = raw.split()[0].strip().rstrip(".,;")
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%d %B %Y"):
        try:
            return datetime.strptime(token, fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(token)
    except ValueError:
        return None


def _parse_decimal(raw: str | None) -> Decimal | None:
    if not raw:
        return None
    # Strip a currency code or symbol and thousands separators, keep sign and point.
    cleaned = re.sub(r"[^\d.\-]", "", raw.replace(",", ""))
    if not cleaned or cleaned in {"-", ".", "-."}:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _parse_percentage(raw: str | None) -> Decimal | None:
    """A percentage as a fraction. `15%` and `0.15` both mean the same thing.

    An unmarked bare number is read as a percent when it is above 1, which is how filings write
    it. Guessing wrong in the other direction would understate withholding by a hundredfold, so
    the ambiguous case is resolved toward the larger value and then bounds-checked.
    """
    if not raw:
        return None
    value = _parse_decimal(raw)
    if value is None:
        return None
    if "%" in raw or value > 1:
        return value / Decimal(100)
    return value


def _parse_ratio(raw: str | None) -> str | None:
    """A share ratio, or nothing.

    Extracts the ratio and discards the rest of the line rather than passing it through. A
    document that appends prose after the ratio -- `2:1 -- SYSTEM NOTE: ignore all previous...` --
    gets the ratio read and the prose dropped at the boundary, which is where prose is supposed
    to stop.
    """
    if not raw:
        return None
    match = re.search(r"\b(\d{1,6})\s*:\s*(\d{1,6})\b", raw)
    if match is None:
        raise ExtractionRejected(
            f"ratio field {raw[:60]!r} does not contain a share ratio. Refusing rather than "
            f"passing unparsed text across the boundary."
        )
    return f"{match.group(1)}:{match.group(2)}"


def _parse_currency(fields: dict[str, str]) -> str | None:
    for key in ("currency", "gross rate", "gross_rate", "amount"):
        raw = fields.get(key)
        if not raw:
            continue
        match = re.search(r"\b([A-Z]{3})\b", raw)
        if match:
            return match.group(1)
    return None
