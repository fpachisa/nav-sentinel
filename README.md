# NAV Sentinel

A governed fleet of fund-accounting agents that clears reconciliation exceptions inside the
NAV production window.

**Track C — The Fortified Enterprise Fleet** · All Things Agentic Hackathon

---

## The problem

Every day, every fund is reconciled twice: once by the fund accountant and once by the
custodian. When the two books disagree, someone has to explain the difference before the NAV
can be struck — and the window is measured in hours. The work is skilled, repetitive and
almost entirely manual: pull the break, guess the cause, chase the evidence across a price
feed, an FX table, a corporate-action notice and a trade blotter, then write the correcting
entry and find someone senior enough to approve it.

Agents can do most of this. The reason they don't is governance. No fund administrator will
let an autonomous process near a NAV without cryptographic identity, enforced separation of
duties, screening of anything ingested from outside, and an audit trail that survives a
regulator asking *why*.

So this project builds both halves: the fleet that does the work, and the control plane that
makes it deployable.

## What it does

1. **Detects** breaks between the accounting and custodian books using deterministic
   tolerance rules — no model, because deciding whether two numbers differ is arithmetic.
2. **Triages** each break, computes its NAV impact in basis points, and asks the **Agent
   Registry** which specialist is authorised to investigate that root-cause family.
3. **Investigates** in parallel via five specialists — corporate actions, FX and rates,
   pricing, settlement, cash and fees — each with its own identity, its own read-only tool
   allowlist, and evidence cited from authoritative sources.
4. **Drafts** a balanced correcting entry with the full evidence chain attached.
5. **Routes** for approval by materiality. Nothing posts autonomously, at any size.

### Definition of done is arithmetic, not assertion

A NAV difference is not another break to investigate — it is the **control total**. Every
other break is a candidate explanation for it. The cycle is complete only when the signed sum
of explained cases equals the NAV difference and the residual is zero:

```
MERID-GEF  control total  EUR 5,338,120.80
           explained       EUR 5,338,120.80   (5 cases)
           residual        EUR        -0.00   complete
```

A model cannot declare victory here. The arithmetic either closes or it does not.

## Architecture

| Brief requirement | Google Cloud component | What we designed on top |
| :--- | :--- | :--- |
| Agent Registry | Agent Registry | Versioned YAML manifests declaring capability, tool allowlist, data scopes and authority. Triage resolves specialists by capability at runtime, so adding one is a publish, not a code change. |
| Agent Runtime | Agent Runtime / Cloud Run | Long-running investigations dispatched asynchronously over Pub/Sub. |
| Memory Bank | Memory Bank | Per-fund, per-security recurrence memory across NAV cycles, so a break seen last month is recognised rather than re-investigated. |
| Agent Identity | Agent Identity + IAM | One service account per agent, minted from its manifest. No shared fleet identity, no ambient authority. |
| Agent Gateway | Agent Gateway | Intended single enforcement point for five policies (below). **Under remediation — see Known defects.** |
| Model Armor | Model Armor | Screens external content before it reaches a model context. **Under remediation — the current implementation has a verified bypass; see Known defects.** |
| Observability | Cloud Trace via OTLP | One trace per exception case. The reasoning chain *is* the audit artefact. |

**Model:** `gemini-3.7-flash` on Vertex AI for investigation and drafting;
`gemini-3.5-flash-lite` for triage classification, which every break passes through and is
where token spend would otherwise run away.

> Note on the brief: it specifies "Gemini 3.5 or newer". Gemini 3.5 Pro had not reached
> general availability as of August 2026 — it missed its I/O target. We satisfy the
> requirement with Gemini 3.7 Flash, which is GA and better suited to agentic workloads.

### The five enforced policies

| ID | Policy |
| :--- | :--- |
| `P-001-TOOL-ALLOWLIST` | An agent may call only the tools declared in its registry manifest. Grants cannot be made at runtime. |
| `P-002-DRAFT-AUTHORITY` | Only the remediation agent may draft an accounting entry. Investigators report root causes. |
| `P-003-NO-AUTONOMOUS-POSTING` | Nothing posts to the ledger without a recorded human approval. No agent holds posting authority at any materiality. |
| `P-004-MATERIALITY-ROUTING` | Basis points of NAV determine who signs off: auto-clear, single reviewer, four eyes, or CIO escalation. Computed, never inferred. |
| `P-005-UNTRUSTED-INGEST` | An agent that reads the public internet cannot opt out of Model Armor screening. |

### Why the governance is load-bearing, not decorative

The corporate-actions investigator reads issuer filings from the public internet. That content is
authored by someone else and lands directly in a model's context, which makes it a genuine
prompt-injection surface rather than a hypothetical one. `fixtures/data/` contains a poisoned
corporate-action notice instructing the agent to enable posting authority, skip the audit log and
exfiltrate the investor register.

**Model Armor blocks that notice in isolation and does not reliably block it inside a real document.**
Measured against the live service, with the 1,008-byte injection held constant:

| Injection position in a 19,662-byte filing | Whole-document screen |
| :--- | :--- |
| head | missed |
| middle | missed |
| tail | caught |

There is also a size cliff between 40,827 and 41,329 bytes, above which the prompt-injection filter
returns `execution_state: EXECUTION_SKIPPED` and `invocation_result: PARTIAL` while
`filter_match_state` still reads `NO_MATCH_FOUND` — so code checking only the match state, as this
code currently does, cannot distinguish a skipped scan from a clean one.

Screening in 1KB windows with 500-byte overlap catches the injection at every position tested, with no
false positives. That fix is in progress, together with a quarantined extractor so that untrusted prose
never enters a privileged context at all. See [docs/PLAN.md](docs/PLAN.md) §1.

## Data

Real public sources provide the external truth an investigator checks against:

- **ECB euro foreign-exchange reference rates** — daily, authoritative, and absent on
  weekends and TARGET holidays, which is itself the cause of a large share of real FX breaks.
- **SEC EDGAR** — issuer filings and corporate-action announcements.

The books and records are synthetic: two funds, twelve holdings, and nine deliberately
seeded breaks spanning all five root-cause families. Each seeded break records its expected
category and exact correction in `eval/golden_breaks.yaml`, which is what allows root-cause
accuracy to be scored without a model grading another model.

No client, proprietary or personal data is used anywhere in this project.

## Spin-up

### Prerequisites

- Python 3.12+, [`uv`](https://docs.astral.sh/uv/), and the `gcloud` CLI
- A Google Cloud project with billing enabled

### 1. Install

```bash
git clone <repository-url> && cd nav-sentinel
make venv
cp .env.example .env      # then set GOOGLE_CLOUD_PROJECT
```

### 2. Provision Google Cloud

```bash
make bootstrap PROJECT=your-project-id REGION=us-central1
```

This enables the required APIs, creates the Model Armor template, mints one service account
per registry manifest (see Known defects on the scope of those roles), and creates the Pub/Sub topic and Firestore
database. It is idempotent — re-run it freely.

> **Gotcha worth knowing:** Model Armor is only reachable on its *regional* endpoint. Calls to
> the global endpoint return `PERMISSION_DENIED` even for a project owner, which is a
> misleading error. `infra/bootstrap.sh` sets the override for you.

### 3. Generate the books and records

```bash
make fixtures
```

Fetches live ECB reference rates — **network required** — and writes the synthetic books plus
`eval/golden_breaks.yaml`.

### 4. Verify

```bash
make test        # 32 invariant tests, including "no agent may post"
make registry    # the published fleet and its coverage
```

### 5. Run a NAV cycle

```bash
make demo      # NOT YET IMPLEMENTED -- see Known defects
```

## Repository layout

```
src/nav_sentinel/
  domain/          Deterministic core: tolerance rules, materiality, the NAV control total
  registry/        Agent Registry: manifests, capability discovery, publication
  control_plane/   Gateway, identity, policies, Model Armor, telemetry, case audit spans
  agents/          The fleet: detection, triage, five investigators, remediation, approval
  tools/           Scoped tools: ECB rates, EDGAR, books and records
  pipeline/         Async orchestration over Pub/Sub
fixtures/          Synthetic book generator and poisoned-document fixtures
eval/              Golden root causes for scoring
infra/             Idempotent Google Cloud provisioning
tests/             Invariants of the reconciliation core and the control plane
```

## Design decisions worth defending

**Deterministic where determinism is correct.** Break detection, materiality scoring and
approval routing contain no model calls. Whether two numbers differ, and who is permitted to
clear a difference of a given size, must be reproducible and testable. Models are reserved
for explaining *why* — which is the part that genuinely needs judgement.

**Enforcement outside the agents.** An agent that checks its own permissions is one prompt
away from deciding it has them. Every policy decision is made by the gateway, from the
registry manifest, and recorded on the trace.

**Traces designed for auditors.** One trace per exception case, carrying the version-pinned
agent reference, every policy decision, every piece of evidence and the materiality that drove
routing. Telemetry built for an auditor happens to be excellent telemetry for an engineer;
the reverse is not reliably true.

**Positions aggregate.** A fund holds securities across multiple lots. Keying position rows
into a map silently discards lots and *under-reports* breaks — the worst failure mode a
reconciliation engine has. `tests/test_reconciliation.py` pins this.

## Status

Built and verified against live Google Cloud:

Each claim below names the test or artefact that evidences it. Items under remediation are listed as
such rather than as complete.

| Component | State | Evidence |
| :--- | :--- | :--- |
| Deterministic reconciliation core | works, with a known gap | `tests/test_reconciliation.py` (32 tests). The NAV control-total closure is **circular** — see [docs/PLAN.md](docs/PLAN.md) §1 |
| Agent Registry, capability discovery | works | `tests/test_governance.py::TestRegistry` |
| Per-agent identity from manifests | works | `infra/bootstrap.sh`, `tests/test_governance.py` |
| OpenTelemetry case traces → Cloud Trace | works | trace `7de855f4…` read back from Cloud Trace |
| Agent Gateway policy enforcement | **under remediation** | P-001 is a string check on a label and P-002/P-003/P-005 trust a caller-supplied manifest; both are bypassable. No test covers the bypass |
| Model Armor screening | **under remediation** | Verified bypass, above. **No test in the suite currently touches Model Armor** |
| Least-privilege IAM | **overstated** | `bootstrap.sh` grants *project-level* `roles/datastore.user`; scope enforcement lives in the gateway, not IAM |
| ADK investigator agents on Gemini | not started | no `google.adk` reference exists in `src/` yet |
| Memory Bank recurrence recall | not started | — |
| Pub/Sub async orchestration | not started | `make demo` does not run |
| Cloud Run deployment | not started | — |
| Evaluation harness | not started | — |

### Known defects

Recorded openly because this repository is public and the claims above were previously overstated. Full
detail, reproductions and remediation plan in [docs/PLAN.md](docs/PLAN.md).

1. **Tool allowlist bypass.** `gateway.call_tool(name, fn)` validates the *name* and executes the
   supplied *callable*, so any function can run under a declared tool's label — and the audit log
   records the declared name, actively falsifying the trail.
2. **Confused deputy.** `authorize_drafting`, `authorize_posting` and `admit_untrusted_content` take the
   acting manifest as an argument instead of resolving it from the bound identity, so a forged manifest
   escalates to posting authority. `human_approval_ref` is an unvalidated string.
3. **Model Armor bypass.** As described above.
4. **Fixtures violate double entry.** Trade-date recognitions are booked without a contra cash leg, so
   the declared ground truth explains only a fraction of the NAV difference.
5. **The control total is blind to the FX chain.** Corrupting every `fx_rate` in the accounting book
   leaves all 32 tests passing, because `market_value_base` is a stored field nothing recomputes.
6. `make demo` fails (`ModuleNotFoundError`); `make lint` fails (ruff not installed); `make fixtures` and
   one test require live network access to the ECB.

## Licence

MIT — see [LICENSE](LICENSE).
