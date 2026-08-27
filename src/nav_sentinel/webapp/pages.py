"""The pages an analyst works in.

Server-rendered HTML, no build step and no client framework. That is a constraint worth stating
rather than apologising for: one GET returns a page, one POST does one thing and redirects, and
there is no state in a browser that can disagree with the state in Firestore.

**The visual language is an operations console, not a website.** Fund accounting is read at 6am
against a cut-off, so the design is dense, tabular and monospaced wherever a number appears: figures
align on the decimal, severity is a colour *and* a position, and the chrome stays out of the way.
Deep navy with a single gold accent, IBM Plex Sans and JetBrains Mono.

**One palette, not a light/dark pair.** The desk is recorded once, and a viewer whose operating
system prefers light should not be shown a different product than the video shows. Gold is reserved
for labels, figures and the one action a screen wants you to take -- making body links gold too
turned a table of ordinary cases into a table of warnings, which is the whole argument for keeping
an accent scarce.

Band colour is semantic and deliberately separate from the accent -- cleared, one signature, four
eyes, escalation -- because an operator scanning a queue reads colour before text.

Everything interpolated is escaped. Verdict prose and observation summaries on the corporate-action
path derive from SEC filings, which this system deliberately ingests: Model Armor screens them
inbound, and escaping is what stops a screened payload leaving as markup.
"""

from __future__ import annotations

import os
from html import escape
from typing import Any

from nav_sentinel.control_plane.approvals import Principal
from nav_sentinel.webapp import identity, session

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    "family=IBM+Plex+Sans:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600"
    "&display=swap\">"
)

#: The product mark. Inline rather than a file: one request, no build step, and it inherits colour.
MARK = (
    '<svg class="mark" viewBox="0 0 24 24" fill="none" aria-hidden="true">'
    '<path d="M12 2.2 4.3 5.4v6.1c0 4.7 3.2 8.4 7.7 10.3 4.5-1.9 7.7-5.6 7.7-10.3V5.4L12 2.2Z" '
    'stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>'
    '<path d="M8.4 12.1l2.5 2.5 4.7-5" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round"/></svg>'
)

CSS = """
/* Deep navy and gold. One committed palette rather than a light/dark pair: the desk is recorded
   once, and a judge whose operating system prefers light should not get a different product than
   the one in the video. Contrast is checked against the panel surface, not against black. */
:root{
  --bg:#101B30; --paper:#14213B; --raise:#1A2B48; --sunk:#0C1526;
  --chrome:#0C1526; --chrome-ink:#EEF3FA; --chrome-soft:#8697B2;
  --ink:#EEF3FA; --soft:#A9B7CD; --faint:#7E8DA6;
  --line:#26406B; --hair:#1E3054;
  --accent:#C9A227; --accent-2:#DFB63F; --accent-ink:#0C1526;
  --accent-wash:rgba(201,162,39,.10);
  --cleared:#7ED491; --single:#6FA8DC; --four:#C9A227; --escalate:#FF6B6B; --live:#3FBF8F;
  --cleared-w:rgba(126,212,145,.10); --single-w:rgba(111,168,220,.10);
  --four-w:rgba(201,162,39,.10); --escalate-w:rgba(255,107,107,.10);
  --shadow:0 1px 2px rgba(0,0,0,.35);
  --shadow-2:0 8px 28px rgba(0,0,0,.45);
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:"IBM Plex Sans","Segoe UI",-apple-system,Helvetica,Arial,sans-serif;
  font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}
a{color:var(--ink);text-decoration-color:var(--line);
  text-underline-offset:3px}
a:hover{color:var(--accent-2)}
.mono,code,.num{font-family:"JetBrains Mono",ui-monospace,Menlo,Consolas,monospace}
.num{font-variant-numeric:tabular-nums;white-space:nowrap}
code{font-size:12px;background:var(--accent-wash);color:var(--accent-2);
  padding:1.5px 6px;border-radius:4px;border:1px solid rgba(201,162,39,.22)}

/* ---- chrome ------------------------------------------------------------------------------- */
.topbar{background:var(--chrome);color:var(--chrome-ink);position:sticky;top:0;z-index:20;
  border-bottom:1px solid var(--line)}
.topbar-in{max-width:1360px;margin:0 auto;padding:0 24px;display:flex;align-items:center;
  gap:8px;height:54px}
.brand{display:flex;align-items:center;gap:9px;font-weight:700;font-size:15px;
  letter-spacing:-.01em;white-space:nowrap;color:var(--chrome-ink);text-decoration:none}
.mark{width:21px;height:21px;color:var(--accent);flex:none}
.brand em{font-style:normal;color:var(--chrome-soft);font-weight:500;font-size:12.5px;
  padding-left:9px;margin-left:2px;border-left:1px solid rgba(255,255,255,.14)}
.tabs{display:flex;gap:2px;margin-left:26px}
.tabs a{color:var(--chrome-soft);text-decoration:none;font-size:13px;font-weight:500;
  padding:7px 12px;border-radius:6px;white-space:nowrap}
.tabs a:hover{color:var(--chrome-ink);background:rgba(255,255,255,.07)}
.tabs a.on{color:var(--accent);background:var(--accent-wash);font-weight:600}
.topright{margin-left:auto;display:flex;align-items:center;gap:14px}
.env{display:flex;align-items:center;gap:6px;font-size:11px;font-weight:600;letter-spacing:.08em;
  font-family:"JetBrains Mono",monospace;
  text-transform:uppercase;color:var(--chrome-soft);border:1px solid var(--line);
  border-radius:20px;padding:4px 11px;white-space:nowrap}
.env i{width:6px;height:6px;border-radius:50%;background:var(--live);display:block;flex:none;
  box-shadow:0 0 0 3px rgba(63,191,143,.18)}
.user{display:flex;align-items:center;gap:9px}
.avatar{width:29px;height:29px;border-radius:50%;
  background:linear-gradient(140deg,var(--accent-2),var(--accent));
  color:var(--accent-ink);font-size:11px;font-weight:700;letter-spacing:.02em;
  display:flex;align-items:center;
  justify-content:center;flex:none}
.user-t{line-height:1.25;font-size:12px;white-space:nowrap}
.user-t b{display:block;color:var(--chrome-ink);font-weight:600;font-size:12.5px}
.user-t span{color:var(--chrome-soft);text-transform:uppercase;letter-spacing:.05em;font-size:10px;
  font-weight:600}
.linkbtn{background:none;border:1px solid var(--line);color:var(--chrome-soft);
  font:inherit;font-size:12px;padding:5px 10px;border-radius:6px;cursor:pointer}
.linkbtn:hover{color:var(--ink);border-color:var(--accent)}

.subbar{background:var(--paper);border-bottom:1px solid var(--line);position:sticky;top:54px;
  z-index:15}
.subbar-in{max-width:1360px;margin:0 auto;padding:0 24px;height:42px;display:flex;
  align-items:center;gap:10px;font-size:12.5px;color:var(--soft);overflow-x:auto}
.subbar b{color:var(--ink);font-weight:600}
.dot-sep{color:var(--line)}
.facts{margin-left:auto;display:flex;gap:16px;font-size:11.5px;color:var(--faint);white-space:nowrap}
.facts b{font-weight:600;color:var(--soft)}

main{max-width:1360px;margin:0 auto;padding:24px 24px 80px}

/* ---- page head ---------------------------------------------------------------------------- */
.head{display:flex;align-items:flex-start;gap:20px;margin:4px 0 20px}
.head-t{min-width:0}
h1{font-size:25px;margin:0 0 6px;letter-spacing:-.02em;font-weight:600;text-wrap:balance;
  color:#fff}
p.lede{color:var(--soft);margin:0;max-width:78ch;font-size:13.5px}
.head-a{margin-left:auto;display:flex;gap:9px;flex:none}
h2{font-size:11px;text-transform:uppercase;letter-spacing:.14em;color:var(--accent);
  margin:30px 0 12px;font-weight:600;font-family:"JetBrains Mono",monospace;
  display:flex;align-items:center;gap:9px}
h2:before{content:"";width:3px;height:13px;background:var(--accent);flex:none;border-radius:1px}
.back{display:inline-flex;align-items:center;gap:5px;font-size:12.5px;color:var(--faint);
  text-decoration:none;margin-bottom:10px}
.back:hover{color:var(--accent)}

/* ---- tiles -------------------------------------------------------------------------------- */
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:22px}
@media (max-width:900px){.kpis{grid-template-columns:repeat(2,1fr)}}
.tile{background:var(--paper);border:1px solid var(--line);border-radius:9px;padding:15px 17px;
  box-shadow:var(--shadow);position:relative;overflow:hidden}
.tile:before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--accent)}
.tile.t-four:before{background:var(--four)} .tile.t-esc:before{background:var(--escalate)}
.tile.t-ok:before{background:var(--cleared)}
.tile .lbl{font-size:10px;text-transform:uppercase;letter-spacing:.13em;color:var(--faint);
  font-weight:500;margin-bottom:8px;font-family:"JetBrains Mono",monospace}
.tile .big{font-family:"JetBrains Mono",monospace;font-size:27px;font-weight:500;
  letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.1;color:#fff}
.tile .sub{font-size:11.5px;color:var(--faint);margin-top:5px}

/* ---- panels ------------------------------------------------------------------------------- */
.panel{background:var(--paper);border:1px solid var(--line);border-radius:9px;
  box-shadow:var(--shadow);overflow:hidden;margin-bottom:16px;position:relative}
.panel:before{content:"";position:absolute;inset:0 0 auto;height:1px;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.06),transparent)}
.panel-h{display:flex;align-items:center;gap:10px;padding:11px 16px;border-bottom:1px solid var(--hair);
  background:var(--raise)}
.panel-h b{font-size:12px;font-weight:700;letter-spacing:.02em}
.panel-h .r{margin-left:auto;font-size:11.5px;color:var(--faint)}
.pad{padding:16px}
.grid{display:grid;grid-template-columns:minmax(0,1fr) 372px;gap:20px;align-items:start}
@media (max-width:1080px){.grid{grid-template-columns:1fr}}
.stick{position:sticky;top:112px}

/* ---- tables ------------------------------------------------------------------------------- */
.scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.12em;color:var(--faint);
  font-weight:500;font-family:"JetBrains Mono",monospace;padding:10px 14px;background:var(--raise);
  border-bottom:1px solid var(--line);
  white-space:nowrap}
td{padding:11px 14px;border-bottom:1px solid var(--hair);vertical-align:middle;font-size:13px}
tbody tr:last-child td{border-bottom:0}
tr.click{cursor:pointer}
tr.click:hover td{background:var(--raise)}
td.rail{box-shadow:inset 3px 0 0 var(--line)}
td.r-auto_clear{box-shadow:inset 3px 0 0 var(--cleared)}
td.r-single_reviewer{box-shadow:inset 3px 0 0 var(--single)}
td.r-four_eyes{box-shadow:inset 3px 0 0 var(--four)}
td.r-cio_escalation{box-shadow:inset 3px 0 0 var(--escalate)}
th.r,td.r{text-align:right}
.sub2{font-size:11px;color:var(--faint);margin-top:2px}

/* ---- chips & pills ------------------------------------------------------------------------ */
.chip{display:inline-block;padding:2.5px 9px;border-radius:5px;font-size:10.5px;font-weight:700;
  letter-spacing:.03em;text-transform:uppercase;white-space:nowrap;border:1px solid transparent}
.b-auto_clear{color:var(--cleared);background:var(--cleared-w);border-color:var(--cleared)}
.b-single_reviewer{color:var(--single);background:var(--single-w);border-color:var(--single)}
.b-four_eyes{color:var(--four);background:var(--four-w);border-color:var(--four)}
.b-cio_escalation{color:var(--escalate);background:var(--escalate-w);border-color:var(--escalate)}
.pill{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;font-weight:600;
  color:var(--soft);white-space:nowrap}
.pill i{width:7px;height:7px;border-radius:50%;background:var(--faint);flex:none}
.pill.ok i{background:var(--cleared)} .pill.go i{background:var(--accent)}
.pill.ok{color:var(--cleared)} .pill.go{color:var(--accent)}
.none{color:var(--escalate);font-weight:700;font-size:11px;letter-spacing:.06em}
.muted{color:var(--faint)}

/* ---- buttons ------------------------------------------------------------------------------ */
.btn{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--accent);
  background:var(--accent);color:var(--accent-ink);padding:8px 15px;border-radius:6px;font:inherit;
  font-size:13px;font-weight:600;cursor:pointer;text-decoration:none;white-space:nowrap;
  box-shadow:var(--shadow)}
.btn:hover{background:var(--accent-2);border-color:var(--accent-2)}
.btn.ghost{background:transparent;color:var(--accent);border-color:var(--line)}
.btn.ghost:hover{background:var(--accent-wash);border-color:var(--accent)}
.btn.wide{width:100%;justify-content:center}
.btn:disabled{cursor:not-allowed;box-shadow:none;background:var(--bg);color:var(--faint);
  border-color:var(--line);border-style:dashed;font-weight:600}
.btn:focus-visible,a:focus-visible{outline:2px solid var(--accent-2);outline-offset:2px}
form{display:inline}
form.block{display:block}

/* ---- notes -------------------------------------------------------------------------------- */
.note{border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:7px;
  padding:11px 14px;font-size:12.5px;color:var(--soft);background:var(--sunk)}
.note b{color:var(--ink)}
.note.deny{background:var(--escalate-w);border-color:rgba(255,107,107,.28);
  border-left-color:var(--escalate);color:var(--escalate)}
.note.deny b{color:var(--escalate)}
.note.ok{background:var(--cleared-w);border-color:rgba(126,212,145,.28);
  border-left-color:var(--cleared);color:var(--cleared)}
.note.ok b{color:var(--cleared)}

/* ---- key/value ---------------------------------------------------------------------------- */
.kv{display:grid;grid-template-columns:132px minmax(0,1fr);gap:0;font-size:13px;margin:0}
.kv dt{color:var(--faint);font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;
  font-weight:700;padding:8px 0;border-bottom:1px solid var(--hair)}
.kv dd{margin:0;padding:8px 0;border-bottom:1px solid var(--hair);min-width:0;
  overflow-wrap:anywhere}
.kv dt:last-of-type,.kv dd:last-of-type{border-bottom:0}
ul.plain{margin:0;padding-left:17px;color:var(--soft);font-size:13px}
ul.plain li{margin:3px 0}

/* ---- timeline ----------------------------------------------------------------------------- */
.steps{position:relative;padding-left:6px}
.steps.compact .step{grid-template-columns:22px minmax(0,1fr)}
.step{display:grid;grid-template-columns:92px 22px minmax(0,1fr);gap:0;padding:0 0 18px;
  position:relative}
.step:last-child{padding-bottom:0}
.step .when{font-family:"JetBrains Mono",monospace;font-size:11.5px;color:var(--faint);
  padding-top:1px;white-space:nowrap}
.step .rail-c{position:relative;display:flex;justify-content:center}
.step .dot{width:10px;height:10px;border-radius:50%;background:var(--accent);margin-top:4px;
  flex:none;box-shadow:0 0 0 3px var(--accent-wash);z-index:1}
.step .dot.deny{background:var(--escalate);box-shadow:0 0 0 3px var(--escalate-w)}
.step:not(:last-child) .rail-c:after{content:"";position:absolute;top:16px;bottom:-18px;
  width:2px;background:var(--hair)}
.step .body b{font-size:13px;font-weight:600}
.step .body .d{font-size:12.5px;color:var(--soft);margin-top:2px}
.step .body .t{font-family:"JetBrains Mono",monospace;font-size:10.5px;color:var(--faint);
  margin-top:3px}

/* ---- signatures --------------------------------------------------------------------------- */
.sig{display:flex;align-items:center;gap:9px;padding:8px 0;border-bottom:1px solid var(--hair)}
.sig:last-child{border-bottom:0}
.sig .avatar{width:26px;height:26px;font-size:10px}
.sig .avatar.wait{background:transparent;color:var(--faint);border:1px dashed var(--line)}
.sig .n{font-size:12.5px;min-width:0;overflow-wrap:anywhere}
.sig .n span{display:block;font-size:10.5px;color:var(--faint);text-transform:uppercase;
  letter-spacing:.05em;font-weight:700}
.meter{height:4px;border-radius:3px;background:var(--hair);overflow:hidden;margin:2px 0 12px}
.meter i{display:block;height:100%;background:var(--accent);border-radius:3px}

/* ---- agent cards -------------------------------------------------------------------------- */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(324px,1fr));gap:14px}
.acard{background:var(--paper);border:1px solid var(--line);border-radius:9px;padding:15px 16px;
  box-shadow:var(--shadow)}
.acard h3{margin:0;font-size:13.5px;font-weight:700;letter-spacing:-.01em}
.acard .ref{font-family:"JetBrains Mono",monospace;font-size:10.5px;color:var(--faint);
  margin-top:2px;overflow-wrap:anywhere}
.acard .row{display:flex;align-items:flex-start;gap:8px;margin-top:11px;font-size:11.5px}
.acard .row .k{color:var(--faint);text-transform:uppercase;letter-spacing:.06em;font-weight:700;
  font-size:9.5px;width:58px;flex:none;padding-top:3px}
.acard .row .v{min-width:0;overflow-wrap:anywhere;color:var(--soft)}
.tag{display:inline-block;font-family:"JetBrains Mono",monospace;font-size:10.5px;
  background:var(--bg);border:1px solid var(--hair);border-radius:4px;padding:1.5px 6px;
  margin:0 3px 3px 0;color:var(--soft)}

/* ---- progress ----------------------------------------------------------------------------- */
.pstep{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--hair);
  font-size:12.5px;color:var(--faint)}
.pstep:last-of-type{border-bottom:0}
.pdot{width:14px;height:14px;border-radius:50%;border:2px solid var(--line);flex:none;
  position:relative}
.plabel{min-width:0}
.pnote{margin-left:auto;font-family:"JetBrains Mono",monospace;font-size:10.5px;color:var(--faint);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:46%}
.pstep[data-state=running]{color:var(--ink);font-weight:600}
.pstep[data-state=running] .pdot{border-color:var(--accent);border-right-color:transparent;
  animation:spin .8s linear infinite}
.pstep[data-state=done]{color:var(--soft)}
.pstep[data-state=done] .pdot{border-color:var(--cleared);background:var(--cleared)}
.pstep[data-state=done] .pdot:after{content:"";position:absolute;left:3px;top:1px;width:4px;
  height:7px;border:solid #fff;border-width:0 2px 2px 0;transform:rotate(40deg)}
.pstep[data-state=refused]{color:var(--escalate);font-weight:600}
.pstep[data-state=refused] .pdot{border-color:var(--escalate);background:var(--escalate)}
.pstep[data-state=skipped]{color:var(--faint);text-decoration:line-through}
@keyframes spin{to{transform:rotate(360deg)}}
.bar{height:4px;border-radius:3px;background:var(--hair);overflow:hidden;margin-top:12px}
.bar i{display:block;height:100%;width:30%;border-radius:3px;background:var(--accent);
  animation:slide 1.5s ease-in-out infinite}
@keyframes slide{0%{margin-left:-30%}100%{margin-left:100%}}
#work-progress.finished .bar{display:none}
.reveal{animation:reveal .45s ease-out}
.bump{animation:bump .5s ease-out}
@keyframes bump{0%{transform:scale(1)}35%{transform:scale(1.16);color:var(--accent)}100%{transform:scale(1)}}
@keyframes reveal{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}

/* ---- live ops ----------------------------------------------------------------------------- */
.lgrid{width:100%;border-collapse:collapse}
.lgrid th{font-size:9.5px}
.lgrid td{padding:9px 12px;font-size:12.5px}
.scell{text-align:center;width:74px}
.sdot{display:inline-block;width:13px;height:13px;border-radius:50%;border:2px solid var(--line);
  position:relative;vertical-align:middle}
.sdot[data-s=done]{border-color:var(--cleared);background:var(--cleared)}
.sdot[data-s=done]:after{content:"";position:absolute;left:2.5px;top:1px;width:3.5px;height:6px;
  border:solid #fff;border-width:0 2px 2px 0;transform:rotate(40deg)}
.sdot[data-s=refused]{border-color:var(--escalate);background:var(--escalate)}
.sdot[data-s=refused]:after{content:"";position:absolute;left:3.5px;top:2.5px;width:5px;height:5px;
  border-top:2px solid #fff;transform:rotate(45deg)}
.sdot[data-s=blocked]{border-style:dotted;border-color:var(--line);background:transparent}
.handover{display:flex;align-items:center;gap:14px;padding:14px 17px;border-radius:9px;
  border:1px solid var(--line);border-left:3px solid var(--accent);background:var(--paper);
  margin-bottom:16px;font-size:13px;box-shadow:var(--shadow)}
.handover.done{border-left-color:var(--cleared)}
.handover b{color:var(--ink)}
.handover .hs{margin-left:auto;display:flex;gap:18px;white-space:nowrap;font-size:12px}
.handover .hs span{color:var(--soft)}
.handover .hs b{font-family:"JetBrains Mono",monospace;font-size:15px;color:var(--accent)}
.nx{font-size:11.5px;white-space:nowrap}
.nx[data-k=sign]{color:var(--four);font-weight:600}
.nx[data-k=human_investigation]{color:var(--escalate);font-weight:600}
.nx[data-k=fleet]{color:var(--faint)}
.nx[data-k=not_started]{color:var(--faint)}
.nx[data-k=posted_by_ledger]{color:var(--cleared);font-weight:600}
.feed{max-height:300px;overflow-y:auto;font-family:"JetBrains Mono",monospace;font-size:11.5px}
.frow{display:grid;grid-template-columns:64px 54px 218px minmax(0,1fr);gap:12px;padding:5px 14px;
  border-bottom:1px solid var(--hair);white-space:nowrap;align-items:baseline}
.frow:last-child{border-bottom:0}
.frow .fx{font-weight:700;letter-spacing:.04em}
.frow[data-e=allow] .fx{color:var(--cleared)}
.frow[data-e=deny] .fx{color:var(--escalate)}
.frow .fr{overflow:hidden;text-overflow:ellipsis;color:var(--soft)}
.frow .fa{color:var(--faint)}
.pulse{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--live);
  animation:beat 1.4s ease-in-out infinite;vertical-align:middle;margin-right:6px}
@keyframes beat{0%,100%{opacity:1}50%{opacity:.25}}

/* ---- sign in ------------------------------------------------------------------------------ */
.auth{min-height:100vh;display:grid;grid-template-columns:1.05fr .95fr}
@media (max-width:940px){.auth{grid-template-columns:1fr}.auth-l{display:none}}
.auth-l{background:var(--chrome);color:var(--chrome-ink);padding:46px 52px;display:flex;
  flex-direction:column;position:relative;overflow:hidden;border-right:1px solid var(--line)}
.auth-l:before{content:"";position:absolute;inset:0;pointer-events:none;
  background:radial-gradient(720px 460px at 86% -10%, rgba(201,162,39,.13), transparent 64%),
             radial-gradient(560px 420px at -12% 106%, rgba(111,168,220,.10), transparent 62%)}
.auth-l:after{content:"";position:absolute;inset:0;pointer-events:none;opacity:.45;
  background-image:linear-gradient(rgba(255,255,255,.032) 1px,transparent 1px),
                   linear-gradient(90deg,rgba(255,255,255,.032) 1px,transparent 1px);
  background-size:34px 34px;
  -webkit-mask-image:radial-gradient(80% 70% at 50% 40%,#000,transparent);
  mask-image:radial-gradient(80% 70% at 50% 40%,#000,transparent)}
.auth-l > *{position:relative;z-index:1}
.auth-l .brand{font-size:16px}
.auth-story{margin:auto 0;padding:32px 0}
.auth-l h2{color:#fff;font-size:31px;line-height:1.2;letter-spacing:-.025em;text-transform:none;
  margin:0 0 16px;font-weight:300;max-width:20ch;font-family:"IBM Plex Sans",sans-serif;
  display:block}
.auth-l h2:before{display:none}
.auth-l p{color:var(--chrome-soft);font-size:13.5px;max-width:47ch;margin:0;line-height:1.6}
.proof{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;
  border-top:1px solid rgba(255,255,255,.1);padding-top:20px}
.proof div .n{font-family:"JetBrains Mono",monospace;font-size:23px;font-weight:400;
  color:var(--accent);letter-spacing:-.02em}
.proof div .l{font-size:10px;color:var(--chrome-soft);text-transform:uppercase;
  letter-spacing:.13em;font-weight:500;margin-top:5px;line-height:1.4;
  font-family:"JetBrains Mono",monospace}
.auth-r{background:var(--bg);display:flex;align-items:center;justify-content:center;
  padding:46px 32px}
.auth-box{width:100%;max-width:388px}
.auth-box .eyebrow{font-size:10.5px;text-transform:uppercase;letter-spacing:.16em;
  color:var(--accent);font-weight:500;font-family:"JetBrains Mono",monospace}
.auth-box h1{font-size:24px;margin:7px 0 6px}
.auth-box .sub{color:var(--soft);font-size:13.5px;margin:0 0 26px}
.auth-notice{border:1px solid var(--line);border-left:3px solid var(--escalate);
  background:var(--escalate-w);color:var(--escalate);border-radius:7px;padding:11px 14px;
  font-size:12.5px;line-height:1.55;margin:0 0 22px}
.gbtn{display:flex;justify-content:center;min-height:44px}
.auth-foot{margin-top:26px;padding-top:18px;border-top:1px solid var(--hair);
  font-size:11.5px;color:var(--faint);line-height:1.6}
.auth-meta{display:flex;gap:14px;flex-wrap:wrap;margin-top:12px;font-size:10.5px;
  font-family:"JetBrains Mono",monospace;color:var(--faint)}
.auth-meta span{display:inline-flex;align-items:center;gap:5px}
.auth-meta i{width:5px;height:5px;border-radius:50%;background:var(--live);display:block}
.empty{text-align:center;padding:48px 20px}
.empty .mark{width:34px;height:34px;color:var(--line);margin-bottom:12px}
"""


#: Streams the investigation into the page. Progressive enhancement: the plain form POST below is
#: what runs without JavaScript, and it is the path the tests drive -- so the fallback cannot rot
#: into something that only works because nobody uses it.
#:
#: `fetch` with a POST rather than `EventSource`, which can only issue a GET. A GET that spends
#: money on model calls is one a link preview or a prefetch can trigger, and this endpoint bills
#: Vertex AI per click.
_WORK_SCRIPT = r"""<script>
(function(){
  var form = document.getElementById('work-form');
  if (!form || !window.fetch) return;                 // no JS, or no fetch: the form POST stands
  form.dataset.enhanced = '1';
  form.addEventListener('submit', function(ev){
    ev.preventDefault();
    var btn = form.querySelector('button');
    btn.disabled = true;                              // one click is one investigation
    var rail = document.getElementById('case-rail');
    var host = document.getElementById('case-sections');
    rail.innerHTML = form.dataset.progress;
    var steps = {};
    rail.querySelectorAll('.pstep').forEach(function(el){ steps[el.dataset.stage] = el; });
    // Spin the first step immediately rather than waiting for the server to say so. On a cold
    // instance the first line can be ten seconds out, and four pending rows with nothing moving
    // is the frozen screen this replaced.
    var first = rail.querySelector('.pstep');
    if (first) first.dataset.state = 'running';

    function mark(stage, state, note){
      var el = steps[stage];
      if (!el) return;
      el.dataset.state = state;
      if (note) el.querySelector('.pnote').textContent = note;
    }

    fetch(form.dataset.stream, {method:'POST', headers:{'Accept':'application/x-ndjson'}})
      .then(function(res){
        var reader = res.body.getReader(), dec = new TextDecoder(), buf = '';
        function pump(){
          return reader.read().then(function(r){
            if (r.done) return;
            buf += dec.decode(r.value, {stream:true});
            var lines = buf.split('\n');
            buf = lines.pop();
            lines.forEach(function(line){
              if (!line) return;
              var ev = JSON.parse(line);
              if (ev.stage) mark(ev.stage, ev.state, ev.detail);
              if (ev.html) {
                var box = document.createElement('div');
                box.className = 'reveal';
                box.innerHTML = ev.html;
                host.appendChild(box);
              }
              if (ev.state === 'finished') {
                document.getElementById('work-progress').classList.add('finished');
                document.getElementById('work-status').textContent = 'complete';
                rail.innerHTML = ev.rail;
              }
              if (ev.state === 'failed') {
                document.getElementById('work-status').textContent = 'failed';
                mark(ev.stage || 'triage', 'refused', ev.detail || 'failed');
              }
            });
            return pump();
          });
        }
        return pump();
      })
      .catch(function(){ location.reload(); });       // whatever happened, the store is the truth
  });
})();
</script>"""


#: Polls the snapshot and repaints. Polling rather than a stream because with events fanned out
#: across instances the browser is not connected to whichever worker is doing the work -- Firestore
#: is the only thing both can see, and reading it is what an auditor would do.
#:
#: It stops when every case is terminal and says so. A page that keeps asking the same question
#: every second forever is a cost with no answer attached.
_LIVE_SCRIPT = r"""<script>
(function(){
  var rows = document.getElementById('live-rows');
  if (!rows || !window.fetch) return;
  var stopped = false;

  function paintCounter(key, value){
    var el = document.querySelector('[data-counter="' + key + '"]');
    if (!el) return;
    var suffix = el.querySelector('span');
    var shown = suffix ? el.firstChild : el;
    if (value === null || value === undefined) return;   // absent, not zero: leave the dash
    if (String(shown.textContent).trim() === String(value)) return;
    shown.textContent = value;
    el.classList.remove('bump'); void el.offsetWidth; el.classList.add('bump');
  }

  function paint(snap){
    Object.keys(snap.counters).forEach(function(k){ paintCounter(k, snap.counters[k]); });

    paintCounter('hand_sign', snap.handover.sign);
    paintCounter('hand_manual', snap.handover.human_investigation);
    paintCounter('hand_working', snap.handover.fleet);

    snap.cases.forEach(function(c){
      var tr = rows.querySelector('[data-case="' + c.case_id + '"]');
      if (!tr) return;
      var dots = tr.querySelectorAll('.sdot');
      snap.stages.forEach(function(s, i){
        if (dots[i] && dots[i].dataset.s !== c.stages[s.key]) dots[i].dataset.s = c.stages[s.key];
      });
      var agent = tr.children[2 + snap.stages.length];
      if (agent && c.agent && agent.textContent.trim() !== c.agent) {
        agent.innerHTML = '<span class="mono" style="font-size:11px">' + c.agent + '</span>';
      }
      var nx = tr.querySelector('.nx');
      if (nx && nx.textContent !== c.next_step) {
        nx.textContent = c.next_step; nx.dataset.k = c.next_kind;
      }
    });

    var feed = document.getElementById('live-feed');
    if (snap.feed.length) {
      feed.innerHTML = snap.feed.map(function(l){
        return '<div class="frow" data-e="' + l.effect + '">'
          + '<span class="fa">' + l.at + '</span>'
          + '<span class="fx">' + l.effect.toUpperCase() + '</span>'
          + '<span>' + l.policy + '</span>'
          + '<span class="fr">' + l.reason + (l.agent ? '  &middot;  ' + l.agent : '') + '</span>'
          + '</div>';
      }).join('');
    }

    if (snap.settled && !stopped) {
      stopped = true;
      document.getElementById('live-status').innerHTML =
'complete &middot; awaiting sign-off';
    }
  }

  function tick(){
    // No window passed from here. The server reads it off the cases, because the browser is
    // not the authority on when a run started and may not have existed when it did.
    fetch('/app/live.json')
      .then(function(r){ return r.json(); })
      .then(function(snap){
        paint(snap);
        if (!stopped) setTimeout(tick, 1200);
      })
      .catch(function(){ if (!stopped) setTimeout(tick, 3000); });
  }
  setTimeout(tick, 1200);
})();
</script>"""


def _e(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


#: How a role is written on screen. `cio` is an initialism and reads wrong in sentence case, and
#: the raw enum values leak the code's vocabulary into an operations screen.
ROLE_LABELS = {"cio": "CIO", "controller": "Controller", "reviewer": "Reviewer"}


def role_label(roles) -> str:
    """"CIO", or "Controller or CIO" — the roles that may sign, as an operator would say them."""
    names = [ROLE_LABELS.get(r, r.capitalize()) for r in sorted(roles)]
    if len(names) <= 1:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + f" or {names[-1]}"


#: Approval bands as an operator says them. The enum spells them for code.
BAND_LABELS = {
    "auto_clear": "Auto-clear",
    "single_reviewer": "Single reviewer",
    "four_eyes": "Four eyes",
    "cio_escalation": "CIO escalation",
}


def band_label(band: str) -> str:
    return BAND_LABELS.get(band, band.replace("_", " ").capitalize())


def _initials(subject: str) -> str:
    """Two letters for an avatar. From the local part, so `j.laurent@x.com` reads `JL`."""
    local = subject.split("@", maxsplit=1)[0]
    parts = [p for p in local.replace("_", ".").replace("-", ".").split(".") if p]
    if len(parts) >= 2:
        return (parts[0][:1] + parts[1][:1]).upper()
    return local[:2].upper() or "??"


def _environment() -> tuple[str, str, str]:
    """Region, store and identity mode, for the chrome.

    Read from the environment rather than passed down through every call: this is a property of the
    deployment, not of a page, and a console that does not say which environment it is showing you
    is the kind of thing that gets a correction booked in the wrong place.
    """
    region = os.environ.get("NAV_REGION", "") or "local"
    store = "Firestore" if os.environ.get("NAV_REPOSITORY") == "firestore" else "in-memory"
    mode = "Google SSO" if identity.uses_google() else "local roster"
    return region, store, mode


#: Break types in an operator's words. `nav.unclassified` is an internal enum meaning triage has not
#: run; showing it as a page title made every exception look identical and named none of them.
BREAK_TITLES = {
    "cash_balance": "Cash balance difference",
    "position_quantity": "Position quantity difference",
    "market_value": "Market value difference",
    "nav_per_share": "NAV per share difference",
}


def describe(document: dict[str, Any]) -> str:
    """What this exception is, for a human. Never an enum.

    Qualified by whatever identifies it: an instrument for a security break, a currency for a cash
    one. Without the currency the queue showed "Cash balance difference" twice, identically, for two
    different cases -- and a row an analyst cannot tell apart from the one above it is a row they
    have to open to identify.
    """
    types = [t for t in document.get("break_types", []) if t]
    titles = [BREAK_TITLES.get(t, t.replace("_", " ").capitalize()) for t in dict.fromkeys(types)]
    head = " and ".join(titles) if titles else "Exception"
    qualifier = document.get("isin") or document.get("currency") or ""
    return f"{head}{f' · {qualifier}' if qualifier else ''}"


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
    links = [
        ("queue", "/app", "Exceptions"),
        ("remediation", "/app/remediation", "Remediation"),
        ("live", "/app/live", "Fleet activity"),
        ("fleet", "/app/fleet", "Fleet"),
        ("audit", "/console", "Audit view"),
    ]
    tabs = "".join(
        f'<a href="{_e(href)}" class="{"on" if key == active else ""}">{_e(label)}</a>'
        for key, href, label in links
    )
    region, store, mode = _environment()
    who = (
        f'<div class="user"><div class="avatar">{_e(_initials(principal.subject))}</div>'
        f'<div class="user-t"><b>{_e(principal.subject)}</b><span>{_e(principal.role)}</span></div>'
        f'<form method="post" action="/app/signout">'
        f'<button class="linkbtn" type="submit">Sign out</button></form></div>'
        if principal
        else ""
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_e(title)}</title>{FONTS}<style>{CSS}</style></head><body>"
        '<header class="topbar"><div class="topbar-in">'
        f'<a class="brand" href="/app">{MARK}NAV Sentinel<em>Exception desk</em></a>'
        f'<nav class="tabs">{tabs}</nav>'
        f'<div class="topright"><span class="env"><i></i>{_e(region)}</span>{who}</div>'
        "</div></header>"
        '<div class="subbar"><div class="subbar-in">'
        "<span>Merian Global Equity Fund</span>"
        '<span class="dot-sep">/</span><span><b>MERID-GEF</b></span>'
        '<span class="dot-sep">/</span><span>Daily NAV reconciliation</span>'
        f'<div class="facts"><span>Store <b>{_e(store)}</b></span>'
        f'<span>Identity <b>{_e(mode)}</b></span>'
        "<span>Gemini <b>Vertex AI</b></span></div>"
        f"</div></div><main>{body}</main></body></html>"
    )


def _head(title: str, lede: str, actions: str = "", back: str = "") -> str:
    """A page header: what this is, what it means, and what you can do about it."""
    crumb = (
        f'<a class="back" href="/app">&larr; {_e(back)}</a>' if back else ""
    )
    return (
        f"{crumb}<div class=\"head\"><div class=\"head-t\"><h1>{title}</h1>"
        f'<p class="lede">{lede}</p></div>'
        + (f'<div class="head-a">{actions}</div>' if actions else "")
        + "</div>"
    )


# ---------------------------------------------------------------------------------------------
# Sign in
# ---------------------------------------------------------------------------------------------


def _auth_page(title: str, right: str) -> str:
    """A standalone sign-in layout. No navigation, because there is nothing yet to navigate."""
    from nav_sentinel.control_plane import packs
    from nav_sentinel.registry import discover

    try:
        agents, capabilities, processes = (
            len(discover.all_agents()),
            len(discover.coverage()),
            len(packs.registered()),
        )
    except Exception:  # noqa: BLE001 -- the sign-in page must render even if the registry does not
        agents = capabilities = processes = 0
    region, store, mode = _environment()
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_e(title)}</title>{FONTS}<style>{CSS}</style></head><body>"
        '<div class="auth"><section class="auth-l">'
        f'<div class="brand">{MARK}NAV Sentinel</div>'
        '<div class="auth-story">'
        "<h2>A governed fleet of fund-accounting agents.</h2>"
        "<p>Differences between the fund's books and the custodian's are found, investigated by "
        "the specialist authorised for that kind of break, and drafted into a correcting entry. "
        "Every step is checked against what that specialist is permitted to do. Nothing reaches a "
        "NAV without a human signature.</p>"
        "</div>"
        '<div class="proof">'
        f'<div><div class="n">{agents}</div><div class="l">Specialists</div></div>'
        f'<div><div class="n">{capabilities}</div><div class="l">Kinds of break</div></div>'
        f'<div><div class="n">{processes}</div><div class="l">Departments</div></div>'
        "</div></section>"
        f'<section class="auth-r"><div class="auth-box">{right}'
        f'<div class="auth-meta"><span><i></i>{_e(region)}</span>'
        f"<span>store: {_e(store)}</span><span>identity: {_e(mode)}</span></div>"
        "</div></section></div></body></html>"
    )


def _notice(text: str) -> str:
    """A refused sign-in, said out loud.

    It used to set no cookie and redirect in silence, on the reasoning that saying *why* would turn
    the sign-in screen into a directory of who can approve this fund's corrections. That reasoning
    holds for the analyst list and not for this: a caller who has just proved they control an
    address learns nothing about anyone else from being told that address is not authorised. What
    the silence actually produced was a page that looked broken -- sign in, land back on sign in,
    with the diagnosis only in a log the person cannot read.
    """
    return f'<div class="auth-notice">{_e(text)}</div>' if text else ""


def signin_google(as_of: str, client: str, *, notice: str = "") -> str:
    """Real Google sign-in. The button is Google's own, and the token it returns is verified
    server-side before a single claim in it is believed."""
    return _auth_page(
        "Sign in — NAV Sentinel",
        '<div class="eyebrow">Exception desk</div>'
        "<h1>Sign in</h1>"
        f'<p class="sub">Valuation point {_e(as_of)}. Authenticate with Google to work the '
        "queue.</p>"
        f"{_notice(notice)}"
        f'<div id="g_id_onload" data-client_id="{_e(client)}" '
        'data-callback="onCredential" data-auto_prompt="false"></div>'
        '<div class="gbtn"><div class="g_id_signin" data-type="standard" data-size="large" '
        'data-text="signin_with" data-shape="rectangular" data-width="330"></div></div>'
        '<form id="credform" method="post" action="/app/auth/google">'
        '<input type="hidden" name="credential" id="credential"></form>'
        "<script>function onCredential(r){"
        "document.getElementById('credential').value=r.credential;"
        "document.getElementById('credform').submit();}</script>"
        '<script src="https://accounts.google.com/gsi/client" async defer></script>'
        '<div class="auth-foot"><b>Signing in proves who you are. It grants nothing.</b> '
        "The role that decides what you may sign comes from this deployment's list of authorised "
        "analysts, and an address that is not on it can sign in and approve nothing.</div>",
    )


def signin(as_of: str, *, notice: str = "") -> str:
    """Choose an analyst. No password, and the page says why."""
    rows = "".join(
        f'<div class="sig"><div class="avatar">{_e(_initials(p.subject))}</div>'
        f'<div class="n">{_e(p.subject)}<span>{_e(p.role)}</span></div>'
        f'<div style="margin-left:auto"><form method="post" action="/app/signin">'
        f'<input type="hidden" name="subject" value="{_e(p.subject)}">'
        f'<button class="btn ghost" type="submit">Sign in</button></form></div></div>'
        for p in session.ROSTER
    )
    return _auth_page(
        "Sign in — NAV Sentinel",
        '<div class="eyebrow">Exception desk</div>'
        "<h1>Sign in</h1>"
        f'<p class="sub">Valuation point {_e(as_of)}. Which role you hold decides what you may '
        "sign: a reviewer cannot approve a four-eyes correction at all, and two different "
        "controllers are needed for one.</p>"
        f"{_notice(notice)}"
        '<div class="note deny" style="margin-bottom:16px"><b>Local mode &mdash; identities are '
        "not verified.</b> No OAuth client is configured, so this deployment falls back to a fixed "
        "roster. The deployed service uses Google sign-in and checks the token before believing "
        "any claim in it.</div>"
        f"{rows}"
        '<div class="auth-foot">There is no password here and nothing is collected. What a '
        "service-to-service token cannot carry is <em>which analyst</em> is acting, and four-eyes "
        "has to count people &mdash; so in this mode the roster is fixed and server-side.</div>",
    )


# ---------------------------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------------------------


def _bps(value: Any) -> float:
    try:
        return abs(float(str(value)))
    except (TypeError, ValueError):
        return 0.0


def queue(items: list[Any], *, principal: Principal, as_of: str) -> str:
    """The exceptions queue: what came out of the valuation point, and who must sign each one."""
    if not items:
        body = _head(
            "Exceptions",
            f"No reconciliation has been run for {_e(as_of)}. The run compares the fund's books "
            "against the custodian's, sizes each difference against NAV, and sets the approval "
            "level each one requires.",
        ) + (
            '<div class="panel"><div class="empty">'
            f"{MARK}"
            '<div style="font-size:15px;font-weight:600;margin-bottom:5px">No exceptions '
            "detected</div>"
            '<p class="muted" style="max-width:52ch;margin:0 auto 18px;font-size:13px">'
            "Differences are found by tolerance rules over both books, not by a model. The "
            "specialists are engaged afterwards, to explain what caused each one.</p>"
            '<form method="post" action="/app/cycle" onsubmit="var b=this.querySelector(\'button\');b.disabled=true;b.textContent=\'Running…\';">'
            '<button class="btn" type="submit">Run reconciliation</button></form>'
            "</div></div>"
        )
        return shell("Exceptions — NAV Sentinel", body, principal=principal, active="queue")

    rows = ""
    for item in items:
        state = (
            '<span class="pill ok"><i></i>Approved</span>'
            if item.approved
            else '<span class="pill go"><i></i>Investigated</span>'
            if item.worked
            else '<span class="pill"><i></i>Not started</span>'
        )
        short = item.case_id.replace("CASE-MERID-GEF-", "")
        rows += (
            f'<tr class="click" onclick="location.href=\'/app/case/{_e(item.case_id)}\'">'
            f'<td class="rail r-{_e(item.band)}">'
            f'<a href="/app/case/{_e(item.case_id)}" style="font-weight:600;text-decoration:none">'
            f"{_e(item.title)}</a>"
            f'<div class="sub2 mono">{_e(short)}</div></td>'
            f"<td>{classification({'capability': item.capability})}</td>"
            f'<td class="r num" style="font-weight:600">'
            f'{_e(item.impact_bps) if item.impact_bps else "<span class=\'muted\'>&mdash;</span>"}'
            "</td>"
            f"<td>{_chip(item.band)}</td>"
            f"<td>{state}</td></tr>"
        )

    worked = sum(1 for i in items if i.worked)
    unworked = len(items) - worked
    approved = sum(1 for i in items if i.approved)
    escalations = sum(1 for i in items if i.band == "cio_escalation")
    total_bps = sum(_bps(i.impact_bps) for i in items)

    tiles = (
        '<div class="kpis">'
        f'<div class="tile"><div class="lbl">Open exceptions</div>'
        f'<div class="big">{len(items)}</div>'
        f'<div class="sub">at the {_e(as_of)} valuation point</div></div>'
        f'<div class="tile t-four"><div class="lbl">Aggregate NAV impact</div>'
        f'<div class="big">{total_bps:.1f}<span style="font-size:13px;color:var(--faint)"> bps'
        "</span></div>"
        '<div class="sub">absolute, across all breaks</div></div>'
        f'<div class="tile t-ok"><div class="lbl">Investigated</div>'
        f'<div class="big">{worked}<span style="font-size:15px;color:var(--faint)">/{len(items)}'
        "</span></div>"
        f'<div class="sub">{approved} signed and approved</div></div>'
        f'<div class="tile t-esc"><div class="lbl">CIO escalations</div>'
        f'<div class="big">{escalations}</div>'
        '<div class="sub">only the CIO may clear these</div></div>'
        "</div>"
    )

    body = (
        _head(
            "Exceptions",
            f"{len(items)} differences between the fund's books and the custodian's at the "
            f"{_e(as_of)} valuation point, {worked} investigated. Impact is stated in basis points "
            "of NAV, and it determines who is required to sign the correction.",
            actions='<form method="post" action="/app/cycle" onsubmit="var b=this.querySelector(\'button\');b.disabled=true;b.textContent=\'Running…\';">'
            '<button class="btn ghost" type="submit">Re-run reconciliation</button></form>'
            + (
                '<form method="post" action="/app/investigate-all" '
                'onsubmit="var b=this.querySelector(\'button\');b.disabled=true;'
                'b.textContent=\'Dispatching…\';">'
                f'<button class="btn" type="submit">Investigate all {unworked}</button></form>'
                if unworked
                else ""
            ),
        )
        + tiles
        + '<div class="panel"><div class="panel-h"><b>Exception queue</b>'
        f'<span class="r">{len(items)} rows &middot; sorted by detection order</span></div>'
        '<div class="scroll"><table><thead><tr><th>Exception</th><th>Classification</th>'
        '<th class="r">Impact (bps)</th><th>Must be signed by</th><th>State</th></tr>'
        f"</thead><tbody>{rows}</tbody></table></div></div>"
    )
    return shell("Exceptions — NAV Sentinel", body, principal=principal, active="queue")


# ---------------------------------------------------------------------------------------------
# One case
# ---------------------------------------------------------------------------------------------


def _evidence(observations: list[Any]) -> str:
    if not observations:
        return '<div class="pad muted">No evidence recorded yet.</div>'
    rows = ""
    for observation in observations:
        facts = "".join(
            f'<div style="margin-bottom:2px"><span class="muted" style="font-size:9.5px;'
            f'text-transform:uppercase;letter-spacing:.06em;font-weight:700">{_e(k)}</span> '
            f'<span class="num" style="font-size:12px">{_e(v)}</span></div>'
            for k, v in sorted(observation.observed.items())
        )
        rows += (
            f'<tr><td class="mono" style="font-size:11px">{_e(observation.observation_id)}</td>'
            f"<td><code>{_e(observation.tool)}</code>"
            f'<div class="sub2">{_e(observation.args)}</div></td>'
            f"<td>{facts}</td>"
            f'<td><code>{_e(observation.source)}</code>'
            f'<div class="sub2 mono">{_e(observation.digest[:16])}</div></td></tr>'
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
        f'<td class="mono" style="font-size:11.5px">{_e(line["currency"])}</td>'
        f'<td class="r num">{_e(line["debit"])}</td>'
        f'<td class="r num">{_e(line["credit"])}</td></tr>'
        for line in proposal.get("lines", [])
    )
    quantities = "".join(
        f'<tr><td><code>{_e(q["account"])}</code></td>'
        f'<td class="mono" style="font-size:11.5px">{_e(q["isin"])}</td>'
        f'<td class="r num">{_e(q["from_quantity"])} &rarr; {_e(q["to_quantity"])}</td></tr>'
        for q in proposal.get("quantity_lines", [])
    )
    table = (
        '<div class="scroll"><table><thead><tr><th>Account</th><th>Ccy</th>'
        '<th class="r">Debit</th><th class="r">Credit</th></tr>'
        f"</thead><tbody>{legs}</tbody></table></div>"
        if legs
        else ""
    )
    shares = (
        '<div class="scroll"><table><thead><tr><th>Account</th><th>ISIN</th>'
        f'<th class="r">Quantity</th></tr></thead><tbody>{quantities}</tbody></table></div>'
        if quantities
        else ""
    )
    return (
        '<div class="pad"><dl class="kv">'
        f'<dt>Outcome</dt><dd><code>{_e(proposal["outcome"])}</code></dd>'
        f'<dt>Residual</dt><dd class="num">{_e(proposal["expected_residual"])}</dd>'
        f'<dt>Rationale</dt><dd>{_e(proposal["rationale"])}</dd></dl></div>' + table + shares
    )


def _case_header(document: dict[str, Any], band: str) -> str:
    return (
        '<div class="panel"><div class="panel-h"><b>Case</b>'
        f'<span class="r mono">{_e(document.get("case_id", ""))}</span></div>'
        '<div class="pad"><dl class="kv">'
        f'<dt>Valuation point</dt><dd class="num">{_e(document.get("as_of"))}</dd>'
        f'<dt>NAV impact</dt><dd class="num" style="font-weight:600">'
        f'{_e(document.get("impact_bps"))} bps</dd>'
        f"<dt>Approval band</dt><dd>{_chip(band)}</dd>"
        f"<dt>Classification</dt><dd>{classification(document)}</dd></dl></div></div>"
    )


def _signals_panel(signals: list[Any]) -> str:
    if not signals:
        return ""
    return (
        '<div class="panel"><div class="panel-h"><b>What the numbers say</b>'
        '<span class="r">from both books, before any model was consulted</span></div>'
        '<div class="pad"><ul class="plain">'
        + "".join(f"<li>{_e(s)}</li>" for s in signals)
        + "</ul></div></div>"
    )


def _triage_panel(document: dict[str, Any]) -> str:
    triage = document.get("triage")
    if not triage:
        return ""
    override = (
        '<div class="note deny" style="margin-top:12px">Model answered '
        f'<code>{_e(triage["overridden_from"])}</code>, below the confidence floor, so it was '
        "escalated instead of routed.</div>"
        if triage.get("overridden_from")
        else ""
    )
    return (
        '<div class="panel"><div class="panel-h"><b>Triage</b>'
        '<span class="r">gemini-3.5-flash-lite</span></div><div class="pad">'
        f'<dl class="kv"><dt>Capability</dt><dd><code>{_e(triage["capability"])}</code></dd>'
        f'<dt>Confidence</dt><dd class="num">{_e(f"{triage["confidence"]:.2f}")}</dd>'
        f'<dt>Reasoning</dt><dd>{_e(triage["reasoning"])}</dd></dl>'
        f"{override}</div></div>"
    )


def _routing_panel(document: dict[str, Any]) -> str:
    if document.get("routed") is not False:
        return ""
    return (
        '<div class="panel"><div class="panel-h"><b>Routing</b></div>'
        f'<div class="pad"><div class="note deny">{_e(document.get("refusal"))}</div>'
        "<p class=\"muted\" style=\"font-size:11.5px;margin:10px 0 0\">No specialist is authorised "
        "for this kind of break, so none was engaged and no cause is claimed. This case needs a "
        "manual investigation.</p></div></div>"
    )


def _cause_panel(document: dict[str, Any]) -> str:
    verdict = document.get("verdict")
    if not verdict:
        return ""
    return (
        '<div class="panel"><div class="panel-h"><b>Established cause</b>'
        f'<span class="r mono">{_e(verdict["agent"])}</span></div><div class="pad">'
        f'<p style="margin:0 0 14px;font-size:14px">{_e(verdict["root_cause"])}</p>'
        f'<dl class="kv"><dt>Confidence</dt><dd class="num">'
        f'{_e(f"{verdict["confidence"]:.2f}")}</dd>'
        f'<dt>Citations</dt><dd class="num">{len(verdict.get("citations", []))}</dd>'
        "</dl></div></div>"
    )


def _evidence_panel(observations: list[Any]) -> str:
    if not observations:
        return ""
    return (
        '<div class="panel"><div class="panel-h"><b>Evidence cited</b>'
        '<span class="r">each figure traceable to the source it came from</span></div>'
        + _evidence(observations)
        + "</div>"
    )


def _proposal_panel(document: dict[str, Any]) -> str:
    if not document.get("proposal"):
        return ""
    return (
        '<div class="panel"><div class="panel-h"><b>Proposed correction</b>'
        '<span class="r">for your review &middot; not posted</span></div>'
        + _proposal(document.get("proposal"))
        + "</div>"
    )


#: Which renderer draws the result of each stage, so the stream and the full page cannot disagree
#: about what a finished stage looks like. Keyed by the stage names in `workflow.WORK_STAGES`.
STAGE_PANELS = {
    "triage": _triage_panel,
    "routing": _routing_panel,
    "investigation": _cause_panel,
    "proposal": _proposal_panel,
}


def progress(stages: list[tuple[str, str]]) -> str:
    """The rail while the fleet is running.

    Drawn complete-but-pending up front, because a progress list that grows as it goes cannot say
    how much is left -- and the honest answer to "why is this screen still" is "it is on step two of
    four, calling a model".
    """
    rows = "".join(
        f'<div class="pstep" data-stage="{_e(key)}">'
        f'<span class="pdot"></span><span class="plabel">{_e(label)}</span>'
        f'<span class="pnote"></span></div>'
        for key, label in stages
    )
    return (
        '<div class="panel" id="work-progress"><div class="panel-h"><b>Running the fleet</b>'
        '<span class="r" id="work-status">working</span></div>'
        f'<div class="pad">{rows}'
        '<div class="bar"><i></i></div>'
        '<p class="muted" style="font-size:11.5px;margin:10px 0 0">Each step is a real model call '
        "on Vertex AI. Results are saved as they land, so a refresh shows the same thing.</p>"
        "</div></div>"
    )


def case(detail: dict[str, Any], *, principal: Principal) -> str:
    """One exception, from the numbers through the reasoning to the signature."""
    document = detail["document"]
    case_id = str(document.get("case_id", ""))
    band = str(document.get("approval_band", "single_reviewer"))
    signed = list(document.get("signed_by", []))
    worked = bool(document.get("verdict"))

    left = _case_header(document, band) + _signals_panel(detail["signals"])
    sections = (
        _triage_panel(document)
        + _routing_panel(document)
        + _cause_panel(document)
        + _evidence_panel(detail["observations"])
        + _proposal_panel(document)
    )

    return shell(
        f"{case_id} — NAV Sentinel",
        _head(
            _e(describe(document)),
            _e(document.get("note")),
            back="Back to exceptions",
        )
        + f'<div class="grid"><div>{left}<div id="case-sections">{sections}</div></div>'
        f'<div class="stick" id="case-rail">'
        f"{_actions(document, principal, band, signed, worked)}</div>"
        "</div>"
        + _WORK_SCRIPT,
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
    from nav_sentinel.control_plane.approvals import BAND_REQUIREMENTS
    from nav_sentinel.control_plane.governance import ApprovalClass

    case_id = str(document.get("case_id", ""))
    outcome = document.get("last_outcome") or {}
    blocks = ""

    if not worked:
        from nav_sentinel.webapp.workflow import WORK_STAGES

        # The progress rail is carried on the form as data, so the streaming client can swap it in
        # without a round trip -- and so the markup for it lives in one place, next to its CSS.
        rail = escape(progress(list(WORK_STAGES)), quote=True)
        return (
            '<div class="panel"><div class="panel-h"><b>Investigate</b></div><div class="pad">'
            '<p class="muted" style="font-size:12.5px;margin:0 0 14px">The difference is '
            "classified, handed to the specialist authorised for that kind of break, and "
            "investigated against source data. You get a cause, the evidence behind it, and a "
            "correcting entry to review.</p>"
            f'<form class="block" id="work-form" method="post" data-progress="{rail}" '
            f'data-stream="/app/case/{_e(case_id)}/work/stream" '
            f'action="/app/case/{_e(case_id)}/work" '
            "onsubmit=\"var b=this.querySelector('button');"
            "b.disabled=true;b.textContent='Running…';\">"
            '<button class="btn wide" type="submit">Run the fleet</button></form>'
            '<p class="muted" style="font-size:11px;margin:10px 0 0;text-align:center">'
            "Gemini on Vertex AI &middot; about 20 seconds</p></div></div>"
        )

    eligible, _why = session.may_sign(principal, ApprovalClass(band))
    # The sentence is built here rather than taken from `may_sign`, which returns the enum's
    # vocabulary -- "cio escalation may be signed only by cio; you hold controller" is the code
    # talking. `may_sign` is still what decides; this is only how the decision is worded.
    already = principal.subject in signed
    approved = bool(document.get("approval_ref"))
    _allowed, required = BAND_REQUIREMENTS[ApprovalClass(band)]
    have = len(set(signed))

    signatures = "".join(
        f'<div class="sig"><div class="avatar">{_e(_initials(s))}</div>'
        f'<div class="n">{_e(s)}<span>signed</span></div></div>'
        for s in signed
    )
    for _ in range(max(0, required - have)):
        signatures += (
            '<div class="sig"><div class="avatar wait">&mdash;</div>'
            '<div class="n muted">Awaiting signature<span>outstanding</span></div></div>'
        )

    pct = int(100 * min(1.0, have / required)) if required else 100
    blocks += (
        '<div class="panel"><div class="panel-h"><b>Approval</b>'
        f'<span class="r">{have} of {required}</span></div><div class="pad">'
        f'<div class="meter"><i style="width:{pct}%"></i></div>'
        + (
            ""
            if not eligible
            else f'<p class="muted" style="font-size:12.5px;margin:0 0 8px">'
            f"{_e(band_label(band))} requires {_e(required)} signature"
            f"{'s' if required > 1 else ''} from {_e(role_label(_allowed))}"
            f"{', and they must be different people' if required > 1 else ''}.</p>"
        )
        + f"{signatures}"
    )
    if approved:
        blocks += (
            '<div class="note ok" style="margin:12px 0 0"><b>Approved</b> &mdash; '
            f'<span class="mono">{_e(document.get("approval_ref"))}</span></div>'
        )
    elif not eligible:
        blocks += (
            '<div class="note" style="margin:12px 0 0">This case is above your signing authority. '
            f"It requires <b>{_e(role_label(_allowed))}</b>. Your role on this deployment is "
            f"{_e(role_label([principal.role]))}.</div>"
        )
    if not approved:
        # A control that can only fail is not offered. A controller looking at an escalation gets a
        # disabled button naming who *can* sign it, rather than a live button, a click, and a
        # refusal -- the refusal is correct and the server still makes it, but an operations screen
        # should not invite an action it knows the answer to.
        if not eligible:
            label, blocked = f"{role_label(_allowed)} to approve", True
        elif already:
            label, blocked = "Signed — waiting for another signatory", True
        else:
            label, blocked = "Approve", False
        blocks += (
            f'<form class="block" method="post" action="/app/case/{_e(case_id)}/approve" '
            "onsubmit=\"var b=this.querySelector('button');b.disabled=true;"
            "b.textContent='Signing…';\" "
            'style="margin-top:12px">'
            f'<button class="btn wide" type="submit"{" disabled" if blocked else ""}>'
            f"{_e(label)}</button></form>"
        )
    blocks += "</div></div>"

    if outcome and not outcome.get("granted"):
        # Refusals only. A granted outcome repeated, one box lower, the reference the Approval panel
        # had just shown -- the same fact twice, which reads as two things having happened.
        blocks += (
            '<div class="note deny" style="margin-bottom:16px">'
            f'{_e(outcome.get("message"))}</div>'
        )
    if outcome and outcome.get("agent_posting_blocked"):
        # The analyst asked to approve, and the approval succeeded. Reporting "Posting refused"
        # to them framed a property of the system as a failure of their action -- they never
        # asked to post anything. So the headline is what did happen, and the control that
        # holds is stated underneath it as the reason the entry is safe to release, in muted
        # type rather than in red.
        blocks += (
            '<div class="panel"><div class="panel-h" style="background:var(--cleared-w)">'
            '<b style="color:var(--cleared)">Cleared for posting</b>'
            '<span class="r" style="color:var(--cleared)">signed</span></div><div class="pad">'
            '<p style="margin:0 0 12px;font-size:13px">The correcting entry is authorised and '
            "now leaves this system. It is released to the general ledger by your posting "
            "process &mdash; <b>no agent in NAV Sentinel can post it</b>, at any value.</p>"
            '<div class="note" style="font-size:11.5px">Verified rather than assumed: an attempt '
            "was made to post this entry as one of the specialists, carrying your signature, and "
            "it was blocked.<br>"
            f'<span class="mono" style="font-size:10.5px;color:var(--faint)">'
            f'{_e(outcome["agent_posting_blocked"])}</span></div>'
            '<p class="muted" style="font-size:11.5px;margin:12px 0 0">Your signature authorises '
            "the correction. It does not give any agent the authority to make it.</p>"
            "</div></div>"
        )
    return blocks


def _feed_line(line: dict[str, Any]) -> str:
    """One governance decision, as the operator reads it."""
    agent = f"  &middot;  {_e(line['agent'])}" if line.get("agent") else ""
    return (
        f'<div class="frow" data-e="{_e(line["effect"])}">'
        f'<span class="fa">{_e(line["at"])}</span>'
        f'<span class="fx">{_e(line["effect"].upper())}</span>'
        f'<span>{_e(line["policy"])}</span>'
        f'<span class="fr">{_e(line["reason"])}{agent}</span></div>'
    )


def _handover(snapshot: dict[str, Any]) -> str:
    """What is now waiting on a person.

    The band exists because four green ticks per row would otherwise read as "done". The fleet
    finishing is a handover, not a completion -- and saying so is the claim this project is built
    on, stated at the moment it becomes true rather than in a paragraph somewhere.
    """
    hand = snapshot.get("handover", {})
    settled = snapshot.get("settled")
    waiting = hand.get("sign", 0)
    manual = hand.get("human_investigation", 0)
    working = hand.get("fleet", 0)
    idle = hand.get("not_started", 0)

    if idle and not working and not waiting and not manual:
        lead = (
            f"<b>Nothing has been investigated yet.</b> {idle} exception"
            f"{'s' if idle != 1 else ''} waiting. Start the fleet from the exception queue."
        )
    elif working and not settled:
        lead = (
            f"<b>Investigation in progress.</b> {working} case"
            f"{'s' if working != 1 else ''} still being worked. No action needed from you yet."
        )
    elif waiting or manual:
        lead = (
            "<b>Ready for your review.</b> Every correction is drafted and none is posted. "
            "Nothing moves further without a signature."
        )
    else:
        lead = "<b>Nothing outstanding.</b> Every exception at this valuation point is closed."

    return (
        f'<div class="handover{" done" if settled else ""}">{lead}'
        '<div class="hs">'
        f'<span><b data-counter="hand_sign">{waiting}</b> awaiting your signature</span>'
        f'<span><b data-counter="hand_manual">{manual}</b> for manual review</span>'
        + (
            f'<span><b data-counter="hand_working">{working}</b> in progress</span>'
            if not idle or working
            else f'<span><b data-counter="hand_idle">{idle}</b> not started</span>'
        )
        + "</div></div>"
    )


def live(snapshot: dict[str, Any], *, principal: Principal) -> str:
    """The fleet at work, read back from the store.

    The screen exists because unattended work that nobody can see reads as nothing happening. Seven
    cases advancing through four stages at once is the thing a single case page structurally cannot
    show, and the governance feed underneath is what makes it a control plane rather than a job
    runner.

    Every number is counted from a persisted record, and the page says which window it is counting.
    A live display is the easiest place in a system to put a number that cannot be checked.
    """
    counters = snapshot["counters"]
    stages = snapshot["stages"]

    tiles = "".join(
        f'<div class="tile {cls}"><div class="lbl">{_e(label)}</div>'
        f'<div class="big" data-counter="{_e(key)}">'
        f'{"&mdash;" if counters.get(key) is None else _e(counters.get(key, 0))}'
        f'{suffix}</div><div class="sub">{_e(note)}</div></div>'
        for key, label, cls, suffix, note in (
            ("investigated", "Cases investigated", "t-ok",
             f'<span style="font-size:15px;color:var(--faint)">/{counters["cases"]}</span>',
             "cause established and correction drafted"),
            ("agents", "Specialists engaged", "", "", "FX, corporate actions, settlement, pricing"),
            ("tool_calls", "Source lookups", "", "", "each one checked against this fund's mandate"),
            ("evidence", "Evidence on file", "", "", "citable, and traceable to its source"),
            ("decisions", "Controls applied", "t-four", "", "recorded against each case"),
            ("denials", "Actions blocked", "t-esc", "", "the fleet was not permitted to proceed"),
        )
    )

    head = "".join(f'<th class="scell">{_e(s["label"])}</th>' for s in stages)
    rows = ""
    for case in snapshot["cases"]:
        cells = "".join(
            f'<td class="scell"><span class="sdot" '
            f'data-s="{_e(case["stages"][s["key"]])}"></span></td>'
            for s in stages
        )
        trailing = (
            f'<span class="none">{_e(case["refusal"])}</span>'
            if case["refusal"]
            else f'<span class="mono" style="font-size:11px">{_e(case["agent"])}</span>'
            if case["agent"]
            else '<span class="muted">—</span>'
        )
        rows += (
            f'<tr data-case="{_e(case["case_id"])}">'
            f'<td class="rail r-{_e(case["band"])}">'
            f'<a href="/app/case/{_e(case["case_id"])}" style="text-decoration:none;'
            f'font-weight:600">{_e(case["title"])}</a></td>'
            f'<td class="r num">{_e(case["impact_bps"]) or "—"}</td>'
            f"{cells}<td>{trailing}</td>"
            f'<td class="nx" data-k="{_e(case["next_kind"])}">{_e(case["next_step"])}</td></tr>'
        )

    feed = "".join(_feed_line(line) for line in snapshot["feed"]) or (
        '<div class="pad muted">No activity yet. Start the fleet from the exception queue and '
        "each control decision will appear here as it is made.</div>"
    )

    window = (
        f"live · this run started {_e(snapshot['since'][11:19])}"
        if snapshot.get("since")
        else "idle · no run in progress"
    )
    return shell(
        "Fleet activity — NAV Sentinel",
        _head(
            "Fleet activity",
            f"Every exception at the {_e(snapshot['as_of'])} valuation point, and how far the "
            "specialists have got with it. Each one is classified, handed to the specialist "
            "authorised for that kind of break, investigated against source data, and drafted into "
            "a correcting entry. Nothing is posted, and nothing is cleared without a signature.",
            actions='<a class="btn ghost" href="/app">Exception queue</a>',
        )
        + f'<div class="kpis" id="live-tiles" style="grid-template-columns:repeat(6,1fr)">{tiles}'
        "</div>"
        + _handover(snapshot)
        + '<div class="panel"><div class="panel-h"><b>Cases</b>'
        f'<span class="r" id="live-status"><span class="pulse"></span>{window}</span>'
        '</div><div class="scroll"><table class="lgrid"><thead><tr><th>Exception</th>'
        f'<th class="r">Impact</th>{head}<th>Investigated by</th>'
        '<th>Next step</th></tr></thead>'
        f'<tbody id="live-rows">{rows}</tbody></table></div></div>'
        '<div class="panel"><div class="panel-h"><b>Control log</b>'
        '<span class="r">what each specialist was permitted to do, and where it was stopped</span>'
        "</div>"
        f'<div class="feed" id="live-feed">{feed}</div></div>' + _LIVE_SCRIPT,
        principal=principal,
        active="live",
    )


# ---------------------------------------------------------------------------------------------
# Fleet
# ---------------------------------------------------------------------------------------------


def fleet(*, principal: Principal) -> str:
    """Who is published, what each may call, and what nobody is authorised to do."""
    from nav_sentinel.control_plane import packs
    from nav_sentinel.registry import discover

    cards = ""
    for m in sorted(discover.all_agents(), key=lambda m: m.agent_id):
        authority = (
            '<span class="chip b-four_eyes">drafts corrections</span>'
            if m.authority.may_propose_remediation
            else '<span class="chip b-single_reviewer">reports findings only</span>'
        )
        armor = (
            ' <span class="chip b-cio_escalation">reads outside data</span>'
            if m.untrusted_inputs
            else ""
        )
        tools = "".join(f'<span class="tag">{_e(t)}</span>' for t in m.allowed_tools) or "&mdash;"
        handles = (
            "".join(f'<span class="tag">{_e(c)}</span>' for c in m.handles_capabilities)
            or '<span class="muted">coordination only</span>'
        )
        cards += (
            f'<div class="acard"><h3>{_e(m.display_name)}</h3>'
            f'<div class="ref">{_e(m.ref)}</div>'
            f'<div style="margin-top:10px">{authority}{armor}</div>'
            f'<div class="row"><div class="k">Model</div><div class="v">'
            f"<code>{_e(m.model)}</code></div></div>"
            f'<div class="row"><div class="k">Investigates</div><div class="v">{handles}</div>'
            "</div>"
            f'<div class="row"><div class="k">May call</div><div class="v">{tools}</div></div>'
            f'<div class="row"><div class="k">May read</div><div class="v">'
            f"{_e(', '.join(m.data_scopes.read) or '—')}</div></div></div>"
        )

    coverage = discover.coverage()
    # Two different things, and counting them together overstated the gaps by three. A `.unclassified`
    # capability is the value triage returns when no root-cause family fits, and it must *never*
    # have an agent -- routing it would be routing "I do not know" to a specialist. A genuine gap is
    # a declared family that simply has nobody published to handle it.
    sentinels = {c for c, ref in coverage.items() if ref is None and c.endswith(".unclassified")}
    gaps = sum(1 for c, ref in coverage.items() if ref is None and c not in sentinels)
    rows = ""
    for capability, ref in sorted(coverage.items()):
        owner = packs.process_of(capability)
        if ref:
            routed, rail = (
                f'<span class="mono" style="font-size:11.5px">{_e(ref)}</span>',
                "single_reviewer",
            )
        elif capability in sentinels:
            routed, rail = (
                '<span class="muted" style="font-size:11.5px">sentinel &mdash; always a human</span>',
                "auto_clear",
            )
        else:
            routed, rail = '<span class="none">NO PUBLISHED AGENT</span>', "cio_escalation"
        rows += (
            f'<tr><td class="rail r-{rail}">'
            f"<code>{_e(capability)}</code></td>"
            f"<td>{_e(owner.name if owner else '—')}</td><td>{routed}</td></tr>"
        )

    tiles = (
        '<div class="kpis">'
        f'<div class="tile"><div class="lbl">Specialists available</div>'
        f'<div class="big">{len(discover.all_agents())}</div>'
        '<div class="sub">each with its own access rights</div></div>'
        f'<div class="tile t-ok"><div class="lbl">Capabilities</div>'
        f'<div class="big">{len(coverage)}</div>'
        f'<div class="sub">kinds of break, across {len(packs.registered())} departments</div>'
        "</div>"
        f'<div class="tile t-esc"><div class="lbl">Coverage gaps</div>'
        f'<div class="big">{gaps}</div>'
        f'<div class="sub">declared, with nobody published to handle them</div></div>'
        '<div class="tile t-four"><div class="lbl">Posting authority</div>'
        '<div class="big">0</div>'
        '<div class="sub">no agent holds it, at any value</div></div>'
        "</div>"
    )

    return shell(
        "Fleet — NAV Sentinel",
        _head(
            "Fleet",
            "The specialists available to this fund, what each may look at, and which "
            "kinds of break each is authorised to investigate. A specialist can be added or "
            "withdrawn without changing the desk.",
        )
        + tiles
        + f'<h2>The specialists</h2><div class="cards">{cards}</div>'
        + '<h2>Who investigates what</h2>'
        + f'<div class="panel"><div class="panel-h"><b>Who investigates what</b>'
        f'<span class="r">{len(coverage) - gaps - len(sentinels)} routed &middot; {gaps} unhandled '
        f'&middot; {len(sentinels)} sentinels</span></div>'
        '<div class="scroll"><table><thead><tr><th>Kind of break</th><th>Department</th>'
        f"<th>Authorised specialist</th></tr></thead><tbody>{rows}</tbody></table></div></div>"
        '<div class="note" style="margin-top:14px">Where no specialist is authorised for a kind '
        "of break, the case is <b>refused at routing</b>: no agent is invoked, no cause is "
        "asserted, and it stays in the queue as human work with the refusal recorded against it. "
        "It is not handed to whichever specialist looks closest &mdash; that returns a confident "
        "wrong answer with real citations attached, which is worse than no answer. "
        "<code>unclassified</code> means the break fitted no known family, and always goes to a "
        "person.</div>",
        principal=principal,
        active="fleet",
    )


# ---------------------------------------------------------------------------------------------
# Remediation
# ---------------------------------------------------------------------------------------------


def remediation(store: Any, case_id: str, *, principal: Principal) -> str:
    """The multi-week case as a timeline, which is how a case that runs for a month is read."""
    history = store.stages_for(case_id) if case_id else []
    decisions = store.decisions_for(case_id) if case_id else []
    if not history:
        return shell(
            "Remediation — NAV Sentinel",
            _head(
                "Remediation",
                "A NAV error that clears materiality stops being a one-day job: fund accounting "
                "restates, transfer agency establishes who dealt at the wrong price, and "
                "compliance decides whether the regulator must be told.",
            )
            + '<div class="panel"><div class="empty">'
            f"{MARK}"
            '<div style="font-size:15px;font-weight:600;margin-bottom:5px">No remediation case '
            "recorded</div>"
            f'<p class="muted" style="max-width:54ch;margin:0 auto;font-size:13px">Nothing under '
            f'<span class="mono">{_e(case_id)}</span>. Run <code>make remediation</code> against '
            "the same store to walk one.</p></div></div>",
            principal=principal,
            active="remediation",
        )

    steps = ""
    for entry in history:
        steps += (
            f'<div class="step"><div class="when">{_e(entry.get("occurred_on") or "—")}</div>'
            '<div class="rail-c"><div class="dot"></div></div>'
            f'<div class="body"><b>{_e(str(entry.get("to")).replace("_", " "))}</b>'
            f'<div class="d">{_e(entry.get("note") or "")}</div>'
            f'<div class="t">written {_e(str(entry.get("recorded_at"))[:19])}</div></div></div>'
        )
    denials = [d for d in decisions if d.get("nav.policy.effect") == "deny"]
    refusals = "".join(
        '<div class="step"><div class="rail-c"><div class="dot deny"></div></div>'
        f'<div class="body"><b>{_e(d.get("nav.policy.id"))}</b>'
        f'<div class="d">{_e(d.get("nav.policy.reason"))}</div></div></div>'
        for d in denials
    )
    dates = [e.get("occurred_on") for e in history if e.get("occurred_on")]
    span = ""
    if len(dates) >= 2:
        from datetime import date as _d

        span = f"{(_d.fromisoformat(dates[-1]) - _d.fromisoformat(dates[0])).days} days"

    tiles = (
        '<div class="kpis">'
        f'<div class="tile"><div class="lbl">Recorded transitions</div>'
        f'<div class="big">{len(history)}</div>'
        '<div class="sub">each one recorded when it happened</div></div>'
        f'<div class="tile t-four"><div class="lbl">Elapsed business dates</div>'
        f'<div class="big">{_e(span.split()[0]) if span else "—"}'
        '<span style="font-size:13px;color:var(--faint)"> days</span></div>'
        '<div class="sub">wall-clock is compressed; these are not</div></div>'
        f'<div class="tile t-ok"><div class="lbl">Policy decisions</div>'
        f'<div class="big">{len(decisions)}</div>'
        '<div class="sub">recorded against this case</div></div>'
        f'<div class="tile t-esc"><div class="lbl">Refusals</div>'
        f'<div class="big">{len(denials)}</div>'
        '<div class="sub">steps that were not permitted</div></div>'
        "</div>"
    )

    return shell(
        "Remediation — NAV Sentinel",
        _head(
            "Remediation",
            f'<span class="mono">{_e(case_id)}</span> &mdash; a published NAV error, from '
            "detection through investor compensation. Fund accounting sizes it, transfer agency "
            "establishes who dealt at the wrong price, and compliance decides what must be "
            "reported. Business dates are real; the elapsed wall-clock is compressed.",
        )
        + tiles
        + '<div class="grid"><div>'
        '<div class="panel"><div class="panel-h"><b>Case history</b>'
        f'<span class="r">{len(history)} transitions</span></div>'
        f'<div class="pad"><div class="steps">{steps}</div></div></div></div>'
        + '<div class="stick">'
        + (
            '<div class="panel"><div class="panel-h" style="background:var(--escalate-w)">'
            '<b style="color:var(--escalate)">Refused</b>'
            f'<span class="r">{len(denials)}</span></div>'
            f'<div class="pad"><div class="steps compact">{refusals}</div></div></div>'
            if refusals
            else ""
        )
        + '<div class="note">This case can only move along the steps its process defines. The '
        "shortcut an operator under time pressure would most want &mdash; paying compensation "
        "before it has been approved &mdash; is not one of them, for anybody.</div>"
        "</div></div>",
        principal=principal,
        active="remediation",
    )
