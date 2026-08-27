# Narration

Spoken text only, one block per shot. Measured, not estimated: `make narration` renders it with
`say` and times it, because a word count is a proxy for duration that assumes the very thing nobody
knows until a voice has read the script.

## Where the four minutes go

Measured end to end, with the shot pauses in the file:

| Speaking rate | Speech | + 8 pauses | Total | Against the 240s cap |
|---|---|---|---|---|
| ~157 wpm (system default) | 198s | 20s | **218s** | 22s spare |
| 140 wpm (a slow, deliberate read) | 220s | 20s | **240s** | none |

So the script fits comfortably at a normal narration pace and **does not fit at 140 wpm**. Two
levers if the voice you pick reads slowly: drop the inter-shot pause from 2.5s to 1.5s, which buys
8s, and cut shot 7 — it is 77 words for a point the address bar has already been making for three
minutes.

Only the first four minutes are evaluated, so overrunning does not truncate the video, it discards
whatever is at the end. Re-measure after any edit:

    make narration                    # default rate
    make narration RATE=140           # the slow end

Shot 5 is the longest at 110 words and 43–48 seconds. It earns it: it is four refusals and a grant,
and it is the centre of the submission.

## 1 · The problem — 0:00–0:32 · 78 words

> A fund publishes a price for itself every day. Investors deal at that price, so a wrong one means
> compensating them and explaining yourself to a regulator.
>
> Every morning the books disagree with the custodian's somewhere, and someone must find out why
> before the deadline. I sign in with Google. This is that desk: seven differences today, and
> the column on the right is who is legally required to sign.

*Silence: 3s on the queue.*

---

## 2 · The stack — 0:35–0:58 · 68 words

> Eight agents on Google's Agent Development Kit version two, running Gemini three-point-seven Flash
> for reasoning and three-point-five Flash Lite for classification, on Vertex AI.
>
> None is named in the application code — each is discovered from the registry by the capability it
> declares. Four capabilities have no authorised agent, so a break classified as one of those is
> refused at routing. No agent runs. It stays in the queue as human work.

*On screen while this is said: the Fleet page, `gemini-3.7-flash` visible per agent, and the routing
table showing `NO PUBLISHED AGENT` against four capabilities.*

**Say four, not seven.** The page reports fourteen declared capabilities: seven routed, four with
nobody published to handle them, and three `.unclassified` sentinels — the value triage returns when
no root-cause family fits, which must never have an agent. Those three are not gaps, and the Fleet
page labels them separately so the screen and the narration agree.

**Why this line is in the video at all.** It is the difference between this fleet and a demo. The
tempting alternative is to hand an unroutable break to whichever agent looks closest, which returns
a confident, wrong root cause with real citations attached — and an audit trail saying a specialist
established it. Refusing is the harder behaviour and the only defensible one.

---

## 3 · Before any model — 0:55–1:18 · 55 words

> Open one. Before a single model call: quantity agrees, price agrees, market value differs by
> eighty-six thousand euros — and the exchange rate applied differs.
>
> That's arithmetic. A model here would be spending a request to be told what the numbers already
> say.

---

## 4 · The fleet works — 1:18–2:05 · 84 words

> Now the agents.

*Silence: 12–20s while it actually runs. Do not cut this — a visible wait is evidence.*

> Triage classified it, the registry chose the agent authorised for that capability, and that
> agent investigated using only the tools its manifest allows.
>
> It found the stale rate and cited the European Central Bank data it read, with a digest of the
> response — so the citation can be checked, not trusted. The correction balances: two legs, residual
> zero.
>
---

## 5 · Where it says no — 2:05–2:45 · 96 words · **the centre of the video**

> I'm signed in with Google; this deployment has me down as a controller. Approve.
>
> The desk won't even offer it — only the chief investment officer can clear this one, and the
> button says so instead of letting me try.
>
> Now a four-eyes case. Signed. Refused again — four eyes means two *different* people.
>
> Second account, the CIO. Granted.
>
> And now the part that matters. Cleared for posting &mdash; and no agent in this system can post
> it. That was checked, not claimed: the gateway was asked to post it under an agent's identity,
> holding this signature, and refused. An approval authorises a correction; it doesn't grant
> anything the authority to make it.

---

## 6 · Multi-week, multi-department — 2:45–3:05 · 71 words

> A published error runs for weeks. Fund accounting quantifies it. Transfer agency is asked,
> through the gateway and under its own identity, who dealt at the wrong price. A repeat is judged
> more harshly than a first.
>
> Three departments, twenty-eight business days. A payment file arrived before approval: refused, and
> recorded. The wall clock is compressed; the business dates are not.

---

## 7 · On Google Cloud — 3:05–3:38 · 74 words · **required**

> This runs on Cloud Run, in us-central1, as its own service account.
>
> Sign-in is public; the endpoints that do work are not. Asking it to run a reconciliation without
> a session: four oh one. And it reports it's persisting to Firestore, not memory &mdash; a service
> holding its audit trail in memory looks identical to a healthy one from outside.
>
> Here are the stage transitions and policy decisions in Firestore, and the traces, one per
> delivered event.

*Fire the traffic before this shot — Cloud Trace indexes with about forty-five seconds of lag.*

---

## 8 · Close — 3:38–3:50 · 48 words

> A second business process — a share register, in units instead of currency — cost five lines and
> no change to the registry.
>
> The agents never get the authority. They gather evidence, they propose, and a person signs.

---

## Check the length before you record

Do not trust a word count written by hand — the first draft of this file claimed 558 words and
measured 581, which with the silences ran to 4:22 and would have cut the required Google Cloud shot.

    awk '/^> /{gsub(/^> /,""); n+=split($0,a," ")} END{print n, "words →", int(n/150*60), "s at 150 wpm"}' \
      docs/submission/narration.md

Add roughly 25 seconds of silence on top for the run-the-fleet wait and page loads. If your voice
runs faster than 155 wpm, put the slack into shot 4's wait rather than adding words — the visible
wait is what proves it isn't a mockup.
