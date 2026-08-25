"""The pages an analyst works in.

Server-rendered HTML, no build step and no client framework. That is a constraint worth stating
rather than apologising for: the service runs `--no-allow-unauthenticated`, so every request carries
an identity token, and a single-page app fetching its own data would need one per fetch and would
fail looking like an empty system. One GET returns a page; one POST does one thing and redirects.

Visual language is the project's own, not a new one: Archivo and JetBrains Mono are the faces the
architecture figures already use, and the accent is the same `#1F5C8B` those figures are drawn in.
Band colour is semantic and separate from the accent -- cleared, one signature, four eyes,
escalation -- because an operator scanning a queue at 6am reads colour before text.

Everything interpolated is escaped. Verdict prose and observation summaries on the corporate-action
path derive from SEC filings, which this system deliberately ingests: Model Armor screens them
inbound, and escaping is what stops a screened payload leaving as markup.
"""

from __future__ import annotations

from html import escape
from typing import Any

from nav_sentinel.control_plane.approvals import Principal
from nav_sentinel.webapp import session

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    "family=Archivo:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap\">"
)

CSS = """
:root{
  --ink:#101720; --soft:#4A5766; --faint:#7C8894;
  --paper:#FFFFFF; --sunk:#EEF1F5; --line:#D8DEE6;
  --accent:#1F5C8B; --accent-wash:#E7EFF6;
  --cleared:#2C6E5A; --single:#1F5C8B; --four:#8A5A1B; --escalate:#A8322A;
  --deny-wash:#FBEDEB;
}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --ink:#E8EDF3; --soft:#A7B2BE; --faint:#77828E;
  --paper:#141A21; --sunk:#0E1318; --line:#28313B;
  --accent:#7FB3D8; --accent-wash:#17242F;
  --cleared:#7FC0A8; --single:#7FB3D8; --four:#D9A55B; --escalate:#E08B84;
  --deny-wash:#2A1A19;
}}
:root[data-theme=dark]{
  --ink:#E8EDF3; --soft:#A7B2BE; --faint:#77828E;
  --paper:#141A21; --sunk:#0E1318; --line:#28313B;
  --accent:#7FB3D8; --accent-wash:#17242F;
  --cleared:#7FC0A8; --single:#7FB3D8; --four:#D9A55B; --escalate:#E08B84;
  --deny-wash:#2A1A19;
}
*{box-sizing:border-box}
body{margin:0;background:var(--sunk);color:var(--ink);
  font-family:Archivo,-apple-system,"Segoe UI",Helvetica,Arial,sans-serif;font-size:15px;line-height:1.5}
.mono,code{font-family:"JetBrains Mono",ui-monospace,Menlo,monospace;font-size:12.5px}
.bar{background:var(--paper);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
.bar-in{max-width:1120px;margin:0 auto;padding:0 22px;display:flex;align-items:center;gap:26px;height:56px}
.brand{font-weight:700;letter-spacing:-.01em;white-space:nowrap}
.brand span{color:var(--faint);font-weight:500}
nav{display:flex;gap:18px;margin-left:6px}
nav a{color:var(--soft);text-decoration:none;font-size:13.5px;padding:4px 0;border-bottom:2px solid transparent}
nav a:hover{color:var(--ink)}
nav a.on{color:var(--accent);border-bottom-color:var(--accent);font-weight:600}
.who{margin-left:auto;display:flex;align-items:center;gap:10px;font-size:13px;color:var(--soft)}
.who b{color:var(--ink);font-weight:600}
main{max-width:1120px;margin:0 auto;padding:26px 22px 72px}
h1{font-size:21px;margin:0 0 4px;letter-spacing:-.015em;text-wrap:balance}
h2{font-size:11.5px;text-transform:uppercase;letter-spacing:.09em;color:var(--faint);
  margin:30px 0 10px;font-weight:600}
p.lede{color:var(--soft);margin:0 0 18px;max-width:68ch}
.card{background:var(--paper);border:1px solid var(--line);border-radius:7px}
.pad{padding:16px 18px}
.grid{display:grid;grid-template-columns:1fr 340px;gap:20px;align-items:start}
@media (max-width:900px){.grid{grid-template-columns:1fr}}
.stick{position:sticky;top:76px}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--faint);
  font-weight:600;padding:10px 14px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:11px 14px;border-bottom:1px solid var(--line);vertical-align:top;font-size:13.5px}
tr:last-child td{border-bottom:0}
tr.click:hover{background:var(--accent-wash);cursor:pointer}
.scroll{overflow-x:auto}
.num{font-variant-numeric:tabular-nums;font-family:"JetBrains Mono",monospace;font-size:12.5px;white-space:nowrap}
.chip{display:inline-block;padding:2px 9px;border-radius:11px;font-size:11px;font-weight:600;
  border:1px solid currentColor;white-space:nowrap}
.b-auto_clear{color:var(--cleared)} .b-single_reviewer{color:var(--single)}
.b-four_eyes{color:var(--four)} .b-cio_escalation{color:var(--escalate)}
.none{color:var(--escalate);font-weight:600}
.muted{color:var(--faint)}
.btn{display:inline-block;border:1px solid var(--accent);background:var(--accent);color:#fff;
  padding:8px 15px;border-radius:6px;font:inherit;font-size:13.5px;font-weight:600;cursor:pointer;
  text-decoration:none}
.btn:hover{filter:brightness(1.08)}
.btn.ghost{background:transparent;color:var(--accent)}
.btn:disabled{opacity:.45;cursor:not-allowed;filter:none}
.btn:focus-visible,a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
form{display:inline}
.note{background:var(--accent-wash);border:1px solid var(--line);border-radius:6px;padding:12px 14px;
  font-size:13px;color:var(--soft)}
.deny{background:var(--deny-wash);border-color:var(--escalate);color:var(--escalate)}
.kv{display:grid;grid-template-columns:auto 1fr;gap:5px 14px;font-size:13px}
.kv dt{color:var(--faint);font-size:11px;text-transform:uppercase;letter-spacing:.05em;padding-top:2px}
.kv dd{margin:0}
ul.plain{margin:6px 0 0;padding-left:18px;color:var(--soft);font-size:13px}
.step{display:flex;gap:12px;padding:10px 0;border-bottom:1px solid var(--line)}
.step:last-child{border-bottom:0}
.step .dot{width:9px;height:9px;border-radius:50%;background:var(--accent);margin-top:6px;flex:none}
.step .dot.deny{background:var(--escalate)}
.step .when{color:var(--faint);font-size:11.5px;white-space:nowrap;width:88px;flex:none;padding-top:2px}
"""


def _e(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


#: Break types in an operator's words. `nav.unclassified` is an internal enum meaning triage has not
#: run; showing it as a page title made every exception look identical and named none of them.
BREAK_TITLES = {
    "cash_balance": "Cash balance difference",
    "position_quantity": "Position quantity difference",
    "market_value": "Market value difference",
    "nav_per_share": "NAV per share difference",
}


def describe(document: dict[str, Any]) -> str:
    """What this exception is, for a human. Never an enum."""
    types = [t for t in document.get("break_types", []) if t]
    titles = [BREAK_TITLES.get(t, t.replace("_", " ").capitalize()) for t in dict.fromkeys(types)]
    isin = document.get("isin")
    head = " and ".join(titles) if titles else "Exception"
    return f"{head}{f' · {isin}' if isin else ''}"


def classification(document: dict[str, Any]) -> str:
    """The capability, or an honest statement that nothing has classified it yet."""
    capability = str(document.get("capability", ""))
    if not capability or capability.endswith(".unclassified"):
        return '<span class="muted">not yet triaged</span>'
    return f"<code>{_e(capability)}</code>"


def _chip(band: str) -> str:
    return f'<span class="chip b-{_e(band)}">{_e(band.replace("_", " "))}</span>'


def shell(title: str, body: str, *, principal: Principal | None, active: str = "") -> str:
    """The application frame. One layout, so every page reads as the same product."""
    links = [("queue", "/app", "Exceptions"), ("remediation", "/app/remediation", "Remediation"),
             ("fleet", "/app/fleet", "Fleet"), ("audit", "/console", "Audit view")]
    nav = "".join(
        f'<a href="{_e(href)}" class="{"on" if key == active else ""}">{_e(label)}</a>'
        for key, href, label in links
    )
    who = (
        f'<div class="who"><span>{_e(principal.role)}</span><b>{_e(principal.subject)}</b>'
        f'<form method="post" action="/app/signout">'
        f'<button class="btn ghost" type="submit">Sign out</button></form></div>'
        if principal
        else ""
    )
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_e(title)}</title>{FONTS}<style>{CSS}</style></head><body>"
        f'<div class="bar"><div class="bar-in">'
        f'<div class="brand">NAV Sentinel <span>· exception desk</span></div>'
        f"<nav>{nav}</nav>{who}</div></div><main>{body}</main></body></html>"
    )


def signin(as_of: str) -> str:
    """Choose an analyst. No password, and the page says why."""
    rows = "".join(
        f"<tr><td><b>{_e(p.subject)}</b></td>"
        f'<td><span class="chip b-single_reviewer">{_e(p.role)}</span></td>'
        f'<td class="muted">{_e(session.ROLE_NOTES.get(p.role, ""))}</td>'
        f'<td style="text-align:right"><form method="post" action="/app/signin">'
        f'<input type="hidden" name="subject" value="{_e(p.subject)}">'
        f'<button class="btn" type="submit">Sign in</button></form></td></tr>'
        for p in session.ROSTER
    )
    return shell(
        "Sign in — NAV Sentinel",
        "<h1>Exception desk</h1>"
        f'<p class="lede">Valuation point {_e(as_of)}. Sign in as an analyst to work the queue. '
        "Which role you hold decides what you may sign: a reviewer cannot approve a four-eyes "
        "correction at all, and two different controllers are needed for one.</p>"
        f'<div class="card scroll"><table><thead><tr><th>Analyst</th><th>Role</th>'
        f"<th>What the role may sign</th><th></th></tr></thead><tbody>{rows}</tbody></table></div>"
        '<p class="note" style="margin-top:16px">There is no password here, and nothing is '
        "collected. Identity in front of this service is the deployment's job — Cloud Run refuses "
        "anonymous callers before a request reaches this code. What a service token cannot carry is "
        "<em>which analyst</em> is acting, and four-eyes has to count people, so the roster is "
        "fixed and server-side.</p>",
        principal=None,
    )


def queue(items: list[Any], *, principal: Principal, as_of: str) -> str:
    """The exceptions queue: what came out of the valuation point, and who must sign each one."""
    if not items:
        body = (
            "<h1>Exceptions</h1>"
            f'<p class="lede">Nothing detected for {_e(as_of)} yet. Running the cycle compares the '
            "fund's own books against the custodian's, scores each difference in basis points of "
            "NAV, and derives who must approve a correction.</p>"
            '<div class="card pad"><form method="post" action="/app/cycle">'
            '<button class="btn" type="submit">Run reconciliation</button></form>'
            '<p class="muted" style="margin:12px 0 0;font-size:13px">No model is called. Finding a '
            "break is arithmetic over two books, and asking a model to do subtraction would be "
            "spending a request to be told what the numbers already say.</p></div>"
        )
        return shell("Exceptions — NAV Sentinel", body, principal=principal, active="queue")

    rows = ""
    for item in items:
        state = (
            '<span class="chip b-auto_clear">approved</span>'
            if item.approved
            else '<span class="chip b-single_reviewer">investigated</span>'
            if item.worked
            else '<span class="muted">not started</span>'
        )
        rows += (
            f'<tr class="click" onclick="location.href=\'/app/case/{_e(item.case_id)}\'">'
            f'<td><a href="/app/case/{_e(item.case_id)}">{_e(item.title)}</a>'
            f'<div class="mono muted" style="font-size:11.5px">'
            f'{_e(item.case_id.replace("CASE-MERID-GEF-", ""))}</div></td>'
            f"<td>{classification({'capability': item.capability})}</td>"
            f'<td class="num">{_e(item.impact_bps)}</td>'
            f"<td>{_chip(item.band)}</td>"
            f"<td>{state}</td></tr>"
        )

    worked = sum(1 for i in items if i.worked)
    body = (
        "<h1>Exceptions</h1>"
        f'<p class="lede">{len(items)} differences at the {_e(as_of)} valuation point, '
        f"{worked} investigated. Impact is basis points of NAV; the band is derived by the control "
        "plane from that magnitude and decides who must sign.</p>"
        '<div class="card scroll"><table><thead><tr><th>Exception</th><th>Classification</th>'
        '<th style="text-align:left">Impact</th><th>Must be signed by</th><th>State</th></tr>'
        f"</thead><tbody>{rows}</tbody></table></div>"
        '<div style="margin-top:14px"><form method="post" action="/app/cycle">'
        '<button class="btn ghost" type="submit">Re-run reconciliation</button></form></div>'
    )
    return shell("Exceptions — NAV Sentinel", body, principal=principal, active="queue")


def _evidence(observations: list[Any]) -> str:
    if not observations:
        return '<div class="pad muted">No evidence recorded yet.</div>'
    rows = ""
    for observation in observations:
        facts = "".join(
            f'<div><span class="muted" style="font-size:11px;text-transform:uppercase">'
            f'{_e(k)}</span> <span class="num">{_e(v)}</span></div>'
            for k, v in sorted(observation.observed.items())
        )
        rows += (
            f'<tr><td class="mono">{_e(observation.observation_id)}</td>'
            f'<td><code>{_e(observation.tool)}</code>'
            f'<div class="muted" style="font-size:11.5px">{_e(observation.args)}</div></td>'
            f"<td>{facts}</td>"
            f'<td><code>{_e(observation.source)}</code>'
            f'<div class="mono muted">{_e(observation.digest[:16])}</div></td></tr>'
        )
    return (
        '<div class="scroll"><table><thead><tr><th>Observation</th><th>Tool</th><th>Facts</th>'
        f"<th>Source / digest</th></tr></thead><tbody>{rows}</tbody></table></div>"
    )


def _proposal(proposal: dict[str, Any] | None) -> str:
    if not proposal:
        return '<div class="pad muted">No correction drafted.</div>'
    legs = "".join(
        f'<tr><td><code>{_e(line["account"])}</code></td>'
        f'<td class="mono">{_e(line["currency"])}</td>'
        f'<td class="num">{_e(line["debit"])}</td>'
        f'<td class="num">{_e(line["credit"])}</td></tr>'
        for line in proposal.get("lines", [])
    )
    quantities = "".join(
        f'<tr><td><code>{_e(q["account"])}</code></td><td class="mono">{_e(q["isin"])}</td>'
        f'<td class="num">{_e(q["from_quantity"])} &rarr; {_e(q["to_quantity"])}</td></tr>'
        for q in proposal.get("quantity_lines", [])
    )
    table = (
        '<div class="scroll"><table><thead><tr><th>Account</th><th>Ccy</th><th>Debit</th>'
        f"<th>Credit</th></tr></thead><tbody>{legs}</tbody></table></div>"
        if legs
        else ""
    )
    shares = (
        '<div class="scroll"><table><thead><tr><th>Account</th><th>ISIN</th><th>Quantity</th></tr>'
        f"</thead><tbody>{quantities}</tbody></table></div>"
        if quantities
        else ""
    )
    return (
        f'<div class="pad"><dl class="kv">'
        f'<dt>Outcome</dt><dd><code>{_e(proposal["outcome"])}</code></dd>'
        f'<dt>Residual</dt><dd class="num">{_e(proposal["expected_residual"])}</dd>'
        f'<dt>Rationale</dt><dd>{_e(proposal["rationale"])}</dd></dl></div>'
        + table
        + shares
    )


def case(detail: dict[str, Any], *, principal: Principal) -> str:
    """One exception, from the numbers through the reasoning to the signature."""
    document = detail["document"]
    case_id = str(document.get("case_id", ""))
    band = str(document.get("approval_band", "single_reviewer"))
    triage = document.get("triage")
    verdict = document.get("verdict")
    signed = list(document.get("signed_by", []))

    left = (
        f'<div class="card pad"><dl class="kv">'
        f'<dt>Case</dt><dd class="mono">{_e(case_id)}</dd>'
        f'<dt>Valuation</dt><dd class="num">{_e(document.get("as_of"))}</dd>'
        f'<dt>Impact</dt><dd class="num">{_e(document.get("impact_bps"))} bps</dd>'
        f"<dt>Band</dt><dd>{_chip(band)}</dd>"
        f"<dt>Classification</dt><dd>{classification(document)}</dd></dl></div>"
    )

    if detail["signals"]:
        left += "<h2>What the numbers say</h2><div class='card pad'><ul class='plain'>" + "".join(
            f"<li>{_e(s)}</li>" for s in detail["signals"]
        ) + "</ul><p class='muted' style='font-size:12.5px;margin:10px 0 0'>Computed from the books, before any model ran.</p></div>"

    if triage:
        left += (
            "<h2>Triage</h2><div class='card pad'>"
            f'<dl class="kv"><dt>Capability</dt><dd><code>{_e(triage["capability"])}</code></dd>'
            f'<dt>Confidence</dt><dd class="num">{_e(f"{triage["confidence"]:.2f}")}</dd>'
            f'<dt>Reasoning</dt><dd>{_e(triage["reasoning"])}</dd></dl>'
            + (
                f'<p class="note deny" style="margin-top:10px">Model answered '
                f'<code>{_e(triage["overridden_from"])}</code>, below the confidence floor, so it '
                f"was escalated instead of routed.</p>"
                if triage.get("overridden_from")
                else ""
            )
            + "</div>"
        )

    if document.get("routed") is False:
        left += (
            "<h2>Routing</h2>"
            f'<div class="card pad note deny">{_e(document.get("refusal"))}</div>'
        )

    if verdict:
        left += (
            "<h2>Established cause</h2><div class='card pad'>"
            f'<p style="margin:0 0 10px">{_e(verdict["root_cause"])}</p>'
            f'<dl class="kv"><dt>Investigator</dt><dd><code>{_e(verdict["agent"])}</code></dd>'
            f'<dt>Confidence</dt><dd class="num">{_e(f"{verdict["confidence"]:.2f}")}</dd>'
            f'<dt>Citations</dt><dd class="num">{len(verdict.get("citations", []))}</dd></dl></div>'
            "<h2>Evidence cited</h2><div class='card'>"
            + _evidence(detail["observations"])
            + "</div>"
            "<h2>Proposed correction</h2><div class='card'>"
            + _proposal(document.get("proposal"))
            + "</div>"
        )

    return shell(
        f"{case_id} — NAV Sentinel",
        f"<h1>{_e(describe(document))}</h1>"
        f'<p class="lede">{_e(document.get("note"))}</p>'
        f'<div class="grid"><div>{left}</div>'
        f'<div class="stick">{_actions(document, principal, band, signed, bool(verdict))}</div>'
        f"</div>",
        principal=principal,
        active="queue",
    )


def _actions(
    document: dict[str, Any],
    principal: Principal,
    band: str,
    signed: list[str],
    worked: bool,
) -> str:
    """The panel where the analyst acts, and where the system says no."""
    from nav_sentinel.control_plane.governance import ApprovalClass

    case_id = str(document.get("case_id", ""))
    outcome = document.get("last_outcome") or {}
    blocks = ""

    if not worked:
        blocks += (
            '<div class="card pad"><b>Investigate</b>'
            '<p class="muted" style="font-size:13px;margin:6px 0 12px">Triage classifies the '
            "difference, the registry decides which agent is authorised for it, and that agent "
            "investigates using only the tools its manifest allows. This calls models.</p>"
            f'<form method="post" action="/app/case/{_e(case_id)}/work">'
            '<button class="btn" type="submit">Run the fleet</button></form></div>'
        )
        return blocks

    eligible, why = session.may_sign(principal, ApprovalClass(band))
    already = principal.subject in signed
    approved = bool(document.get("approval_ref"))

    blocks += '<div class="card pad"><b>Approval</b>'
    blocks += f'<p class="muted" style="font-size:13px;margin:6px 0 10px">{_e(why)}.</p>'
    if signed:
        blocks += (
            '<div class="mono muted" style="font-size:12px;margin-bottom:10px">signed: '
            + _e(", ".join(signed))
            + "</div>"
        )
    if approved:
        blocks += (
            f'<p class="note" style="margin:0 0 10px">Approved &mdash; '
            f'<span class="mono">{_e(document.get("approval_ref"))}</span></p>'
        )
    elif not eligible:
        blocks += f'<p class="note deny" style="margin:0 0 10px">{_e(why)}</p>'
    blocks += (
        f'<form method="post" action="/app/case/{_e(case_id)}/approve">'
        f'<button class="btn" type="submit"'
        f'{" disabled" if (already and not approved) or approved else ""}>'
        f"{'Signed' if already and not approved else 'Approve'}</button></form></div>"
    )

    if outcome:
        tone = "" if outcome.get("granted") else " deny"
        blocks += (
            f'<div class="card pad note{tone}" style="margin-top:14px">'
            f'{_e(outcome.get("message"))}</div>'
        )
        if outcome.get("posting_refused"):
            blocks += (
                '<div class="card pad note deny" style="margin-top:14px">'
                "<b>Posting refused</b>"
                f'<p style="margin:6px 0 0">{_e(outcome["posting_refused"])}</p>'
                '<p class="muted" style="font-size:12px;margin:8px 0 0">An approval is necessary '
                "and not sufficient. No agent in this fleet holds posting authority, so the entry "
                "is refused with a valid signature in hand.</p></div>"
            )
    return blocks


def fleet(*, principal: Principal) -> str:
    """Who is published, what each may call, and what nobody is authorised to do."""
    from nav_sentinel.control_plane import packs
    from nav_sentinel.registry import discover

    agents = "".join(
        f"<tr><td class='mono'>{_e(m.ref)}</td><td>{_e(m.display_name)}</td>"
        f"<td><code>{_e(', '.join(m.handles_capabilities) or '—')}</code></td>"
        f"<td><code>{_e(', '.join(m.allowed_tools) or '—')}</code></td>"
        f"<td><code>{_e(', '.join(m.data_scopes.read) or '—')}</code></td>"
        f"<td>{'<span class=\"chip b-cio_escalation\">may draft</span>' if m.authority.may_propose_remediation else '<span class=\"muted\">reports only</span>'}</td></tr>"
        for m in sorted(discover.all_agents(), key=lambda m: m.agent_id)
    )
    coverage = discover.coverage()
    gaps = sum(1 for ref in coverage.values() if ref is None)
    cover = ""
    for capability, ref in sorted(coverage.items()):
        owner = packs.process_of(capability)
        cover += (
            f"<tr><td><code>{_e(capability)}</code></td>"
            f"<td>{_e(owner.name if owner else '—')}</td>"
            f"<td>{f'<span class=\"mono\">{_e(ref)}</span>' if ref else '<span class=\"none\">NONE</span>'}</td></tr>"
        )
    return shell(
        "Fleet — NAV Sentinel",
        "<h1>Fleet</h1>"
        '<p class="lede">Every agent is discovered from the registry by the capability it declares. '
        "No agent is named in application code, so publishing one is a registry change rather than a "
        "deployment.</p>"
        '<div class="card scroll"><table><thead><tr><th>Reference</th><th>Agent</th><th>Handles</th>'
        f"<th>Allowed tools</th><th>Reads</th><th>Authority</th></tr></thead><tbody>{agents}"
        "</tbody></table></div>"
        f'<h2>Coverage</h2><p class="lede">{len(coverage)} declared capabilities across '
        f"{len(packs.registered())} processes. <b>{gaps}</b> are published by nobody &mdash; the "
        "registry refuses to route them rather than choosing whichever agent looks closest.</p>"
        '<div class="card scroll"><table><thead><tr><th>Capability</th><th>Process</th>'
        f"<th>Authorised agent</th></tr></thead><tbody>{cover}</tbody></table></div>",
        principal=principal,
        active="fleet",
    )


def remediation(store: Any, case_id: str, *, principal: Principal) -> str:
    """The multi-week case as a timeline, which is how a case that runs for a month is read."""
    history = store.stages_for(case_id) if case_id else []
    decisions = store.decisions_for(case_id) if case_id else []
    if not history:
        return shell(
            "Remediation — NAV Sentinel",
            "<h1>Remediation</h1>"
            f'<p class="lede">No remediation case recorded under '
            f'<span class="mono">{_e(case_id)}</span>. Run <code>make remediation</code> against '
            "the same store to walk one.</p>",
            principal=principal,
            active="remediation",
        )

    steps = ""
    for entry in history:
        steps += (
            f'<div class="step"><div class="when">{_e(entry.get("occurred_on") or "—")}</div>'
            f'<div class="dot"></div><div><b>{_e(entry.get("to"))}</b>'
            f'<div class="muted" style="font-size:12.5px">{_e(entry.get("note") or "")}</div>'
            f'<div class="mono muted" style="font-size:11px">written '
            f'{_e(str(entry.get("recorded_at"))[:19])}</div></div></div>'
        )
    denials = [d for d in decisions if d.get("nav.policy.effect") == "deny"]
    refusals = "".join(
        f'<div class="step"><div class="when">&mdash;</div><div class="dot deny"></div>'
        f'<div><b>{_e(d.get("nav.policy.id"))}</b>'
        f'<div class="muted" style="font-size:12.5px">{_e(d.get("nav.policy.reason"))}</div></div>'
        f"</div>"
        for d in denials
    )
    dates = [e.get("occurred_on") for e in history if e.get("occurred_on")]
    span = ""
    if len(dates) >= 2:
        from datetime import date as _d

        span = f"{(_d.fromisoformat(dates[-1]) - _d.fromisoformat(dates[0])).days} days"

    return shell(
        "Remediation — NAV Sentinel",
        "<h1>Remediation</h1>"
        f'<p class="lede"><span class="mono">{_e(case_id)}</span> &mdash; '
        f"{len(history)} recorded transitions"
        + (f" over {_e(span)} of business dates" if span else "")
        + f", {len(decisions)} policy decisions, {len(denials)} of them refusals. Each event was "
        "applied in its own invocation and read back from the store, so the sequence survives the "
        "process that recorded it. Wall-clock is compressed; the business dates are not.</p>"
        f'<div class="card pad">{steps}</div>'
        + (f'<h2>Refused</h2><div class="card pad">{refusals}</div>' if refusals else ""),
        principal=principal,
        active="remediation",
    )
