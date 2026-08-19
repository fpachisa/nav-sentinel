# NAV Sentinel — Delivery Plan v3

**Deadline:** 31 Aug 2026, 17:00 PDT · **Plan date:** 18 Aug 2026 · **Remaining:** 12 days, both weekends worked
**Track:** C — Fortified Enterprise Fleet · **Entrant:** individual
**Supersedes:** v3.3 (APPROVE WITH CHANGES — 3 blockers), v3.2, v3.1, v2 (REJECTED), v1

---

## 1. What five gates established

| Gate | Verdict |
| :--- | :--- |
| Plan v1 | APPROVE WITH CHANGES — 4 blockers, 10 majors |
| S0 code | **REJECT** — 3 blockers, 12 majors |
| Plan v2 | **REJECT** — 5 blockers, 14 majors |

v2 was rejected principally because its own honesty section contained a factual misstatement. That is corrected first, because it changes the architecture.

### The Model Armor finding — placement dominates, and windowed screening fixes it

Two earlier versions of this section were wrong. v2 called the bypass "latent"; it is live. v3.0 then
attributed it to *dilution* and concluded no chunk size could rescue it. Independent re-testing
falsified that: those tests all placed the injection at the head of the document, so they measured
**position** while labelling it concentration.

The reproducible finding, live service, same 19,662-byte document, poison held constant at 1,008 bytes:

| Injection position | Whole-document screen |
| :--- | :--- |
| head | **missed** (0/2) |
| middle | **missed** (0/2) |
| tail | caught (2/2) |

And a separate, independent failure above a size threshold, bisected to between 40,827 B and 41,329 B:

```
filter_match_state: NO_MATCH_FOUND
pi_and_jailbreak_filter_result { execution_state: EXECUTION_SKIPPED
  message_items { message: "Detection skipped as token limit exceeded." } }
invocation_result: PARTIAL
```

Code reading only `filter_match_state` — as ours does — cannot distinguish a skipped scan from a clean
one. Confirmed end to end: `admit_untrusted_content` admitted 152,066 bytes with the injection intact.

**Windowed screening closes both.** On the head-placed case that the whole-document screen missed:

| Window | Overlap | Result |
| :--- | :--- | :--- |
| 4,000 B | 0 | missed |
| 1,000 B | 0 | caught |
| 1,000 B | 500 B | caught, zero false positives |

The window must approach the size of the injection; 50% overlap prevents a split across a boundary.

**Consequence.** Model Armor is a position-sensitive detector with an undocumented size cliff, not a
boundary. Used correctly — windowed, with all three response fields checked — it is a working control.
Used as shipped, it is not.

### The architectural response: quarantined extraction

Untrusted document text must never enter a privileged model context.

```
   EDGAR filing (attacker-controllable prose)
        │
        ▼
   Model Armor screen            ← retained as one layer, no longer the guarantee
        │
        ▼
   EXTRACTOR  (no tools, no identity, no memory; typed output only)
        │      returns: {ex_date, gross_rate, withholding_pct, dr_ratio}
        ▼
   INVESTIGATOR  (reasons over VALUES, never over prose)
```

The extractor holds no tools, no registry authority, and emits only a validated schema, so it cannot
act on an instruction. But quarantine bounds **instruction** injection only — it does nothing against
**data** poisoning. The poisoned notice already carries attacker-chosen values (`Withholding Tax: 0.00%`),
and an extractor that dutifully returns them has been attacked without receiving an instruction. The
full control chain is therefore three-deep, and the plan should claim no more than it has:

| Layer | Bounds |
| :--- | :--- |
| Windowed Model Armor screening | Known injection patterns |
| Quarantined extractor | Instruction injection — blast radius |
| Value cross-checks vs books-and-records + plausibility bounds | Data poisoning |
| P-003 human approval | Everything that survives the above |

A measured placement finding, a documented size cliff, and an architecture that survives the guardrail
failing is a far stronger Best-Architectural-Design artefact than "we enabled Model Armor".

### The four blockers in S0, all reproduced

| # | Defect | Evidence |
| :--- | :--- | :--- |
| B1 | Tool allowlist evaded by caller-supplied callable; P-001 writes a **false** audit record | `call_tool("ecb_fx.rate_on", bnr.cash_movements)` returned cash rows; log said `allow … resource=ecb_fx.rate_on` |
| B2 | Fixture books violate double entry | Σ golden = −129,320.80 vs required −5,338,120.80; gap = 5,208,800.00 = the unsettled LVMH buy with no contra leg |
| B3 | `authorize_*` trust a caller-supplied manifest | forged manifest escalated drafting **and** posting; `human_approval_ref` is an unvalidated string |
| B4 | Control total blind to the FX chain | every `fx_rate` set to `999999.99999999` → **all tests still green** (32/32 when found; 70/70 when last re-checked, so the blocker is live, not historical) |

### The control total, honestly

Closure today is the identity `Σ(a−c) = Σa − Σc` — the generator defines NAV as Σ positions + converted cash, and `explained_total` re-sums the same differences. It proves detection drops no line and lots aggregate. It proves nothing about the fleet.

v2 proposed `Σ golden == −control_total`. That is a **fixture-integrity** check and it is worth having (it catches B2), but it compares two artefacts the same generator wrote. The load-bearing assertion, absent from v2 entirely:

> **`Σ (fleet-proposed corrections) == −control_total`, per fund, from the agents' own output.**

`RemediationProposal.expected_residual` already exists as the hook and nothing computes it. This is why S4 (remediation drafting) is now **mandatory** rather than should-have: without it there is no 40% evidence.

---

## 2. Scope cut on paper, not deferred to a ladder

v2's ladder was the load-bearing assumption and could not climb far enough: it contradicted its own protected set, counted 2h that were never budgeted, and closed 1.5h of a 6.7h gap at its expected rungs. v3 cuts now.

| Cut | Was | Now | Saves |
| :--- | :--- | :--- | :--- |
| Funds | 2 (MERID-GEF, ATLAS-USE) | **1** (MERID-GEF) | 2.0h |
| Golden scenarios | 9 | **6** — two each across three categories | 1.5h |
| Investigators | 5 | **3** — FX, corporate actions, settlement | 2.5h |
| Memory Bank | full integration | **Firestore recurrence store behind a Memory-Bank-shaped interface**, documented as such | 3.0h |
| Exception console | live web UI (8h) | **cut** — the case trace and eval output carry the evidence | 8.0h |
| CI | GitHub Actions | cut | 1.0h |
| Blog post | bonus | cut | 2.0h |
| Multimodal (Gemma/Veo/Lyria) | bonus | cut, logged as a decision | — |

Dropping to three investigators retires the pricing and cash-fees categories. The registry will correctly report **NO AUTHORISED INVESTIGATOR** for both, which is itself a governance demonstration rather than a gap — and it is honest, which a silently missing category would not be. It also deletes the fee-accrual scenario that carried two of S0's domain errors (a 3× overstated base and an accrual booked as custodian cash), so the simplification removes defects rather than hiding them.

**Six scenarios, one fund, three categories:**

| Scenario | Category |
| :--- | :--- |
| FX stale rate (AAPL) · FX inverted cross (GSK) | `fx_rate` |
| 2:1 split unapplied (MSFT) · ADR dividend gross-vs-net (Ambev) | `corporate_action` |
| Trade-date vs settlement-date (LVMH) · failed trade (Pfizer) | `settlement` |

---

## 3. Capacity, arithmetic checked

| | Hours |
| :--- | :--- |
| Build — §5 (S5 split into S5a 1.5 + S5b 2.5) | 63.1 |
| Build — §5b platform sections (S0-R.9 4.5, S0-R.10 1.0, S9 **4.0**, S10 2.0, figure 5 1.5) | 13.0 |
| Rework reserve — 9 code gates × 2.5h | 22.5 |
| **Commitment** | **98.6** |
| Already complete ahead of the calendar — S0a 1.5, S0-R.1 2.0, README patch 0.25, architecture figures 1.5 | −5.3 |
| **Remaining** | **93.3** |
| Capacity — 12 days, both weekends, 8.5h/day | 102.0 |
| **Margin** | **8.8h (8.6%)** |

§5b now costs 13h of build, down from 16h: §5c's shot list showed the TA investigator and
registrar cassette buy zero screen time, so 2.5h came out on measured grounds rather than
negotiation. §5b costs 13h of build. It is retained on an explicit instruction that timeline is not the
binding constraint on this project, and because the extensibility story is a headline selling point
that must be **visible in the video** rather than merely true in the test suite.

The rate is stated once and held: **8.5h/day for twelve consecutive days including both weekends.**
v3.2 contradicted itself here, quoting 8h/day in the table and "~7h/day of productive build" twelve
lines below; at 7h/day this plan is 7h under water and the contradiction hid that. 8.5h/day is a
demanding rate and it is the rate this scope requires — if it does not hold, the §3 response
releases in order.

Of the 22.5h reserve, **12.2h is scheduled in-line** in §7 and **10.3h is deliberately unallocated**
— rework lands on whichever gate produces findings, and pretending to know which day would be false
precision.

**2.5h per gate is a budget fitted to capacity, not an estimate derived from history — and the
distinction matters.** The five gates so far produced 15 blockers and 55 majors at roughly 10h of
re-planning each. At that rate **two gates exhaust the entire 22.5h reserve and the 8.8h margin with it.**
The reserve is a bet that remediating narrower, already-planned sections costs a quarter of what
re-planning the whole project cost. That bet may lose, which is what §3's ordered response is for.

The nine code gates are: S0-R · S0-R.9+.10 · S7a · S1 · S2a · S3 · S4 · S5 · S7+S9.

**The margin is thin and it is stated rather than hidden.** One further discovery of S0-R's magnitude
breaks this plan. The pre-decided response, in order — **cheapest and least-scoring first, the 40% axis
last**: (1) CI dropped (0.5h); (2) S10 per-tenant policy documented rather than built (2.0h); (3) S9
transfer agency reduced to one scenario (2.0h) — never to zero, because it is the on-camera evidence
for §5b, and **S0-R.10 is not on this ladder at any rung** since it is the only evidence that
survives every cut; (4) the second adversarial case dropped (0.5h);
(5) S2 memory degraded to a stub interface (1.0h); (6) S8b's rehearsal compressed (1.0h); (7) only
then S4 reduces to materiality routing with no drafting — which forfeits the 40% closure proof and is therefore the last resort, not
the first. v3.0 had this ordering inverted, sacrificing the heaviest-weighted axis before touching 2h of
should-have work.

**Trigger, observable:** at 18:00 each day, if cumulative build-hours variance exceeds 4h, or the day's scheduled items are not code-review-closed, the next response above is taken that evening.

**Working assumption, stated once and held:** 12 consecutive days, no rest day, both weekends,
**8.5h/day of productive build** — which for solo work means roughly 10–11 elapsed hours daily.
**This is feasible full-time and impossible alongside employment.** If the days are evenings and
weekends rather than dedicated, the plan does not hold and scope must halve, not shrink.

v3.2 quoted 8h/day in the table and 7h/day in this paragraph. At 7h/day this scope is 7h under
water, and the contradiction concealed that. One figure now, and it is the demanding one the scope
requires.

There is also **no slack day.** v3.0 claimed the final two days carried no build work; that was
false, and v3.2 repeated the claim in §7 while its own table contradicted it. The 8.8h margin is the
slack. What de-risks a late S7 is that **S7a banks the mandatory Cloud Run proof on 23 August**,
eight days before the deadline.

---

## 4. Critical path

```
S0a ──▶ S0-R (incl. .9) ──▶ S0c ──▶ S7a ──▶ S1.1 ──▶ S1.5 ──▶ S2a ──▶ S3 ──▶ S4 ──▶ S5 ──▶ S8
```

**S0-R.9 sits inside S0-R, before S1.** Decoupling after the investigators exist means rewriting
them; the seam has to be there before anything is built on it. **S9 and S10 are off the critical
path** — they extend a working platform rather than gate one.

**S0a is first**, not second: it is the only disqualifying unknown. **S5 is on the path** — its numbers
feed the README, the Devpost description and the video, so S8 cannot start before it. S7 sits parallel
to S4/S5 because S7a already banked the mandatory proof. S4 depends on S1.1 only, not S3.

---

## 5. Work breakdown

### S0-R — Foundations remediation · 16.0h · M

| ID | Task | h |
| :--- | :--- | :--- |
| .1 | Gateway owns a `name → callable` registry; `call_tool` takes a name only and resolves the function. Tool modules not importable from `agents/`. *(B1)* | 2.0 |
| .2 | `acting_as(agent_ref: str)` resolves the manifest from the published catalogue — a forged manifest can never enter the context. Every `authorize_*` takes its subject from `identity.current()`. `human_approval_ref` must resolve to a Firestore approval record — **includes the approvals-collection slice pulled forward from S2a**, without which this gate cannot close. *(B3)* | 2.5 |
| .3 | Fixtures rebuilt: one fund, six scenarios, **contra cash/payable leg on every trade-date recognition**, and a **second NAV cycle** for recurrence. *(B2)* | 3.0 |
| .4 | Security master corrected: Ambev `US02319V1035` (`US0028241000` is Abbott), GSK `GB00BN7SWP63`, ISIN→CIK map, split applied consistently. Corporate-action evidence served from a **committed EDGAR cassette** relabelled `sec_edgar_fixture` — the seeded events are synthetic and no real filing corroborates them, so citing live EDGAR would be dishonest. | 1.5 |
| .5 | Golden schema → `expected_corrections: [{leg, account, currency, amount}]` to express multi-leg corrections; all six re-derived independently of the generator. Assertions: `Σ golden == −control_total`; `market_value_base == quantize(q × p ÷ fx_rate)` per row; `fx_rate` equals the ECB rate for its stated date; **withhold-one-break negative test** asserting the residual equals that break to the cent. *(B2, B4)* | 1.5 |
| .6 | Remove the `identity_to_base` default from `residual`, `is_complete`, `explained_total`, `signed_impact_base`. Fix `summary()`'s hardcoded `base_currency: None`. Quantity-break materiality floor; invert the test that pins 2× auto-clear as correct. | 0.75 |
| .7 | **Model Armor + quarantine.** Screen in **1 KB windows with 500 B overlap**, preferring structural boundaries; fail closed per window. Each window gated on all three fields: `invocation_result == SUCCESS`, PI `execution_state == EXECUTION_SUCCESS`, `match_state == NO_MATCH_FOUND`. Then the typed extractor (no tools, no identity, schema output) plus value cross-checks against books-and-records. | 4.0 |
| .8 | Promoted from v2's accepted-minors list, because each falsifies a headline claim: registry cache invalidation (else "a publish, not a code change" is false in a live service); `detect_nav_breaks` one-sided NAV (else the control total silently reports nothing); `edgar._throttled` async-safe limiter (else it blocks the S3 runtime); `as_of` filter on all detectors (else the two-cycle fixture reports phantom breaks). | 0.75 |

### Remaining sections

| ID | Section | h | Pri | Notes |
| :--- | :--- | :--- | :--- | :--- |
| S0a | Stack compliance proof | 1.5 | M | One **ADK** agent, one Vertex call per model ID, OTLP export, and the returned model version **asserted into a trace attribute by a test**. Both IDs are confirmed to resolve on Vertex in this project, so the disqualification risk is model-*wiring*, not model availability. Includes a **preflight assertion that `gcloud config get-value project` matches `GOOGLE_CLOUD_PROJECT`** — the CLI here defaults to a different project, and `bootstrap.sh` shells out to gcloud. |
| S0c | Offline reproducibility | 2.0 | M | Commit fixtures + ECB cassette; `make verify` runs offline; `LICENSE`; fix `make demo` (currently `ModuleNotFoundError` in the README's own spin-up) and `make lint` (ruff absent). Golden file `--check` mode so regeneration cannot silently replace reviewed ground truth. |
| S7a | Deployment vertical slice | 5.0 | M | Pub/Sub push → Cloud Run (per-agent SA) → gateway → Vertex Gemini → Model Armor regional → span in Cloud Trace. Retires stack compliance, the push-vs-pull decision that would otherwise force S3 rework, Model-Armor-from-Cloud-Run IAM, and the mandatory Console proof. No FastAPI service exists yet. |
| S1 | Agent layer (3 investigators) | 6.5 | M | S1.1 contract 2.0 · FX 1.5 · corporate actions 2.0 · triage 1.0. **All three are ADK agents** — the framework requirement must be met by the real fleet, not by S0a's throwaway. |
| S2a | Firestore repository | 2.0 | M | Cases, proposals, **append-only** policy decisions keyed by `case_id`+`trace_id`. Approvals slice moved into S0-R.2. `_decision_log` is a process-global list today and cannot survive a Cloud Run split. |
| S2 | Memory (shim) | 1.0 | **M** | Firestore recurrence collection behind a Memory-Bank-shaped interface, labelled as such. **Promoted to mandatory:** its criterion — cycle 2 makes zero external fetches and ≥50% fewer tool calls — is the most direct "friction removed" measurement in the plan, and friction removed is the 40% axis. Funded by cutting the volume run. |
| S3 | Orchestration | 5.0 | M | Pub/Sub dispatch, detection trigger, cycle runner. |
| S4 | Remediation & approval | 4.0 | **M** | Multi-leg proposals (`JournalEntry`, `ReconcilingItem`, `QuantityRestatement` — two of six outcomes are not journals); `balances` grouped **by currency**; approval as a Firestore record + CLI. Mandatory because the 40% proof needs fleet-proposed corrections. |
| S5a | Eval harness + side-by-side report shell | 1.5 | M |
| S5b | Metrics, heuristic baseline, perturbed variants | 2.5 | M | **Headline metric: leg-level correction accuracy and root-cause accuracy** against S0-R.5's `expected_corrections` golden — a pass-through stub cannot fake either. `Σ fleet corrections == −control_total` is demoted to a **closure invariant**, because `signed_impact_base` already computes it deterministically and a stub returning its negation satisfies it with no model call. Heuristic baseline scored on the same golden **and on perturbed/ambiguous cases**, with the framing pre-committed before the number is seen. Two adversarial cases: one **pricing** break (correctly triaged, then refused by the registry for want of an authorised agent — this is what makes the three-investigator cut a demonstrated control rather than a rationalisation) and one where the **custodian** is the incorrect side. |
| S7 | Deployment | 6.0 | M | Cloud Run under per-agent SAs, IaC, `make teardown` — **verified against a throwaway resource prefix, not executed against the demo deployment**, since the recording is the following day and the brief's own guidance is to tear down after capturing proof — **budget alert**, ingress authentication (fund NAV data must not be publicly readable), Pub/Sub push OIDC, no secrets in image. Fix `bootstrap.sh` role-binding idempotency — it `continue`s on an existing SA and never reconciles roles, so "re-run it freely" is false. |
| S8a | Reproducibility + Devpost text | 3.5 | M | Includes a stated decision on the **hosted URL**: forfeited when the console was cut. "Optional but highly encouraged" in the brief, so the blank field is explained rather than left blank. | README verbatim in a clean container; **Devpost technical description** (mandatory, omitted from v1 and v2 — problem, features, technology inventory, data sources, findings/challenges/learnings); checklist mapped 1:1 with an evidence column. |
| S8b | Demo script, rehearsal, record | 3.5 | M | Script, **rehearsal before the take**, then record. Pre-captured Console screenshots are for navigation only and must never stand in for the agent run — the briefing requires a live, unedited demonstration. |
| — | Architecture diagram | 1.5 | M | Drafted **day 1**, so it can discipline the S3/S7 decisions it describes. Counted once (v2 double-counted it). |
| — | CI (GitHub Actions, `make verify` offline) | 0.5 | S | ~30 min once S0c makes the suite offline, and it continuously protects the "reproduces byte-identically" claim. The rubric names production-mindset explicitly. |
| — | Security pass on the deployed surface | 0.5 | M | Was scheduled inside S7 with no hours and no criterion. Ingress authentication, Pub/Sub push OIDC, no secrets in image. |
| — | README honesty patch + `LICENSE` | 0.25 | M | **19 August, not 30 August.** See below. |
| — | Devpost skeleton, credit application, `#AllThingsAgenticHackathon` post | 0.3 | M | Devpost permits post-submission editing, so an early skeleton converts a hard deadline into a soft one. |

### S8.0 — README honesty patch · 0.25h · **19 August** · M
Deferring this to the final day is not defensible: the repo is **public now**, the claims are false, and
**no test touched Model Armor at all**. The plan applies exactly this standard to
itself in S0-R.4 — relabel the EDGAR cassette because citing live EDGAR "would be dishonest" — so it
cannot exempt the one artefact judges actually read. The 15-minute patch lands 19 August; the full rewrite
stays in S8a. Every `[x]` in the Status section must name the test or trace that evidences it.

Everything v3.2 listed here was already fixed in `65a7539`, and the list then rotted a second way: it kept citing "the 32-test suite" after the suite reached 70, and the README carried the same stale figure in three places plus a "no test touches Model Armor" disclaimer that its own later commits had falsified. Both classes are now guarded by `tests/test_readme_claims.py`, which asserts every stated count matches collection and that the Model Armor disclaimer neither understates coverage that exists nor overstates what stubbed tests prove.

**Two claims survive and are still to fix:** the Gemini 3.5 aside at `README.md:75`, which reads as pre-emptive excuse-making, and step 3's "fetches live ECB reference rates", which contradicts S8a's offline criterion.

---

## 5b. The platform claim, and what makes it credible on camera

Track C asks for a *fleet*, not a workflow. NAV reconciliation is one process; the control plane
should host any reconciliation-shaped process, for any client. That is a strong selling point and
it is currently **false**. Verified by AST sweep — six one-hop violations, not the five v3.2 named:

```
control_plane/audit.py      -> domain.models.ExceptionCase
control_plane/gateway.py    -> domain.models.ExceptionCase
control_plane/gateway.py    -> tools.catalogue          (missed by v3.2)
control_plane/policies.py   -> domain.models.{ApprovalClass, ExceptionCase}
registry/discover.py        -> domain.models.BreakCategory
registry/models.py          -> domain.models.BreakCategory
```

Three deeper couplings that no import list reveals:

1. **`control_plane` reads eleven members of `ExceptionCase`**, not the four v3.2 assumed —
   `approval_class, as_of, breaks, case_id, category, fund_id, nav_impact_bps, recurrence_key,
   severity, status, trace_id`. All of them in `audit.py` and `policies.py`; `gateway.py` reads
   **none** and only annotates. Four are irreducibly fund-accounting.
2. **Three domain enums are consumed with no import at all** — `case.status.value`,
   `case.severity.value`, `case.category.value` in `audit.py`. Any import-based check goes green
   while the coupling stands.
3. **`Authority.max_autonomous_bps` is a basis-point field in the registry manifest schema**,
   present in all seven published manifests. A bps ceiling is meaningless for a shares-denominated
   process, and v3.2's "fourth coupling" paragraph missed it entirely.

### How the claim gets evidenced

The audience decides the form. **Judges watch a four-minute video; they do not clone the
repository.** A test suite is the right evidence for a reviewer and invisible to a judge, so the
claim is carried on screen and backed in the suite — not the other way round.

**On camera (primary).** A second process, real enough to run: its own fixtures, its own
investigator on Gemini, cited evidence, and its closure proof in a different unit. Then the
artefact that makes the architectural claim unfalsifiable in five seconds —
`git diff --stat` for the commit that added it, showing **zero lines changed** in
`control_plane/`, `registry/` and the telemetry layer.

**In the suite (backing).** An AST test with actual teeth, and a conformance test that drives the
control plane over a process that does not exist in the codebase. These survive the descope ladder;
the demo does not, which is why both exist.

### S0-R.9 — Decouple the control plane · 4.5h · M

Re-budgeted from 3h: v3.2 omitted the catalogue work, which is a registration inversion of an
immutable module-level singleton plus its test seam and every caller.

| Concept | Why it leaks | Resolution |
| :--- | :--- | :--- |
| `ApprovalClass` | Governance vocabulary, not fund accounting | Moves to **`control_plane/governance.py`**, which imports nothing from the package. The registry capability-string change lands **first**, removing `registry → domain`, so no cycle can form. |
| `ExceptionCase` | 11 members read, 4 of them fund-specific | **Not** protocol-ised — a Protocol satisfied by `ExceptionCase` still admits one. The process hands over a flat `CaseFacts`: `case_id, subject_id, as_of, item_count, recurrence_key, status: str, capability: str, severity: str, impact: (Decimal, unit: str)`; `trace_id` is returned, not written back.<br><br>**No `band` field.** v3.3 carried one, which is caller-supplied governance — B3 reappearing on the governance axis — and the section then contradicted itself by also saying the control plane derives the band. The process supplies a unit-tagged magnitude; `policies.band_for` returns the band.<br><br>**A `pydantic.BaseModel`, `frozen=True`, `extra="forbid"` — not a dataclass.** Pydantic coerces a `StrEnum` to `str`, so a domain enum cannot be smuggled in through `status` or `capability`; a frozen dataclass passes it through intact with `.value` still live and every static check green. The mechanism decides whether that hole is open.<br><br>**`as_span_attributes()` is owned by `control_plane`** — not a mapping handed in, which would put the `nav.*` keys under process control, make the audit record non-uniform, and be invisible to both S0-R.10 checks. `severity` is retained rather than silently dropped from the span. |
| `BreakCategory` | A closed enum cannot route a second process | `handles_capabilities: list[str]`, namespaced (`nav.fx_rate`, `ta.subscription_in_transit`). `coverage()` takes its namespace from registered packs; `registry/cli.py` stops calling `.value`. |
| `max_autonomous_bps` | A bps ceiling in a process-agnostic registry | `max_autonomous_impact: {value, unit}`; the existing zero-headroom test asserts on the value. |
| bps materiality | Units differ per process | **The control plane owns the threshold table and derives the band itself**: `policies.band_for(impact: Decimal, unit: str, policy: ThresholdSet) -> ApprovalClass`. The process supplies a unit-tagged magnitude and nothing else. Pulled out of S10 so it cannot be lost with it — v3.2 made S0-R.9 depend on a section sitting at descope rung 2. |
| `gateway -> tools.catalogue` | The catalogue is a hardcoded 17-entry singleton | The **port** moves to `control_plane` — `register(pack)` returning an immutable view. Specs stay in the packs. |
| **Manifest sourcing** | `registry/models.py:20` sets `MANIFEST_DIR = Path(__file__).parent / "manifests"`, so **a new process's manifest is a new file inside `registry/`** — S9's headline zero-diff artefact would contradict itself on screen | `register(pack)` carries the pack's manifest directory; `load_manifests()` aggregates over `packs.manifest_dirs()`; `MANIFEST_DIR` becomes the NAV pack's own. `coverage()` takes its capability universe from **registered packs, not published manifests** — otherwise an uncovered capability disappears instead of reporting NONE, and shot 10's governance beat becomes inexpressible. |
| `discover_for_category` | Published as a *governed tool name* (`tools/catalogue.py:64`) and declared in `triage-agent.yaml` | Renaming touches `registry/{models,discover,cli}.py`, `tools/catalogue.py`, seven manifests, two tests. Inside the 4.5h, but named so it is not discovered mid-change. |

**Acceptance:** `ApprovalClass` is defined in `control_plane/governance.py`; every module imports
cleanly in isolation in both orders; no capability string is unnamespaced; `control_plane` contains
no reference to `case.fund_id`, `case.breaks`, `case.category`, `case.severity`, `case.status` or
`case.nav_impact_bps`.

### S0-R.10 — Make the seam enforced, not asserted · 1h · M

- **Transitive** import closure, not one hop: `control_plane` and `registry` must not *reach* any
  process package. One-hop misses `gateway → tools → domain` and `identity → registry → domain`.
- **Allow-list attribute scan**, not a deny-list: the attribute names `control_plane/` reads off its
  case parameter must be a **subset of `CaseFacts.model_fields`**. A six-name deny-list was
  incomplete (`hypotheses`, `proposal`, `value_breaks`, `prior_occurrences`, `created_at` all
  reachable) and self-colliding, since `status` was both forbidden and a declared field. An
  allow-list is complete by construction. `getattr` on the facts parameter is forbidden outright.
- **Synthetic-process conformance test**: drive `case_trace`, `route_for_approval` and
  `may_post_entry` over a non-NAV case defined in the test file. Proves the seam without a second
  product, and survives every ladder rung.
- **Span-attribute uniformity test**: the root-span attribute key set for a TA case equals a NAV
  case's. That is what "the same governance log runs over it" actually means.
- A **composition root** outside `control_plane/` and `registry/` imports both and calls
  `register()`. `if TYPE_CHECKING:` imports count as violations — stated now so the first
  implementer cannot exempt them to make the check pass.

### S9 — Transfer agency process pack · 4h · S

Re-cut to 4.0h by §5c's shot list: models 0.5 · detection 1.0 · fixtures with contra legs 1.5 ·
manifest and routing 0.4 · golden and shares closure 0.6. The side-by-side eval row moves to S5a.

**The TA investigator and the registrar cassette are cut** — 2.5h buying zero screen time. v3.3
added them so the TA agent would not "read on camera as the weak agent"; §5c shows it never reaches
camera. `subscription_in_transit` is corrected by *arithmetic* — add the in-transit shares — so TA
needs no model, and a fleet reserving judgement for where judgement is required is a better claim
than one putting an LLM on every step.

Shares in issue per the registrar against per fund accounting. Capabilities
`ta.subscription_in_transit`, `ta.redemption_unprocessed`, `ta.switch_not_booked`.

The control total is denominated in **shares** — the point being that it proves the band derivation
is unit-driven rather than covertly tied to basis points, and exercises closure in a second unit.
Shares corrections route through `QuantityRestatement`, which **S4 already requires** for the MSFT
split, so the quantity path is not new work created by S9.

```
control total     +12,500 shares
less corrections  −12,500 shares
                  ──────────────
residual                       0
```

**Acceptance — written as demo artefacts, because that is the evidence channel:**

1. `git diff --stat` for the S9 commit shows **zero lines** in `control_plane/`, `registry/` and
   `telemetry`. Recorded in the README and shown on screen.
2. `make registry` lists both packs with namespaced capabilities.
3. A TA case is detected, banded, routed and audited, its trace opening in the same Cloud Trace view
   as a NAV case with the **same root-span attribute keys**. One TA capability deliberately has no
   authorised investigator and the registry reports **NONE** rather than routing it — shot 10's
   governance beat, at no extra cost.
4. `make eval` reports both processes side by side.

### S10 — Per-tenant policy · 2h · S

A `tenants/{tenant_id}` record carries thresholds per process, the approved agent set and capability
scope; `policies.band_for` resolves from it. Firestore collections are tenant-prefixed.

**Correction to v3.2:** it claimed this retires the overstated least-privilege defect. It does not.
`bootstrap.sh` calls `gcloud projects add-iam-policy-binding … --condition None`, which is a
*project-level* grant; a collection prefix narrows it by nothing. The defect stays open and the
README's status row stays until S7 addresses IAM. v3.2 asserting otherwise is the same class of
misstatement that rejected v2.

**Acceptance:** two tenants with different thresholds route the same case to different approval
classes, asserted by test; `control_plane` resolves the band from the tenant record itself; a case
whose declared band sits below the tenant floor is rejected.

### Figure 5 — the seam · 1.5h · M

One control plane, two process packs, and the line between them. Also states plainly what the
telemetry namespace is: every span attribute is `nav.*` because that is the *product's* name, not
the process's. A judge invited to check the seam will otherwise open a transfer-agency trace and
read `nav.case.impact_bps`.

---

## 5c. The 240 seconds

The plan argued about what a four-minute video can carry without ever writing the four minutes
down. This is that budget. It is the instrument that decides S9's scope, because on-camera time is
the binding constraint on demo-driven scope, not build hours.

| # | Shot | s | Why it cannot be cut |
| :--- | :--- | :--- | :--- |
| 1 | The NAV window, the breaks, what it costs a fund accountant | 20 | The brief requires problem and value |
| 2 | Cycle triggered; detection opens exceptions from tolerance rules | 20 | Shows the deterministic floor |
| 3 | Triage classifies, then **discovers** the specialist through the registry | 15 | The registry claim, live |
| 4 | FX investigator resolves the stale rate against real ECB data, cites the rate **and its date** | 35 | The 40% axis. This is the fleet doing the work |
| 5 | Corporate actions ingests a filing; **Model Armor blocks the poisoned notice**; the extractor takes only typed values | 35 | The strongest architectural asset |
| 6 | Remediation drafts a multi-leg correction; the gateway **refuses to post it** | 25 | The governance thesis in one beat |
| 7 | Sign-off statement foots; residual 0.00 | 20 | Definition of done, uncheatable |
| 8 | **Cloud Run, Cloud Trace, the case's reasoning chain in the Console** | 25 | Mandatory. Non-negotiable |
| 9 | Architecture: the control plane band, then the seam | 20 | The 30% axis |
| 10 | A second process on the same control plane — `git diff --stat`, `make registry`, `make eval` | 15 | §5b's whole claim, in three artefacts |
| 11 | Reproducibility: `make verify`, the eval numbers | 10 | The 30% axis |
| | **Total** | **240** | |

### What this settles

**S9 gets 15 seconds, and they are all artefacts rather than a walkthrough.** The three items in
shot 10 — a diff, a registry listing, a two-row eval table — need a second process that *runs*. They
do not need a Gemini investigator for it, and they do not need a second prompt-injection
demonstration: nobody shows two, and at the 10 seconds a TA investigator would get, no viewer can
tell whether its evidence was external.

So the registrar cassette and the TA investigator buy **zero incremental screen time** for 2.5h of
the 7h. v3.3 added them to stop the TA agent "reading on camera as the weak agent"; the shot list
shows it never appears on camera at all. That was scope defended by argument rather than measured.

**S9 is re-cut to 4.0h** (§5b), and the honest architectural point improves rather than weakens:
transfer agency's `subscription_in_transit` correction is *arithmetic* — add the in-transit shares.
It needs no model. A fleet that uses judgement where judgement is required and deterministic logic
where it is not is a better claim than one that puts an LLM on every step.

Shot 10 also absorbs the S1.5 governance beat at no extra cost: TA declares a capability with no
authorised investigator, and the registry reports **NONE** rather than silently routing it.

---

## 6. Acceptance criteria

| Section | Criterion |
| :--- | :--- |
| S0a | The Vertex response's returned model version string appears in a Cloud Trace span attribute, **asserted by a test**, for both model IDs. |
| S0-R | `Σ golden == −control_total`; `market_value_base == q × p ÷ fx_rate` per row; withholding any break makes `is_complete()` False **and** the residual equals that break to the cent; all four exploits raise — forged manifest, swapped callable, unresolvable approval ref, and tool call with no identity bound. |
| S0-R.7 | Marked `-m live`, with a recorded window-verdict cassette for the offline suite: head/middle/tail at 20KB and 200KB in 1KB windows is roughly 400 sanitize calls per screen, which can neither sit in `make verify` (S0c and S8a require it offline) nor be run casually against the $150 credit. Windowed screening raises `ContentBlocked` for the injection at **head, middle and tail** of a genuine filing, at 20KB and at 200KB — the head and middle cases are unachievable without windowing, which is why v3.0's criterion could not be met by its own fix. Zero false positives across the clean windows of a real filing. A response with `EXECUTION_SKIPPED` or `invocation_result != SUCCESS` raises regardless of `match_state`. |
| S0-R.7 (extractor) | **Falsifiable, replacing v3.0's tautology** ("returns a valid record or escalates" admitted every outcome): extraction from the *poisoned* notice must yield the **same typed values** as the clean notice; and a value outside plausibility bounds, or disagreeing with books-and-records, must escalate the case rather than produce a proposal. |
| S1 | Every verdict cites ≥1 `EvidenceItem` with non-null `source_uri` **and** `retrieved_at`; an FX verdict must cite the ECB rate **and the rate date used**; a corporate-action verdict citing only `books_and_records` fails. An **AST-scan test** asserts no module under `agents/` imports `nav_sentinel.tools.*` directly. |
| S1.5 | Triage classifies ≥5 of 6, and any miss returns `UNCLASSIFIED` with confidence < 0.5, never a confident wrong category. Republishing a manifest changes routing **without a process restart**. The **pricing** adversarial case is triaged correctly and then refused by the registry as having no authorised investigator — and `test_every_break_category_has_an_authorised_investigator` is inverted to assert exactly that gap, rather than deleted. |
| S2 | Cycle 2 makes zero EDGAR/ECB fetches for a recurring break and ≥50% fewer gateway tool calls than cycle 1; both are span attributes and the delta is printed by `make eval`. |
| S4 | Posting denial holds for published manifests, a manifest mutated to `may_post_entries=true`, a caller-forged manifest, and an invented `human_approval_ref`. Every proposal balances **per currency**. |
| S5 | **Leg-level correction accuracy and root-cause accuracy** against the `expected_corrections` golden, reported **beside the heuristic baseline** on the same cases and on perturbed variants. `Σ fleet corrections == −control_total` holds to the cent as a closure invariant. 100% of deliberately-unclassifiable cases return `UNCLASSIFIED`. Accuracy is labelled **indicative at N=8**. |
| S7 | Traces from the **deployed** service appear in Cloud Trace; the service rejects unauthenticated requests; `make teardown` executes; `gcloud` preflight confirms the target project. The deployed artefact contains the **final** fleet including the remediation agent — v3.0 scheduled S7 before S4, so its criterion would have been met by a fleet missing the component carrying the 40% proof. |
| S8a | README executed verbatim in a fresh container, no `.env`, no network beyond Google Cloud, timed and recorded. `make verify` reproduces the eval numbers. **Break IDs are content-hashed, not `itertools.count`**, so "reproduces byte-identically" is achievable — v2 asserted this while accepting non-deterministic IDs as a minor. |

---

## 7. Calendar

Twelve build days at 8.5h. **Every day carries build work, including the last two** — v3.0 and v3.2
both claimed otherwise while their own tables disagreed. What protects the recording is that S7a
banked the mandatory Cloud Run proof on 23 August, and that the 29 August rehearsal precedes the
30 August take.

| Date | Day | Work | h |
| :--- | :--- | :--- | :--- |
| Aug 19 | Wed | S0a stack proof · **README honesty patch + LICENSE** · architecture diagram · S0-R.1 | 6.0 |
| Aug 20 | Thu | **S0-R.9 decouple** — before anything is authored in a vocabulary it changes | 6.0 |
| Aug 21 | Fri | S0-R.2 (incl. approvals slice) · S0-R.3 fixtures · S0-R.7 screening + extractor | 8.5 |
| Aug 22 | Sat | S0-R.4 · .5 · .6 · .8 · **S0-R.10 enforced seam** · S0c — **S0-R gate must close** | 8.5 |
| Aug 23 | Sun | S7a vertical slice — **mandatory Cloud Run proof banked** · Devpost skeleton, credit, social | 6.3 |
| Aug 24 | Mon | S1.1 contract · S1 FX investigator | 6.0 |
| Aug 25 | Tue | S1 corporate actions · S1.5 triage · S2a Firestore | 6.0 |
| Aug 26 | Wed | S3 orchestration · **S5a eval harness** · CI | 8.5 |
| Aug 27 | Thu | S4 remediation · S2 memory shim · **S10 per-tenant policy** | 8.5 |
| Aug 28 | Fri | **S9 transfer agency pack** · security pass | 8.5 |
| Aug 29 | Sat | S7 deployment · figure 5 · **S8a Devpost text** — **code freeze 18:00** · rehearsal | 8.5 |
| Aug 30 | Sun | **S5b metrics + baseline** · S8b record · **submit** | 7.0 |

Scheduled: **88.3h** across the twelve days. Build in §5 and §5b totals 76.1h, so 12.2h of in-line
rework sits in the calendar and **10.3h of the 22.5h reserve stays unallocated** — rework lands on
whichever gate produces findings. Gross commitment is 98.6h; 5.3h of the 19 August row is already
complete as of 18 August, so 93.3h remains against 102.0h of capacity. The 3h released by S9's
re-cut goes to **30 August**, which the v3.3 review showed was 1.5–2.5h over-committed while
carrying the eval metrics, the Devpost description, the recording and the submission.

**S4 → S5 now precede S7**, so the deployed artefact is the final fleet. A late S7 is tolerable precisely
because S7a banks the mandatory proof on 23 August.

No day exceeds 8.5h. v2 scheduled 15h on Aug 27 while criticising v1 for scheduling 14h.

---

## 8. Risk register

| Risk | Sev | Mitigation |
| :--- | :--- | :--- |
| Mandatory stack never executed; model IDs unverified | **High** | S0a on day 1, asserted by test |
| S0-R overruns and eats S1 | **High** | Gate must close 22 Aug per §7; §3 response taken that evening if not |
| Governance claims falsifiable in ten minutes | **High** | S0-R.1/.2 plus four exploit tests |
| Model Armor cannot stop contextual injection | **High** | Accepted as a property of the service; mitigated structurally by quarantined extraction, not by configuration |
| 40% axis unevidenced | **High** | `Σ fleet corrections == −control_total` + heuristic baseline; S4 promoted to mandatory |
| 8.6% margin at a demanding 8.5h/day rate | **High** | Response pre-decided in §3, observable trigger, checked daily at 18:00 |
| Eval N too small to quote | **Accepted** | The volume run is **cut**, so there is no denominator fix. Accuracy is reported on N=6 (+2 adversarial) and stated as **indicative only** — one miss is 16.7%. The defensible claims are leg-level structure and evidence citation, which a heuristic cannot produce at any N. |
| Live demo fails on the take | Med | 29 Aug rehearsal, pre-captured Console navigation, S7a's proof already banked |
| The platform claim is disprovable by grep today | **High** | S0-R.9 plus the AST test; S9 proves it by diff, not assertion |
| S9 and S10 crowd the final days | Med | Both off the critical path, at ladder rungs 2 and 3 |
| Solo, no redundancy, no rest day | Accepted | Stated in §3 rather than implied |

## 9. Note on the B2 figures

§1's B2 evidence (Σ golden −129,320.80, gap 5,208,800.00) does not reproduce exactly from the committed
fixtures — an independent recomputation gives −134,550.99 and 5,203,569.81. The control total
(5,338,120.80) reproduces exactly, and `total_liabilities_base` is 0.00 on both sides, so **B2's substance
is confirmed**: the unsettled buy has no contra leg. The drift is because `fixtures/generate.py` fetches
**live** ECB rates, so the golden file changes with the day it was generated — which is precisely the
reproducibility defect S0c fixes. All figures are to be restated against a fixture hash once the ECB
cassette is committed.

## 10. Waived minors

Accepted with reasons, not silently dropped: `_version_key` ranks `2.0.0-rc1` above `2.0.0` (no RC versions published); `csam_filter_result` is the wrong proto field name (affects reporting only, not blocking); unreachable `nav_per_share_impact_bps`; `Severity`, `ExceptionStatus`, `Sla`, `EvidenceItem` declared and partly unconsumed; `books_and_records.parents[3]` breaks in a non-editable install; 30-char service-account truncation could collide for two agents sharing a prefix. Everything v2 waived that falsified a headline claim has been promoted into S0-R.8.

## 11. Section log

| Section | Plan review | Build | Code review | Closed |
| :--- | :--- | :--- | :--- | :--- |
| Plan v1 | APPROVE WITH CHANGES | — | — | superseded |
| S0 | n/a — built before any plan existed | done | **REJECT** | no |
| Plan v2 | **REJECT** | — | — | superseded |
| Plan v3.0 | **APPROVE WITH CHANGES** | — | — | superseded |
| Plan v3.1 | changes applied | — | — | superseded |
| S0a | covered by v3 review | done | **APPROVE WITH CHANGES** — applied | **yes** |
| S0-R.1 | covered by v3 review | done | **APPROVE WITH CHANGES** — applied | **yes** |
| Plan v3.2 | **APPROVE WITH CHANGES** — 3 blockers | — | — | superseded |
| Plan v3.3 | **APPROVE WITH CHANGES** — 3 blockers | — | — | superseded |
| Plan v3.4 | **in progress** | — | — | — |
| S0-R.9 + .10 seam | covered by v3.2–v3.4 reviews | done | **APPROVE WITH CHANGES** — applied | **yes** |
| S0-R.2 identity + approvals | covered by plan reviews | done | pending | — |
| S0-R.3–.6 fixtures + closure | covered by plan reviews | done | pending | — |
| S0-R.7 windowed screening + extractor | pending | — | — | — |
