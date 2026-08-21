"""What a NAV error case is, as this process sees it.

Small on purpose. The stage machine, the audit record and the approval band all live in the control
plane and take flat values, so this carries the few facts the process actually owns and hands them
over -- the same `to_facts`/`to_brief` pair the other two processes use.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from nav_sentinel.control_plane.governance import CaseBrief, CaseFacts, Impact


class NavErrorCase(BaseModel):
    """A published NAV that turned out to be wrong."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    fund_id: str
    #: The valuation point whose NAV was misstated.
    as_of: date
    #: Magnitude of the misstatement. Positive: materiality is assessed on magnitude, and a signed
    #: value would make a 285bps overstatement compare as smaller than a 20bps understatement.
    error_bps: Decimal
    capability: str = "rem.unclassified"
    status: str = "open"
    #: Filled once transfer agency has reported it. None means nobody has asked yet, which is a
    #: different thing from nobody having dealt.
    affected_investors: int | None = None
    note: str = ""

    @property
    def recurrence_key(self) -> str:
        """How this case identifies its fund for recurrence purposes.

        Deliberately the same string `memory.recurrence` builds, and that duplication is why this
        property exists rather than the literal being written twice: a case that filed itself under
        a key nothing looks up would be invisible to the next error's assessment, and the count
        would silently read low.
        """
        return f"{self.fund_id}:nav_error"

    def to_facts(self) -> CaseFacts:
        """What the control plane may know. Impact in **investors**, this process's unit."""
        return CaseFacts(
            case_id=self.case_id,
            subject_id=self.fund_id,
            as_of=self.as_of,
            capability=self.capability,
            impact=(
                Impact(value=Decimal(self.affected_investors), unit="investors")
                if self.affected_investors is not None
                else None
            ),
            status=self.status,
            item_count=1,
            recurrence_key=self.recurrence_key,
            # A published NAV error does not clear on size alone: it is reportable, and the record
            # of it is what the next assessment reads.
            no_auto_clear=True,
        )

    def to_brief(self) -> CaseBrief:
        """What an agent may know. The process renders its own facts as prose."""
        population = (
            f"{self.affected_investors} investors dealt at the misstated price"
            if self.affected_investors is not None
            else "the affected population has not yet been reported by transfer agency"
        )
        return CaseBrief(
            case_id=self.case_id,
            subject_id=self.fund_id,
            as_of=self.as_of,
            capability=self.capability,
            breaks=(
                f"  - published NAV for {self.as_of.isoformat()} misstated by "
                f"{self.error_bps}bps\n"
                f"  - {population}"
                + (f"\n  - {self.note}" if self.note else "")
            ),
        )

    def as_document(self) -> dict[str, object]:
        """The case as the repository keeps it, including the key recurrence reads."""
        return {
            "case_id": self.case_id,
            "subject_id": self.fund_id,
            "as_of": self.as_of.isoformat(),
            "recurrence_key": self.recurrence_key,
            "error_bps": str(self.error_bps),
            "capability": self.capability,
            "status": self.status,
            "affected_investors": self.affected_investors,
            "note": self.note,
        }
