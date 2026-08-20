# S7a evidence — the fleet running on Cloud Run

Captured 20 August 2026 against project `all-things-agentic-hack-fp`, service `nav-sentinel`,
region `us-central1`, revision `nav-sentinel-00006-4ps`.

Reproduce with `make deploy`, then hit `/selftest` and `/cycle/2026-08-17` with an identity token.

## What the slice proves

| S7a requirement | Evidence |
| :--- | :--- |
| Pub/Sub **push** → Cloud Run | `POST /pubsub/exceptions` → 204, Cloud Run request log shows `userAgent: APIs-Google`, latency 0.61s |
| Runs under a dedicated service account | `nav-runtime@all-things-agentic-hack-fp.iam.gserviceaccount.com`; the span below carries the *acting agent's* SA |
| Not publicly invokable | Anonymous `GET /readyz` → **403** from Cloud Run IAM; authenticated → 200 |
| Gateway polices the path | `/cycle/2026-08-17` → **28 policy decisions** recorded for 7 cases |
| Vertex Gemini reachable from Cloud Run | `/selftest` → `gemini-3.7-flash` returned, `location: global`, `transport: vertex-ai`, `google-adk 2.7.1` |
| Model Armor reachable **and denying** | `/selftest` → benign admitted, injection refused with `ContentBlocked`, endpoint `modelarmor.us-central1.rep.googleapis.com` |
| Span in Cloud Trace from the **deployed** service | trace `0a5d058b…` (5 spans, the ADK call tree) and per-case traces (below) |
| Cycle closes | `control_total −4529562.69`, matching the generator's asserted closure figure |

## A case trace, as a reviewer sees it

Trace `90e6c52e3024cf937c7d3db5ced507c1`:

```
nav_sentinel.exception_case
    nav.case.approval_class  = four_eyes
    nav.case.impact_value    = 4.7492      nav.case.impact_unit = bps
    nav.case.recurrence_key  = MERID-GEF:security:US0378331005
    nav.case.severity        = medium      nav.case.status = open
  └─ gateway.tool_call
       nav.agent.ref             = triage-agent@2.0.0
       nav.agent.service_account = nav-triage-agent@…iam.gserviceaccount.com
       nav.tool.name             = registry.coverage
       nav.tool.reads            = ["registry"]
       nav.tool.untrusted_output = false
```

The governance decision, the acting agent, **that agent's own service account**, and the tool's
declared data reach are all on the span. This is the shot the video needs.

## Two things the deployment taught that no local test could

**`/healthz` is unusable on Cloud Run.** The Google Frontend answers it before the container sees
it: `/healthz` returns Google's own HTML 404 while `/health`, `/livez` and `/readyz` all reach
FastAPI. The conventional name passed every local test and was dead in production. Liveness is now
`/health`.

**Spans were never leaving the container.** Cloud Run throttles CPU to near zero the moment a
response is sent, so `BatchSpanProcessor`'s delayed flush had nothing to run on — the push returned
at 02:02:28 and the exporter failed at 02:02:41 with `DEADLINE_EXCEEDED`, thirteen seconds later.
Spans are now flushed inside the request.

## Note for the video

**Cloud Trace takes roughly 45 seconds to index a new trace.** A first lookup returned
`404 Trace not found` and the same id resolved fine on retry. The recording must not cut straight
from the API call to the Console, or it will show an empty trace view.
