"""Who the analyst is, verified rather than chosen.

The desk's first version let you pick a name from a list. That was honest about being a demo, and it
was also the weakest thing in the project: an approval here is *a signature by a named principal*,
and four-eyes counts distinct people, so an identity anyone can select is a control anyone can
satisfy alone.

This verifies a Google ID token instead. The browser gets a real Google sign-in, posts the resulting
JWT, and the server checks its signature, issuer, audience and expiry against Google's published
keys before believing a single field in it. What comes back is an email address Google has
authenticated and, importantly, a claim about whether it verified that address.

**Authentication is not authorisation.** Signing in proves who you are; it grants nothing. The role
that decides what you may sign comes from a table this deployment holds, keyed by email, and an
address that is not in it can sign in and approve nothing. A person cannot bring their own role.

Falls back to the roster when no client id is configured, so local development and the offline test
suite need no Google round-trip -- and the sign-in page says which of the two is in force, because a
page that looked identical either way would make a demo indistinguishable from the real thing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from nav_sentinel.control_plane.approvals import BAND_REQUIREMENTS, Principal

#: Roles this deployment recognises, most privileged last. Anything else is refused rather than
#: treated as unknown-but-harmless: a typo in the table must not silently mint an authority.
KNOWN_ROLES = {role for allowed, _ in BAND_REQUIREMENTS.values() for role in allowed}


class UnknownAnalyst(PermissionError):
    """Authenticated, and not on this deployment's list of people who may sign anything."""


@dataclass(frozen=True)
class Verified:
    """A Google-authenticated identity, before any role is attached to it."""

    email: str
    email_verified: bool
    name: str = ""


def client_id() -> str:
    """The OAuth client id, or empty when this deployment is not using Google sign-in."""
    return os.environ.get("NAV_OAUTH_CLIENT_ID", "").strip()


def uses_google() -> bool:
    return bool(client_id())


def authorised() -> dict[str, str]:
    """Email to role, from `NAV_ANALYSTS`, e.g. `a@x.com:controller,b@y.com:cio`.

    Configuration rather than code, so adding a signatory is a deployment change and not a release
    -- and so the people who may sign are visible in one place to whoever operates the service.
    """
    table: dict[str, str] = {}
    for entry in os.environ.get("NAV_ANALYSTS", "").split(","):
        email, _, role = entry.partition(":")
        email, role = email.strip().lower(), role.strip().lower()
        if not email or not role:
            continue
        if role not in KNOWN_ROLES:
            # Refused loudly. A role nobody recognises would otherwise sit in the table looking
            # like an authority while satisfying no band -- an approver who can never approve.
            raise ValueError(
                f"{email} is assigned role {role!r}, which no approval band recognises. "
                f"Known roles: {sorted(KNOWN_ROLES)}"
            )
        table[email] = role
    return table


def verify_google_credential(credential: str) -> Verified:
    """Check a Google ID token and return what it says. Raises if it does not hold up.

    Verified against Google's published keys with the audience pinned to this deployment's client
    id. Without the audience check a token minted for *any* Google application would authenticate
    here, which is the classic mistake with this flow: the signature is valid, the issuer is Google,
    and the token was never meant for us.
    """
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    audience = client_id()
    if not audience:
        # Explicit, rather than relying on google-auth skipping the check only when the audience is
        # `None` and empty string happening not to be `None`. That is a library's internal `is not
        # None` standing between this service and accepting any Google-signed token in the world,
        # and one tidy-up to `client_id() or None` removes it. The push handler carries the same
        # guard for the same reason.
        raise ValueError(
            "this deployment has no OAuth client configured, so it cannot verify a Google token"
        )

    claims = id_token.verify_oauth2_token(
        credential, google_requests.Request(), audience
    )
    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise ValueError(f"unexpected issuer {claims.get('iss')!r}")
    return Verified(
        email=str(claims.get("email", "")).lower(),
        email_verified=bool(claims.get("email_verified")),
        name=str(claims.get("name", "")),
    )


def principal_for(verified: Verified) -> Principal:
    """Attach this deployment's role to a verified identity, or refuse.

    An unverified email address is refused outright. Google will issue a token for an address whose
    ownership it has not confirmed, and an approval trail is a record of *who signed* -- a name
    nobody verified is worth less than no name at all.
    """
    if not verified.email:
        raise UnknownAnalyst("the token carries no email address")
    if not verified.email_verified:
        raise UnknownAnalyst(
            f"{verified.email} is not a Google-verified address, so it cannot sign an approval"
        )
    role = authorised().get(verified.email)
    if role is None:
        raise UnknownAnalyst(
            f"{verified.email} is not on this deployment's list of authorised analysts. "
            f"Signing in proves who you are; it does not grant a role."
        )
    return Principal(subject=verified.email, role=role)
