# Recording runbook

Order matters here. Two things have to happen *before* you press record, and one of them takes
forty-five seconds to become visible.

## Before you record

```bash
# 1. Traffic first — Cloud Trace indexes with ~45s of lag, so the traces you film must already exist.
URL=https://nav-sentinel-rwkxhtvoeq-uc.a.run.app
TOKEN=$(gcloud auth print-identity-token)
curl -s -H "Authorization: Bearer $TOKEN" "$URL/readyz"          # warms the instance too
gcloud pubsub topics publish nav-exceptions --message '{"as_of":"2026-08-17"}' \
  --project all-things-agentic-hack-fp

# 2. Clean opening state. Safe to re-run between takes; it never touches the audit trail.
NAV_REPOSITORY=firestore make demo-reset

```

Then wait a minute before filming shot 7 so the traces have indexed.

**Film everything on the deployed URL.**

    https://nav-sentinel-rwkxhtvoeq-uc.a.run.app/app

The service is public *ingress* with Google sign-in enforced by the application, so a browser reaches
the sign-in page and a real Google account signs in. Nothing needs to run locally, and the address
bar carries the `.run.app` URL through every shot — which is the required proof, obtained for free
rather than staged at the end.

**Use exactly that hostname.** Cloud Run publishes two for this service --
`nav-sentinel-rwkxhtvoeq-uc.a.run.app` and `nav-sentinel-523099900380.us-central1.run.app` -- and only
the first is a registered JavaScript origin on the OAuth client. On the other one Google's sign-in
button renders and then refuses, with the reason only in the browser console. It looks like the app
is broken.

**Shot 5's first beat changed.** It used to be "Approve, refused" on a CIO escalation. The desk
no longer offers an action it knows will fail: signed in as controller the button reads **CIO to
approve** and is disabled. Film that, then switch account. The *four-eyes* beat still shows a real
server-side refusal, because a controller is genuinely eligible there and is turned down on count
rather than on role -- so there is still a live refusal on camera, and it is the more interesting
one.

Dropped from the narration with it: "nothing was recorded: an ineligible signature isn't a partial
signature." Still true, still tested, no longer *shown* -- and a claim the footage does not support
is one a judge has to take on trust.

**The two accounts hold different roles.** `fpachisa@gmail.com` is the controller,
`aitutor@homecampus.ai` is the CIO. That is deliberate: it makes the escalation refusal real (a
controller genuinely cannot clear one) and it lets four-eyes be satisfied by two distinct
principals. Check `/readyz` — `unsignable_bands` must be `[]` before you record, or some case in
the queue cannot be approved by anyone on camera.

**Warm the service before you record.** Cloud Run runs this at `--min-instances 0`, and a cold
start costs about fifty seconds on the first investigation. Measured on the deployed service:

| | first line arrives | whole investigation |
|---|---|---|
| Cold instance | 11.2s | 74.4s |
| Warm instance | 0.3s | 23.8s |

Open `/readyz` a couple of times, then work one case for real, and only then start recording. Even
warm, the investigate step is about fifteen seconds of a model actually working — plan the cut
rather than discovering it in the edit. The progress rail is there so those seconds read as
progress rather than as a hang, and shot 4 should linger on it: it names the four steps, and each
one going green is a separate authorisation.

**Before you record, sign in once with both accounts.** The consent screen is in testing mode, so
both addresses have to be listed as *Test users*, and `aitutor@homecampus.ai` is a Workspace account
whose admin may block third-party apps. Find that out now rather than on camera.

## Capture

Screen at **1920×1080**, browser zoomed so the queue fills the frame without a scrollbar. Hide
bookmarks and any other tabs. macOS: `⇧⌘5` → Record Selected Portion.

Record in **five separate clips**, one per shot, rather than one continuous take. A fluffed
approval click means re-recording twenty seconds instead of four minutes, and the narration is one
continuous track you cut to anyway.

| Clip | What is on screen | Live? |
| --- | --- | --- |
| 0 | Sign-in page, then signing in with Google | — |
| 1 | **Fund page** — the two NAVs and the gap, the holdings, then the filed notice under *What explaining one of these takes*; then the queue | — |
| 2 | A terminal, `gcloud pubsub topics publish` — **then hands off the keyboard** — then Fleet activity filling in | **yes, real model calls** |
| 3 | A case page: the **Source document** (the filed notice, in prose), cause, cited evidence, journal legs. Then the disabled **CIO to approve**, the four-eyes refusal, the second account, **Cleared for posting** | **yes** |
| 4 | The remediation timeline | — |
| 5 | Fleet page and the routing table, then Cloud Run console, the 401 curl, Firestore, Cloud Trace | **yes** |

**Clip 2 is the one that must not be cut.** It is the only shot that shows the fleet acting without
a human, and that is the heaviest judging criterion. Publish, take your hands off the keyboard
visibly, and let it run: counters climbing, one gold arc per row walking left to right, the control
log scrolling. If it takes sixty seconds, keep sixty seconds and trim words elsewhere.

**Clip 3 is the argument.** A disabled button, a real refusal, a grant, and then posting refused
with a valid signature in hand. Do it slowly enough to read.

**Reset between takes.** `make demo-reset` puts all seven cases back to *Not started* and re-runs
detection, so a retake opens exactly as the first one did.

## Clip 5, the Google Cloud proof

The address bar has been showing `.run.app` since clip 0, so this clip is about the
*infrastructure* rather than the URL:

1. **Cloud Console → Cloud Run → `nav-sentinel`** — region, live revision, the `nav-runtime` service
   account, the request graph.
2. **Cloud Console → Firestore** — the `nav_stages` and `nav_decisions` collections.
3. **Cloud Console → Trace** — the per-case traces.
4. A terminal, to show that opening the door did not open everything:

```bash
curl -s -o /dev/null -w '%{http_code}\n' $URL/cycle/2026-08-17    # 401 — needs an analyst
curl -s $URL/readyz -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

`/readyz` prints `"repository": "FirestoreRepository"` — the line to linger on, because it is the
service telling you it persists rather than you asserting it. And the 401 on `/cycle` is worth a
sentence: ingress is public, that route still refuses, and the two are not the same thing.

## Assemble

```bash
# Narration first, as one track, from docs/submission/narration.md
ffprobe -v error -show_entries format=duration -of csv=p=0 narration.m4a   # must be < 240

# Concatenate the clips, then lay the narration over.
printf "file '%s'\n" clip1.mov clip2.mov clip3.mov clip4.mov \
                     clip5.mov clip6.mov clip7.mov clip8.mov > clips.txt
ffmpeg -f concat -safe 0 -i clips.txt -c:v libx264 -crf 18 -preset slow -pix_fmt yuv420p video.mp4
ffmpeg -i video.mp4 -i narration.m4a -map 0:v -map 1:a -c:v copy -c:a aac -shortest final.mp4

# Check the cap before uploading. Only the first four minutes are evaluated.
ffprobe -v error -show_entries format=duration -of csv=p=0 final.mp4
```

If `final.mp4` exceeds 240 seconds, cut from clips 1 and 4 — never from 2, 3 or 5.

## Subtitles

YouTube auto-captions are usually fine for a clear AI voice, but check "four eyes", "NAV" and the
model names, which are the words that matter and the ones it mishears. Uploading an SRT is safer:
you already have the exact text.

## Last check before upload

- Under 4:00.
- The model and framework are **said**, not only shown.
- A `.run.app` URL is legible on screen.
- Nothing claims per-agent IAM enforcement, an event-driven runtime, outbound Model Armor, or a real
  month of elapsed time. All four are things this build does not do, and all four are easy to imply
  by accident.
