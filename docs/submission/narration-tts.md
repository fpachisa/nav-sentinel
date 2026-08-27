# Narration with style tags — paste into Google / Gemini TTS

Same words as [narration.md](narration.md), with bracketed style tags for a TTS voice. A test
asserts the two files say exactly the same thing once the tags are stripped, so this cannot quietly
drift from the script that gets measured and recorded.

**How to use it.** Paste one shot at a time and keep the audio files separate — you need them
separate anyway to line each one up against its own footage, and one long generation gives you no
way to retime a single shot without redoing all of it. The tags are hints about delivery, not
words: they should never be audible.

**Tone to aim for:** a practitioner explaining a system to people who will look for the holes in it.
Measured, unhurried, no salesmanship. The refusals in shot 5 are the only place to lift the energy,
and even there the lift is precision rather than excitement — the system saying no is the product.

---

## Shot 1 · The problem

```
[serious] A fund publishes a price for itself every day. [explanation] Investors deal at that price, so a wrong one means compensating them and explaining yourself to a regulator.

[informative] Every morning the books disagree with the custodian's somewhere, and someone must find out why before the deadline. [neutral] I sign in with Google. [explanation] This is that desk: seven differences today, and the column on the right is who is legally required to sign.
```

## Shot 2 · The stack

```
[informative] Eight agents on Google's Agent Development Kit version two, running Gemini three-point-seven Flash for reasoning and three-point-five Flash Lite for classification, on Vertex AI.

[explanation] None is named in the application code — each is discovered from the registry by the capability it declares. [informative] Four capabilities have no authorised agent, so a break classified as one of those is refused at routing. [emphatic] No agent runs. [calm] It stays in the queue as human work.
```

## Shot 3 · Before any model

```
[instruction] Open one. [informative] Before a single model call: quantity agrees, price agrees, market value differs by eighty-six thousand euros — and the exchange rate applied differs.

[matter-of-fact] That's arithmetic. [explanation] A model here would be spending a request to be told what the numbers already say.
```

## Shot 4 · The fleet works

```
[neutral] Now the agents.

[explanation] Triage classified it, the registry chose the agent authorised for that capability, and that agent investigated using only the tools its manifest allows.

[informative] It found the stale rate and cited the European Central Bank data it read, with a digest of the response — [emphatic] so the citation can be checked, not trusted. [matter-of-fact] The correction balances: two legs, residual zero.
```

## Shot 5 · Where it says no — the centre of the video

```
[neutral] I'm signed in with Google; this deployment has me down as a controller. [instruction] Approve.

[emphatic] The desk won't even offer it — [explanation] only the chief investment officer can clear this one, and the button says so instead of letting me try.

[neutral] Now a four-eyes case. [instruction] Signed. [emphatic] Refused again — [explanation] four eyes means two different people.

[neutral] Second account, the CIO. [approval] Granted.

[serious] And now the part that matters. [informative] Cleared for posting — and no agent in this system can post it. [emphatic] That was checked, not claimed: [explanation] the gateway was asked to post it under an agent's identity, holding this signature, and refused. [serious] An approval authorises a correction; it doesn't grant anything the authority to make it.
```

## Shot 6 · Multi-week, multi-department

```
[informative] A published error runs for weeks. Fund accounting quantifies it. [explanation] Transfer agency is asked, through the gateway and under its own identity, who dealt at the wrong price. [serious] A repeat is judged more harshly than a first.

[emphatic] Three departments, twenty-eight business days. [serious] A payment file arrived before approval: refused, and recorded. [matter-of-fact] The wall clock is compressed; the business dates are not.
```

## Shot 7 · On Google Cloud

```
[informative] This runs on Cloud Run, in us-central1, as its own service account.

[explanation] Sign-in is public; the endpoints that do work are not. [matter-of-fact] Asking it to run a reconciliation without a session: four oh one. [informative] And it reports it's persisting to Firestore, not memory — [serious] a service holding its audit trail in memory looks identical to a healthy one from outside.

[neutral] Here are the stage transitions and policy decisions in Firestore, and the traces, one per delivered event.
```

## Shot 8 · Close

```
[informative] A second business process — a share register, in units instead of currency — cost five lines and no change to the registry.

[serious] The agents never get the authority. [calm] They gather evidence, they propose, and a person signs.
```

---

## If your voice reads slowly

`make narration RATE=140` measures the slow end. At 140 words per minute the script lands on the
240-second cap with nothing to spare, so if the voice you pick is deliberate, take the slack from:

1. **The inter-shot pauses** — 2.5s × 8 is 20s. At 1.5s it is 12s.
2. **Shot 7** — 74 words for a point the `.run.app` address bar has been making since shot 1. The
   401 and the Firestore line are the parts that earn their place; the opening sentence does not.

Do not take it from shot 5. It is four refusals and a grant, and it is the reason the submission
exists.
