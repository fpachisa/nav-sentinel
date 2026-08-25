"""The operations console: what an organisation would actually look at.

Four questions, one page, and they are the four the track asks a submission to answer. *Which agents
exist and what may each of them do* -- the registry, including the capabilities nobody is published
to handle. *Where has this case got to* -- the stage history, with both dates. *Why does the fleet
believe what it says* -- verdicts resolved to the observations they cite, with source and digest.
*What did the platform allow and refuse* -- the governance log, denials included.

**Server-rendered, and deliberately.** The deployed service runs `--no-allow-unauthenticated`, so
every request needs an identity token; a page that fetched its own data would need one per fetch and
would fail in a way that looks like an empty system rather than an auth problem. One GET returns a
complete page.

**Read-only, and that is a governance decision rather than a limitation.** Approval stays in
`make approve`, where the four-eyes gate lives. A write path behind a demo UI is exactly where an
unauthenticated posting route gets created by accident, and this project's central claim is that no
such route exists.

**Everything interpolated is escaped.** Not hygiene: observation summaries and verdict prose on the
corporate-action path derive from SEC filings, which are attacker-authored text this system
deliberately ingests. Model Armor screens them on the way in; `html.escape` is what stops a screened
payload becoming markup on the way out.
"""

from __future__ import annotations

from html import escape
from typing import Any

from nav_sentinel.control_plane import packs
from nav_sentinel.registry import discover


def _e(value: Any) -> str:
    """Escape anything on its way into the page. Quotes included, for attribute contexts."""
    return escape("" if value is None else str(value), quote=True)


CSS = """
:root{--ink:#10151C;--soft:#46525F;--faint:#7C8894;--paper:#FBFCFD;--sunk:#F1F4F7;
--rule:#D6DCE4;--accent:#1F5C8B;--wash:#E6EFF6;--deny:#A8322A;--ok:#2C6E5A}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--ink:#E7ECF2;--soft:#A9B4C0;
--faint:#78838F;--paper:#12161B;--sunk:#171C22;--rule:#2A323B;--accent:#7FB3D8;--wash:#182634;
--deny:#E08B84;--ok:#7FC0A8}}
*{box-sizing:border-box}
body{margin:0;background:var(--sunk);color:var(--ink);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 64px}
h1{font-size:20px;margin:0 0 2px;letter-spacing:-.01em}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--faint);
margin:34px 0 10px;font-weight:600}
.sub{color:var(--soft);font-size:13px;margin:0 0 4px}
.card{background:var(--paper);border:1px solid var(--rule);border-radius:6px;overflow:hidden}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-weight:600;color:var(--faint);font-size:11px;text-transform:uppercase;
letter-spacing:.06em;padding:9px 12px;border-bottom:1px solid var(--rule);white-space:nowrap}
td{padding:9px 12px;border-bottom:1px solid var(--rule);vertical-align:top}
tr:last-child td{border-bottom:0}
.scroll{overflow-x:auto}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.none{color:var(--deny);font-weight:600}
.pill{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;font-weight:600;
border:1px solid currentColor}
.allow{color:var(--ok)}.deny{color:var(--deny)}
.muted{color:var(--faint)}
.note{color:var(--soft);font-size:12px;margin:8px 0 0}
.empty{padding:16px 12px;color:var(--faint);font-size:13px}
.k{color:var(--faint);font-size:11px;text-transform:uppercase;letter-spacing:.05em}
"""


def _table(headers: list[str], rows: list[list[str]], empty: str) -> str:
    if not rows:
        return f'<div class="card"><div class="empty">{_e(empty)}</div></div>'
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return (
        f'<div class="card scroll"><table><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def authority_cell(authority: Any) -> str:
    """How an agent's authority is shown.

    Extracted so both branches are testable. Nothing in this fleet may post, so the posting branch
    would otherwise be unreachable in a test -- and an unreachable branch in the column whose job is
    to make posting authority visible is the column being decorative.
    """
    held = []
    if authority.may_propose_remediation:
        held.append("may draft")
    if authority.may_post_entries:
        held.append("MAY POST")
    if not held:
        return '<span class="muted">reports only</span>'
    return f'<span class="pill deny">{_e(", ".join(held))}</span>'


def _fleet() -> str:
    """Which agents exist, what each may call, and what nobody is published to do."""
    rows = []
    for manifest in sorted(discover.all_agents(), key=lambda m: m.agent_id):
        rows.append([
            f'<code>{_e(manifest.ref)}</code>',
            _e(manifest.display_name),
            f'<code>{_e(", ".join(manifest.handles_capabilities) or "—")}</code>',
            f'<code>{_e(", ".join(manifest.allowed_tools) or "—")}</code>',
            f'<code>{_e(", ".join(manifest.data_scopes.read) or "—")}</code>',
            authority_cell(manifest.authority),
        ])
    fleet = _table(
        ["Reference", "Agent", "Handles", "Allowed tools", "Reads", "Authority"],
        rows,
        "no agents published",
    )

    coverage = discover.coverage()
    cover_rows = []
    for capability, ref in sorted(coverage.items()):
        owner = packs.process_of(capability)
        cover_rows.append([
            f'<code>{_e(capability)}</code>',
            _e(owner.name if owner else "—"),
            f'<code>{_e(ref)}</code>' if ref else '<span class="none">NONE</span>',
        ])
    gaps = sum(1 for ref in coverage.values() if ref is None)
    return (
        "<h2>The fleet</h2>"
        '<p class="sub">Discovered from the registry by capability. No agent is named in code.</p>'
        + fleet
        + "<h2>Coverage</h2>"
        f'<p class="sub">{len(coverage)} declared capabilities across '
        f"{len(packs.registered())} processes. "
        f"<strong>{gaps}</strong> are published by nobody &mdash; the registry refuses to route "
        f"them rather than picking whichever agent looks closest.</p>"
        + _table(["Capability", "Process", "Authorised agent"], cover_rows, "no capabilities")
    )


def _case(store: Any, case_id: str) -> str:
    """Where this case has got to, and how it got there."""
    history = store.stages_for(case_id)
    rows = [
        [
            f'<span class="mono">{_e(entry.get("sequence"))}</span>',
            f'<code>{_e(entry.get("from") or "—")}</code>',
            f'<code>{_e(entry.get("to"))}</code>',
            f'<span class="mono">{_e(entry.get("occurred_on") or "—")}</span>',
            f'<span class="mono muted">{_e(str(entry.get("recorded_at"))[:19])}</span>',
            _e(entry.get("note") or ""),
        ]
        for entry in history
    ]
    dates = [e.get("occurred_on") for e in history if e.get("occurred_on")]
    span = ""
    if len(dates) >= 2:
        from datetime import date

        days = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days
        span = (
            f" Business dates span <strong>{days} days</strong> "
            f"({_e(dates[0])} to {_e(dates[-1])}); the wall-clock column shows when this system "
            f"wrote each row."
        )
    return (
        "<h2>Case</h2>"
        f'<p class="sub"><code>{_e(case_id)}</code> &mdash; {len(history)} recorded transitions.'
        f"{span}</p>"
        + _table(
            ["#", "From", "To", "Happened on", "Written at", "Note"],
            rows,
            "no stage history for this case",
        )
    )


def _reasoning(store: Any, case_id: str) -> str:
    """Why the fleet believes what it says: the evidence, with provenance."""
    observations = store.observations_for(case_id)
    rows = [
        [
            f'<code>{_e(observation.observation_id)}</code>',
            f'<code>{_e(observation.agent_ref)}</code>',
            f'<code>{_e(observation.tool)}</code>',
            f'<code>{_e(observation.args)}</code>',
            "".join(
                f'<div><span class="k">{_e(k)}</span> <span class="mono">{_e(v)}</span></div>'
                for k, v in sorted(observation.observed.items())
            )
            or '<span class="muted">nothing projected</span>',
            f'<code>{_e(observation.source)}</code><br>'
            f'<span class="mono muted">{_e(observation.digest[:16])}</span>',
            f'<span class="mono muted">{_e(str(observation.retrieved_at)[:19])}</span>'
            + (
                f'<br><span class="pill deny">{_e(observation.armor_verdict)}</span>'
                if observation.armor_verdict
                else ""
            ),
        ]
        for observation in observations
    ]
    return (
        "<h2>Reasoning</h2>"
        '<p class="sub">Every observation an agent recorded on this case. A verdict may cite only '
        "these, and only ones recorded against this case &mdash; which is what makes a citation "
        "checkable rather than decorative.</p>"
        + _table(
            ["Observation", "Recorded by", "Tool", "Arguments", "Facts", "Source / digest", "When"],
            rows,
            "no observations recorded for this case",
        )
    )


def _governance(store: Any, case_id: str) -> str:
    """What the platform allowed and what it refused."""
    decisions = store.decisions_for(case_id)
    rows = []
    for record in decisions:
        # The stored keys are `as_span_attributes()`'s, not the model's field names. Reading
        # `policy_id`/`effect`/`reason` produced an empty Governance panel on a case with fourteen
        # recorded decisions -- a screen that showed nothing and looked like a system that had
        # decided nothing.
        effect = str(record.get("nav.policy.effect", ""))
        denied = effect == "deny"
        rows.append([
            f'<span class="mono">{_e(record.get("sequence"))}</span>',
            f'<code>{_e(record.get("nav.policy.id"))}</code>',
            f'<span class="pill {"deny" if denied else "allow"}">{_e(effect.upper())}</span>',
            f'<code>{_e(record.get("nav.agent.ref") or "—")}</code>',
            f'<code>{_e(record.get("nav.policy.resource") or "—")}</code>',
            _e(record.get("nav.policy.reason")),
        ])
    denials = sum(1 for r in decisions if str(r.get("nav.policy.effect")) == "deny")
    return (
        "<h2>Governance</h2>"
        f'<p class="sub">{len(decisions)} recorded decisions, <strong>{denials}</strong> of them '
        "refusals. Denials are kept because a refusal that left no trace is indistinguishable from "
        "a request that was never made.</p>"
        + _table(
            ["#", "Policy", "Effect", "Agent", "Resource", "Reason"],
            rows,
            "no policy decisions recorded for this case",
        )
    )


def render(store: Any, case_id: str, *, backend: str) -> str:
    """The whole page. Nothing here writes."""
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>NAV Sentinel &mdash; operations console</title>"
        f"<style>{CSS}</style></head><body><div class=\"wrap\">"
        "<h1>NAV Sentinel &mdash; operations console</h1>"
        f'<p class="sub">Read-only. Store: <code>{_e(backend)}</code>. '
        "Approval is deliberately not here: it lives behind the four-eyes gate in "
        "<code>make approve</code>, because a write path behind a console is where an "
        "unauthenticated posting route gets created by accident.</p>"
        + _fleet()
        + _case(store, case_id)
        + _reasoning(store, case_id)
        + _governance(store, case_id)
        + '<p class="note">Every value on this page is HTML-escaped. Observation summaries and '
        "verdict prose on the corporate-action path derive from SEC filings &mdash; "
        "attacker-authored text this system deliberately ingests. Model Armor screens it inbound; "
        "escaping is what stops a screened payload becoming markup outbound.</p>"
        "</div></body></html>"
    )
