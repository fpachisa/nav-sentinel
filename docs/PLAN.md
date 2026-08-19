# NAV Sentinel — Delivery Plan v3

**Deadline:** 31 Aug 2026, 17:00 PDT · **Plan date:** 18 Aug 2026 · **Remaining:** 12 days, both weekends worked
**Track:** C — Fortified Enterprise Fleet · **Entrant:** individual
**Supersedes:** v3.1 (approved, changes applied), v2 (REJECTED), v1 (APPROVE WITH CHANGES)

---

## 1. What the three gates established

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
| B4 | Control total blind to the FX chain | every `fx_rate` set to `999999.99999999` → **32/32 tests green** |

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
| Build — §5 | 63.1 |
| Build — §5b platform sections (S0-R.9, S9, S10, figure 5) | 11.0 |
| Rework reserve — 9 code gates × 2.5h | 22.5 |
| **Commitment** | **96.6** |
| Already complete ahead of the calendar — S0a, S0-R.1, README patch, LICENSE, figures 1–4 | −5.3 |
| **Remaining** | **91.3** |
| Capacity — 12 days, both weekends, ~8h/day | 96.0 |
| **Margin** | **4.7h (4.9%)** |

§5b adds 11h of build and 5h of rework, against an explicit decision that build time is not the
binding constraint here. The margin survives only because day one's allocation was completed
ahead of the calendar; it is not slack won back by estimating better.

Of the 22.5h reserve, **9.2h is scheduled in-line** in §7 and **13.3h is deliberately unallocated**
— rework lands on whichever gate produces findings, and pretending to know which day would be false
precision.

**2.5h per gate is a budget fitted to capacity, not an estimate derived from history — and the
distinction matters.** The four gates so far produced 12 blockers and 41 majors at roughly 10h of
re-planning each. At that rate **two gates exhaust the entire 17.5h reserve and the 3.7h margin with it.**
The reserve is a bet that remediating narrower, already-planned sections costs a quarter of what
re-planning the whole project cost. That bet may lose, which is what §3's ordered response is for.

The seven code gates are: S0-R · S7a · S1 · S2a · S3 · S4+S5 · S7.

**The margin is thin and it is stated rather than hidden.** One further discovery of S0-R's magnitude
breaks this plan. The pre-decided response, in order — **cheapest and least-scoring first, the 40% axis
last**: (1) CI dropped (0.5h); (2) S10 per-tenant policy documented rather than built (2.0h); (3) S9
transfer agency reduced to one scenario (2.0h); (4) the second adversarial case dropped (0.5h);
(5) S2 memory degraded to a stub interface (1.0h); (6) S8b's rehearsal compressed (1.0h); (7) only
then S4 reduces to materiality routing with no drafting — which forfeits the 40% closure proof and is therefore the last resort, not
the first. v3.0 had this ordering inverted, sacrificing the heaviest-weighted axis before touching 2h of
should-have work.

**Trigger, observable:** at 18:00 each day, if cumulative build-hours variance exceeds 4h, or the day's scheduled items are not code-review-closed, the next response above is taken that evening.

**Working assumption, stated because it is load-bearing:** 12 consecutive days, no rest day, both
weekends, ~7h/day of *productive* build — which for solo work means roughly 9–10 elapsed hours daily.
**This is feasible full-time and impossible alongside employment.** If the days are evenings and
weekends rather than dedicated, the plan does not hold and scope must halve, not shrink.

There is also **no slack day.** v3.0 claimed the final two days carried no build work; that was false.
The 3.7h margin is the slack. What de-risks a late S7 is that **S7a banks the mandatory Cloud Run proof
on 23 August**, eight days before the deadline.

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
| S5 | Evaluation | 4.0 | M | **Headline metric: leg-level correction accuracy and root-cause accuracy** against S0-R.5's `expected_corrections` golden — a pass-through stub cannot fake either. `Σ fleet corrections == −control_total` is demoted to a **closure invariant**, because `signed_impact_base` already computes it deterministically and a stub returning its negation satisfies it with no model call. Heuristic baseline scored on the same golden **and on perturbed/ambiguous cases**, with the framing pre-committed before the number is seen. Two adversarial cases: one **pricing** break (correctly triaged, then refused by the registry for want of an authorised agent — this is what makes the three-investigator cut a demonstrated control rather than a rationalisation) and one where the **custodian** is the incorrect side. |
| S7 | Deployment | 6.0 | M | Cloud Run under per-agent SAs, IaC, `make teardown`, **budget alert**, ingress authentication (fund NAV data must not be publicly readable), Pub/Sub push OIDC, no secrets in image. Fix `bootstrap.sh` role-binding idempotency — it `continue`s on an existing SA and never reconciles roles, so "re-run it freely" is false. |
| S8a | Reproducibility + Devpost text | 3.5 | M | README verbatim in a clean container; **Devpost technical description** (mandatory, omitted from v1 and v2 — problem, features, technology inventory, data sources, findings/challenges/learnings); checklist mapped 1:1 with an evidence column. |
| S8b | Demo script, rehearsal, record | 3.5 | M | Script, **rehearsal before the take**, then record. Pre-captured Console screenshots are for navigation only and must never stand in for the agent run — the briefing requires a live, unedited demonstration. |
| — | Architecture diagram | 1.5 | M | Drafted **day 1**, so it can discipline the S3/S7 decisions it describes. Counted once (v2 double-counted it). |
| — | CI (GitHub Actions, `make verify` offline) | 0.5 | S | ~30 min once S0c makes the suite offline, and it continuously protects the "reproduces byte-identically" claim. The rubric names production-mindset explicitly. |
| — | Security pass on the deployed surface | 0.5 | M | Was scheduled inside S7 with no hours and no criterion. Ingress authentication, Pub/Sub push OIDC, no secrets in image. |
| — | README honesty patch + `LICENSE` | 0.25 | M | **19 August, not 30 August.** See below. |
| — | Devpost skeleton, credit application, `#AllThingsAgenticHackathon` post | 0.3 | M | Devpost permits post-submission editing, so an early skeleton converts a hard deadline into a soft one. |

### S8.0 — README honesty patch · 0.25h · **19 August** · M
Deferring this to the final day is not defensible: the repo is **public now**, the claims are false, and
**no test in the 32-test suite touches Model Armor at all**. The plan applies exactly this standard to
itself in S0-R.4 — relabel the EDGAR cassette because citing live EDGAR "would be dishonest" — so it
cannot exempt the one artefact judges actually read. The 15-minute patch lands 19 August; the full rewrite
stays in S8a. Every `[x]` in the Status section must name the test or trace that evidences it. The following claims are currently false and must be rewritten, not merely supplemented: "Agent Gateway enforcing all five policies" (B1/B3 defeat four of them), "Model Armor screening, verified blocking a real prompt injection" and "Screening is fail-closed" (§1), "least-privilege… only the declared scopes" (project-level `roles/datastore.user`), the MIT declaration with no `LICENSE` file, and README step 3's "fetches live ECB reference rates",
which contradicts S8a's offline criterion. Also delete the Gemini 3.5 aside — it reads as pre-emptive excuse-making; name the verified versions instead.

---

## 5b. The platform claim, and what makes it checkable

Track C asks for a *fleet*, not a workflow. NAV reconciliation is one process; the control plane
should host any reconciliation-shaped process, for any client. That is a strong selling point and
it is currently **false**. One grep disproves it:

```
control_plane/gateway.py   -> domain.models.ExceptionCase
control_plane/policies.py  -> domain.models.ApprovalClass, ExceptionCase
control_plane/audit.py     -> domain.models.ExceptionCase
registry/models.py         -> domain.models.BreakCategory
registry/discover.py       -> domain.models.BreakCategory
```

The control plane knows what a NAV break is, and the registry holds a closed enum of one domain's
categories, so it could never route for a second process. A fourth coupling shows in no import at
all: `materiality.py` scores everything in **basis points of NAV**, which is meaningless for a
process whose control total is denominated in shares.

Asserting extensibility in prose is worthless — every entrant will claim it. Two things make it
evidence instead.

**An AST test.** `control_plane/` and `registry/` must contain no import from any process package.
Same technique as the tool-import scan in S1. It turns "domain-agnostic" from a sentence into a
property the build enforces, and it fails the moment anyone re-couples them.

**A second process, thin.** The demonstration is not that transfer agency works; it is that adding
it required **zero changes** to the gateway, the policies, the registry or the telemetry. That is
verifiable from the diff, which is the only form of this claim a judge should believe.

### S0-R.9 — Decouple the control plane · 3h · M

| Concept | Why it leaks | Resolution |
| :--- | :--- | :--- |
| `ApprovalClass` | Four-eyes and escalation are governance vocabulary, not fund accounting | Moves **into** `control_plane`; processes import it |
| `ExceptionCase` | The gateway reads only an id, a trace id, an approval class and a display impact | Replaced by a `GovernedCase` **Protocol** — the control plane states its requirements, processes satisfy them |
| `BreakCategory` | A closed enum of one domain's categories cannot route a second | `handles_capabilities: list[str]`, namespaced per process (`nav.fx_rate`, `ta.subscription_in_transit`) |
| bps-of-NAV materiality | Units differ per process | The control plane enforces on the **approval band**; each process computes its band from its own units and the tenant's thresholds. The raw figure rides along for the audit record only. |

A `ProcessPack` becomes the unit of extension — a key, a capability namespace, its tools, its
manifests, and an impact scorer. The tool catalogue and the registry are assembled from the
registered packs rather than from one hardcoded tuple.

**Acceptance:** the AST test passes; `ApprovalClass` is defined in `control_plane`; every capability
string is namespaced; no policy function accepts a fund-accounting type.

### S9 — Transfer agency process pack · 5h · S

Shares in issue per the registrar against per fund accounting. Capabilities
`ta.subscription_in_transit`, `ta.redemption_unprocessed`, `ta.switch_not_booked`. One investigator,
two seeded scenarios, contra legs from the start — B2 is the lesson.

The control total is denominated in **shares**, which is the point: it proves the impact scorer is
not covertly tied to basis points, and it exercises the closure test in a second unit.

```
control total     +12,500 shares
less corrections  −12,500 shares
                  ──────────────
residual                       0
```

**Acceptance:** the pack adds no line to `control_plane/`, `registry/` or `telemetry`; the same
governance log and the same closure assertion run over it; `make eval` reports both processes side
by side.

### S10 — Per-tenant policy · 2h · S

Materiality thresholds live in global `Settings` today, which implies every client shares one risk
appetite. A `tenants/{tenant_id}` record carries thresholds per process, the approved agent set and
capability scope; policies resolve from it rather than from config, and Firestore collections are
tenant-prefixed.

This also retires a live honesty defect: `bootstrap.sh` grants project-level `roles/datastore.user`,
so the README's least-privilege claim is currently overstated.

**Acceptance:** two tenants with different thresholds route the same case to different approval
classes, asserted by test; no policy reads a threshold from `Settings`.

### Figure 5 — the seam · 1h · M

One control plane, two process packs, and the line between them. Added to `docs/architecture.html`.

---

## 6. Acceptance criteria

| Section | Criterion |
| :--- | :--- |
| S0a | The Vertex response's returned model version string appears in a Cloud Trace span attribute, **asserted by a test**, for both model IDs. |
| S0-R | `Σ golden == −control_total`; `market_value_base == q × p ÷ fx_rate` per row; withholding any break makes `is_complete()` False **and** the residual equals that break to the cent; all four exploits raise — forged manifest, swapped callable, unresolvable approval ref, and tool call with no identity bound. |
| S0-R.7 | Windowed screening raises `ContentBlocked` for the injection at **head, middle and tail** of a genuine filing, at 20KB and at 200KB — the head and middle cases are unachievable without windowing, which is why v3.0's criterion could not be met by its own fix. Zero false positives across the clean windows of a real filing. A response with `EXECUTION_SKIPPED` or `invocation_result != SUCCESS` raises regardless of `match_state`. |
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

Ten build days at ~7.3h, then two packaging days at ~4h. Build finishes **28 Aug**; the last two days carry no build work, so a failed take has room for a retake.

| Date | Day | Work | h |
| :--- | :--- | :--- | :--- |
| Aug 19 | Wed | S0a stack proof · **README honesty patch + LICENSE** · architecture diagram · S0-R.1 | 6.0 |
| Aug 20 | Thu | S0-R.2 (incl. approvals slice) · S0-R.3 fixtures | 6.0 |
| Aug 21 | Fri | S0-R.7 windowed screening + extractor · **S0-R.9 decouple** | 7.5 |
| Aug 22 | Sat | S0-R.4 · .5 · .6 · .8 · S0c — **S0-R gate must close** | 8.0 |
| Aug 23 | Sun | S7a vertical slice — **mandatory Cloud Run proof banked** · Devpost skeleton, credit, social | 6.3 |
| Aug 24 | Mon | S1.1 contract · S1 FX investigator | 6.0 |
| Aug 25 | Tue | S1 corporate actions · S1.5 triage · S2a Firestore | 6.0 |
| Aug 26 | Wed | S3 orchestration | 7.0 |
| Aug 27 | Thu | S4 remediation · S2 memory shim · **S10 per-tenant policy** | 8.0 |
| Aug 28 | Fri | **S9 transfer agency pack** · figure 5 · security pass | 7.5 |
| Aug 29 | Sat | S5 evaluation + baseline · S7 deployment — **code freeze** | 8.0 |
| Aug 30 | Sun | S8a reproducibility + Devpost · S8b rehearse and record · **submit** | 7.0 |

Scheduled: **83.3h** across the twelve days. Build in §5 and §5b totals 74.1h, so 9.2h of in-line rework sits in the calendar and **13.3h of the 22.5h reserve stays unallocated** — rework lands on whichever gate produces findings. Gross commitment is 96.6h; the 19–20 August rows are already complete as of 18 August, so 91.3h remains against 96.0h of capacity.

**S4 → S5 now precede S7**, so the deployed artefact is the final fleet. A late S7 is tolerable precisely
because S7a banks the mandatory proof on 23 August.

No day exceeds 8h. v2 scheduled 15h on Aug 27 while criticising v1 for scheduling 14h.

---

## 8. Risk register

| Risk | Sev | Mitigation |
| :--- | :--- | :--- |
| Mandatory stack never executed; model IDs unverified | **High** | S0a on day 1, asserted by test |
| S0-R overruns and eats S1 | **High** | Gate must close 21 Aug; §3 response taken that evening if not |
| Governance claims falsifiable in ten minutes | **High** | S0-R.1/.2 plus four exploit tests |
| Model Armor cannot stop contextual injection | **High** | Accepted as a property of the service; mitigated structurally by quarantined extraction, not by configuration |
| 40% axis unevidenced | **High** | `Σ fleet corrections == −control_total` + heuristic baseline; S4 promoted to mandatory |
| 4.4% margin | **High** | Response pre-decided in §3, observable trigger, checked daily at 18:00 |
| Eval N too small to quote | **Accepted** | The volume run is **cut**, so there is no denominator fix. Accuracy is reported on N=6 (+2 adversarial) and stated as **indicative only** — one miss is 16.7%. The defensible claims are leg-level structure and evidence citation, which a heuristic cannot produce at any N. |
| Live demo fails on the take | Med | 29 Aug rehearsal, pre-captured fallback, 30 Aug has no build work |
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
| Plan v3.2 | **in progress** | — | — | — |
| S0-R | pending | — | — | — |
