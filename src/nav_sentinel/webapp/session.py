"""Who is using the application, and what they are allowed to sign.

An approval in this system is a **signature by a named principal holding a role**, and
`BAND_REQUIREMENTS` decides which roles suffice and how many distinct people are needed. So an
identity is not decoration here: it is the input to the control the whole project is about. A page
that let an operator approve as "the user" would make four-eyes uncountable.

This module is the *cookie*: it decides who the current request belongs to. Where the subject came
from is `identity`'s problem -- a verified Google account when this deployment has an OAuth client,
a fixed roster when it does not, and the sign-in page says which is in force.

The cookie carries the subject and nothing else, signed with HMAC-SHA256. Signed rather than plain
because an unsigned cookie is an identity anyone can type, and an application about zero-trust access
that let a viewer edit their own role in devtools would be making the opposite point.

**The role is deliberately not in the cookie.** It is looked up on every request, so removing someone
from `NAV_ANALYSTS` ends their session at the next click rather than whenever the cookie happens to
expire, and demoting a CIO to reviewer takes effect immediately. A role baked in at sign-in would be
an authority that outlives the decision to grant it -- the session equivalent of a stale access
badge. It costs an environment-variable parse per request and buys revocation.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from dataclasses import dataclass

from nav_sentinel.control_plane.approvals import BAND_REQUIREMENTS, Principal
from nav_sentinel.control_plane.governance import ApprovalClass
from nav_sentinel.webapp import identity

logger = logging.getLogger("nav_sentinel.webapp")

COOKIE = "nav_analyst"

#: The roster, used **only** when no OAuth client is configured. Chosen so every band in
#: `BAND_REQUIREMENTS` is reachable and every refusal is demonstrable: a reviewer cannot sign
#: four-eyes at all, one controller cannot sign it alone, two can, and only the CIO can clear an
#: escalation. A deployment that verifies Google identities ignores this entirely -- see `verify`.
ROSTER: tuple[Principal, ...] = (
    Principal(subject="a.okafor@merian.example", role="reviewer"),
    Principal(subject="j.laurent@merian.example", role="controller"),
    Principal(subject="m.devlin@merian.example", role="controller"),
    Principal(subject="s.raghunathan@merian.example", role="cio"),
)

#: What each role is for, in the operator's language rather than the code's.
ROLE_NOTES: dict[str, str] = {
    "reviewer": "checks and clears immaterial differences; cannot sign a four-eyes case",
    "controller": "signs material corrections; four-eyes needs two different controllers",
    "cio": "the only role that can clear an escalation",
}


def _secret() -> bytes:
    """The signing key.

    From `NAV_SESSION_SECRET` when set, so sessions survive a restart and several instances agree.
    Otherwise a fresh random key per process -- which invalidates sessions on restart, and is the
    right default: falling back to a *fixed* string would put a known signing key in a public
    repository, and every session anywhere would be forgeable by anyone who read it.
    """
    configured = os.environ.get("NAV_SESSION_SECRET")
    if configured:
        return configured.encode()
    global _EPHEMERAL
    if _EPHEMERAL is None:
        _EPHEMERAL = secrets.token_bytes(32)
    return _EPHEMERAL


_EPHEMERAL: bytes | None = None


def sign(subject: str) -> str:
    """A cookie value binding a subject to this deployment's key."""
    mac = hmac.new(_secret(), subject.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{subject}|{mac}"


def verify(cookie: str | None) -> Principal | None:
    """The signed-in analyst, or None. A bad signature is None, not an error.

    `compare_digest`, not `==`: comparing MACs with a short-circuiting equality leaks their contents
    through timing, which is a real attack on a real signature and a free fix.

    A valid signature proves only that *this deployment issued this subject*. The role is resolved
    afterwards, and from the source this deployment actually trusts: the analyst table when Google
    sign-in is configured, the roster when it is not. The two never both apply. A real deployment
    must not also honour the demo roster, whose subjects are published in this repository -- that is
    the shape of back door that gets left in because it was convenient during development.

    Resolving here rather than at sign-in is what makes removal work: the table is read on every
    request, so an analyst taken off it stops being able to act immediately.
    """
    if not cookie or "|" not in cookie:
        return None
    subject, _, mac = cookie.rpartition("|")
    expected = hmac.new(_secret(), subject.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(mac, expected):
        return None
    if identity.uses_google():
        try:
            role = identity.authorised().get(subject)
        except ValueError:
            # An unusable analyst table means this deployment cannot say what anyone is allowed to
            # do, so nobody is signed in. Fail closed, not loud: raising here made a one-character
            # typo in an environment variable a 500 on every page, reported as a service fault in
            # the one place an operator would not look for a configuration error. `/readyz` refuses
            # and names the variable, which is where this belongs.
            logger.error("outcome=analyst_table_unusable -- see /readyz; nobody can sign in")
            return None
        return Principal(subject=subject, role=role) if role else None
    return next((p for p in ROSTER if p.subject == subject), None)


def may_sign(principal: Principal, band: ApprovalClass) -> tuple[bool, str]:
    """Whether this analyst's role is permitted to sign at this band, and how many are needed.

    Answered *before* the attempt so the page can say what will happen, rather than presenting a
    button that always fails. The authority re-checks it at grant time regardless -- this is a
    label, not the control.
    """
    allowed, required = BAND_REQUIREMENTS[band]
    if principal.role not in allowed:
        return False, (
            f"{band.value} may be signed only by {', '.join(sorted(allowed))}; "
            f"you hold {principal.role}"
        )
    if required > 1:
        return True, f"{band.value} needs {required} different signatories"
    return True, f"{band.value} needs one signatory holding {', '.join(sorted(allowed))}"


@dataclass(frozen=True)
class Signatures:
    """Who has already signed a case, so the page can ask for what is missing."""

    subjects: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()

    def outstanding(self, band: ApprovalClass) -> int:
        _allowed, required = BAND_REQUIREMENTS[band]
        return max(0, required - len(set(self.subjects)))
