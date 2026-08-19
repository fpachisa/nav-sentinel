"""SEC EDGAR: issuer filings and corporate-action evidence.

Everything this module returns is authored by a third party and fetched over the public
internet. It is untrusted by definition, so the corporate-actions investigator must route
it through the Agent Gateway's Model Armor screening before any of it reaches a model
context. Nothing here admits content on its own.

SEC access policy requires a contact address in the User-Agent of automated requests and
rate-limits to 10 requests per second. Both are honoured here.
"""

from __future__ import annotations

import time
from datetime import date
from functools import lru_cache
from threading import Lock

import httpx

from nav_sentinel.config import settings

SOURCE_NAME = "sec_edgar"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FULLTEXT_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data"

_MIN_INTERVAL = 0.12  # ~8 requests/second, inside the SEC's published ceiling
_last_call = 0.0
_throttle = Lock()


class ContactNotConfigured(RuntimeError):
    """Raised when NAV_SEC_CONTACT is unset.

    Deliberately fatal rather than defaulted: sending unattributed automated traffic to
    EDGAR risks having the project's access blocked, and silently inventing a contact
    address would be worse than failing.
    """


def _user_agent() -> str:
    contact = settings().sec_contact.strip()
    if not contact:
        raise ContactNotConfigured(
            "SEC EDGAR requires a contact address in the User-Agent. "
            "Set NAV_SEC_CONTACT in .env (see .env.example)."
        )
    return f"NAV-Sentinel/0.1 ({contact})"


def _headers() -> dict[str, str]:
    return {"User-Agent": _user_agent(), "Accept-Encoding": "gzip, deflate"}


def _throttled() -> None:
    """Wait our turn, without holding the lock while waiting.

    The previous version slept inside `with _throttle:`, so every other caller blocked on the
    mutex for the duration of the sleep -- and under the asynchronous runtime S3 introduces that
    stalls the event loop rather than one thread. The lock now only guards the reservation; the
    sleep happens outside it.
    """
    global _last_call
    while True:
        with _throttle:
            now = time.monotonic()
            wait = _MIN_INTERVAL - (now - _last_call)
            if wait <= 0:
                _last_call = now
                return
            # Reserve our slot before releasing, so concurrent callers queue rather than all
            # waking to the same instant and firing together.
            _last_call = _last_call + _MIN_INTERVAL
            wait = _last_call - now
        time.sleep(wait)
        return


def _get(url: str, params: dict | None = None, *, attempts: int = 4) -> httpx.Response:
    """GET with backoff on transient failures.

    EDGAR's full-text search intermittently returns 500 for requests that succeed on retry,
    and rate-limits with 403 under load. An investigator that abandons a case because an
    upstream hiccuped would report "root cause unknown" for a break it could have explained,
    so transient faults are retried and only a persistent failure is allowed to surface.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        _throttled()
        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                r = client.get(url, params=params, headers=_headers())
            if r.status_code in (403, 429, 500, 502, 503, 504) and attempt < attempts - 1:
                last = httpx.HTTPStatusError(
                    f"transient {r.status_code}", request=r.request, response=r
                )
                time.sleep(0.5 * (2 ** attempt))
                continue
            r.raise_for_status()
            return r
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = exc
            if attempt == attempts - 1:
                break
            time.sleep(0.5 * (2 ** attempt))
    raise RuntimeError(f"EDGAR request failed after {attempts} attempts: {url}") from last


@lru_cache(maxsize=128)
def recent_filings(cik: int, forms: tuple[str, ...] = ()) -> list[dict]:
    """Recent filings for an issuer, newest first.

    `forms` filters to the form types that carry corporate-action detail: 8-K for material
    events including splits and dividend declarations, DEF 14A for shareholder actions.
    """
    data = _get(SUBMISSIONS_URL.format(cik=cik)).json()
    recent = data.get("filings", {}).get("recent", {})
    rows: list[dict] = []
    for i, form in enumerate(recent.get("form", [])):
        if forms and form not in forms:
            continue
        rows.append(
            {
                "issuer": data.get("name"),
                "cik": cik,
                "form": form,
                "filing_date": recent["filingDate"][i],
                "accession": recent["accessionNumber"][i],
                "primary_document": recent["primaryDocument"][i],
                "description": recent.get("primaryDocDescription", [None] * (i + 1))[i],
                "source_uri": filing_uri(cik, recent["accessionNumber"][i],
                                         recent["primaryDocument"][i]),
            }
        )
    return rows


def filing_uri(cik: int, accession: str, document: str) -> str:
    return f"{ARCHIVE_URL}/{cik}/{accession.replace('-', '')}/{document}"


def search_filings(
    query: str, forms: tuple[str, ...] = ("8-K",), start: date | None = None,
    end: date | None = None, limit: int = 10,
) -> list[dict]:
    """Full-text search across EDGAR. Used to locate a corporate-action announcement when
    the issuer's CIK is known but the specific filing is not."""
    params: dict[str, str] = {"q": f'"{query}"', "forms": ",".join(forms)}
    if start and end:
        params |= {"dateRange": "custom", "startdt": start.isoformat(), "enddt": end.isoformat()}

    hits = _get(FULLTEXT_URL, params).json().get("hits", {}).get("hits", [])
    out: list[dict] = []
    for h in hits[:limit]:
        src = h.get("_source", {})
        ident = h.get("_id", "")
        accession = ident.split(":")[0] if ":" in ident else ident
        document = ident.split(":")[1] if ":" in ident else ""
        ciks = src.get("ciks") or []
        cik = int(ciks[0]) if ciks else 0
        out.append(
            {
                "issuer": (src.get("display_names") or [None])[0],
                "cik": cik,
                "form": src.get("root_form") or src.get("file_type"),
                "filing_date": src.get("file_date"),
                "accession": accession,
                "source_uri": filing_uri(cik, accession, document) if cik and document else None,
            }
        )
    return out


def fetch_filing_text(source_uri: str, max_bytes: int = 200_000) -> str:
    """Retrieve raw filing text.

    The return value is UNTRUSTED. Callers must pass it through
    `gateway.admit_untrusted_content` before placing it in a model context; the gateway
    is the only path that screens it.
    """
    body = _get(source_uri).text
    return body[:max_bytes]
