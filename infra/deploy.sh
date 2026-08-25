#!/usr/bin/env bash
#
# Deploy the Cloud Run service, and wire Pub/Sub to push into it.
#
# Two identities, deliberately. The service runs as `nav-runtime`, which holds only what the
# service itself needs. Pub/Sub signs its push tokens as `nav-pubsub-push`, which holds nothing
# except the right to invoke this one service -- so a leaked push token cannot do anything else,
# and the service can tell a genuine delivery from any other authenticated caller.
#
# Note on per-agent identity: Cloud Run gives one runtime identity per service, so the seven
# per-agent service accounts `bootstrap.sh` mints are not what the container runs as. Agent
# identity is enforced in-process by the gateway against the published manifests, and the
# per-agent accounts exist for the data-plane grants. Making them the *cloud* identity of each
# call needs token impersonation, which is a stated extension rather than something this script
# pretends to do.
set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:-all-things-agentic-hack-fp}"
REGION="${NAV_REGION:-us-central1}"
SERVICE="${NAV_SERVICE:-nav-sentinel}"
RUNTIME_SA="nav-runtime@${PROJECT}.iam.gserviceaccount.com"
PUSH_SA="nav-pubsub-push@${PROJECT}.iam.gserviceaccount.com"
TOPIC="nav-exceptions"
DLQ_TOPIC="nav-exceptions-dlq"
# Signs the exception desk's session cookie. Generated per deploy rather than committed: a signing
# key in a public repository is every session forgeable by anyone who reads it. Rotating it on each
# deploy signs analysts out, which is the correct trade for a key nobody has to store.
SESSION_SECRET="$(openssl rand -hex 32)"
# `NAV_REPOSITORY` stated explicitly even though the server derives it from `NAV_APPROVALS`: a
# deployment writing its audit trail to memory looks identical to a healthy one from outside, so the
# intent is named rather than inferred. `/readyz` now refuses to report ready unless it is durable.

say() { printf "\n\033[1m== %s\033[0m\n" "$1"; }

if [[ "$(gcloud config get-value project 2>/dev/null)" != "$PROJECT" ]]; then
  echo "gcloud is pointed at $(gcloud config get-value project 2>/dev/null), not $PROJECT." >&2
  echo "This script shells out to gcloud, so it would deploy to the wrong project." >&2
  exit 1
fi

say "Service accounts"
for pair in "nav-runtime:NAV Sentinel Cloud Run runtime" "nav-pubsub-push:Pub/Sub push identity"; do
  id="${pair%%:*}"; name="${pair#*:}"
  gcloud iam service-accounts describe "${id}@${PROJECT}.iam.gserviceaccount.com" \
    --project "$PROJECT" >/dev/null 2>&1 \
    || gcloud iam service-accounts create "$id" --display-name "$name" --project "$PROJECT"
done

say "Runtime roles"
# Only what the service uses: call Gemini, screen content, write traces, persist cases and
# approvals. No Pub/Sub publish -- the service consumes, it does not produce.
for role in roles/aiplatform.user roles/cloudtrace.agent roles/telemetry.tracesWriter \
            roles/datastore.user roles/modelarmor.user; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member "serviceAccount:${RUNTIME_SA}" --role "$role" --condition None >/dev/null
  echo "  ${role}"
done

say "Build"
gcloud builds submit --tag "gcr.io/${PROJECT}/${SERVICE}:latest" --project "$PROJECT" .

say "Deploy"
# --no-allow-unauthenticated is the first of two auth layers; the handler verifies the OIDC token
# independently, because this flag is exactly the kind of thing a later deploy drops.
gcloud run deploy "$SERVICE" \
  --image "gcr.io/${PROJECT}/${SERVICE}:latest" \
  --region "$REGION" \
  --project "$PROJECT" \
  --service-account "$RUNTIME_SA" \
  --no-allow-unauthenticated \
  --update-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=global,NAV_REGION=${REGION},GOOGLE_GENAI_USE_VERTEXAI=true,NAV_APPROVALS=firestore,NAV_REPOSITORY=firestore,NAV_SESSION_SECRET=${SESSION_SECRET},NAV_PUSH_SERVICE_ACCOUNT=${PUSH_SA}" \
  --memory 1Gi --cpu 1 --timeout 300 --max-instances 4 --min-instances 0

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" \
        --format='value(status.url)')"
echo "  ${URL}"

say "Audience"
# `--update-env-vars` above, not `--set-env-vars`: set removes every existing variable first, so
# each *redeploy* shipped a revision with NAV_PUSH_AUDIENCE stripped while the previous deploy's
# subscription was live and pushing. Every push in that window fails closed with 500, and with
# --max-delivery-attempts 5 a retrying message can exhaust its attempts and dead-letter.
#
# The handler checks the token audience, which is only knowable after the URL exists, so on a
# first deploy it lands in a second revision. That used to mean the first revision served with audience verification
# silently disabled -- `audience=PUSH_AUDIENCE or None` skips the check entirely. The handler now
# refuses with 500 when either push variable is unset, so the window between these two revisions
# fails closed rather than accepting any Google-signed token. The subscription is created after
# this update, so no push is ever sent into that window.
gcloud run services update "$SERVICE" --region "$REGION" --project "$PROJECT" \
  --update-env-vars "NAV_PUSH_AUDIENCE=${URL}" >/dev/null

say "Pub/Sub push subscription"
gcloud run services add-iam-policy-binding "$SERVICE" --region "$REGION" --project "$PROJECT" \
  --member "serviceAccount:${PUSH_SA}" --role roles/run.invoker >/dev/null
# The dead-letter topic must NOT be the subscription's own source topic. It was: a message that
# failed five attempts was republished to nav-exceptions and redelivered by the same subscription,
# an unbounded loop spending Gemini and Model Armor calls on every turn. And the create was
# `2>/dev/null || <create with no dead-letter policy at all>`, so the broken form failed silently
# and the fallback -- which retries a permanently-failing message forever -- is what actually ran.
# No silencing here: a failure to attach the policy should be loud.
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
PUBSUB_AGENT="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"

gcloud pubsub topics describe "$DLQ_TOPIC" --project "$PROJECT" >/dev/null 2>&1 \
  || gcloud pubsub topics create "$DLQ_TOPIC" --project "$PROJECT" >/dev/null

# A dead-letter policy needs the Pub/Sub service agent to publish to the DLQ, and to acknowledge on
# the source subscription so it can remove the message it forwarded. The subscriber grant is
# applied after the subscription exists, below -- an earlier comment here claimed both were
# required before the policy would be accepted, which cannot be true of a grant on a subscription
# the create call is what brings into existence.
gcloud pubsub topics add-iam-policy-binding "$DLQ_TOPIC" --project "$PROJECT" \
  --member "serviceAccount:${PUBSUB_AGENT}" --role roles/pubsub.publisher >/dev/null

if gcloud pubsub subscriptions describe nav-exceptions-push --project "$PROJECT" >/dev/null 2>&1
then
  # Update rather than delete-and-recreate: recreating discards every unacknowledged message, and
  # on an exception queue those are the audit-bearing ones.
  gcloud pubsub subscriptions update nav-exceptions-push --project "$PROJECT" \
    --push-endpoint "${URL}/pubsub/exceptions" \
    --push-auth-service-account "$PUSH_SA" \
    --push-auth-token-audience "$URL" \
    --ack-deadline 300 \
    --dead-letter-topic "$DLQ_TOPIC" --max-delivery-attempts 5 >/dev/null
else
  gcloud pubsub subscriptions create nav-exceptions-push \
    --topic "$TOPIC" --project "$PROJECT" \
    --push-endpoint "${URL}/pubsub/exceptions" \
    --push-auth-service-account "$PUSH_SA" \
    --push-auth-token-audience "$URL" \
    --ack-deadline 300 \
    --dead-letter-topic "$DLQ_TOPIC" --max-delivery-attempts 5 >/dev/null
fi

gcloud pubsub subscriptions add-iam-policy-binding nav-exceptions-push --project "$PROJECT" \
  --member "serviceAccount:${PUBSUB_AGENT}" --role roles/pubsub.subscriber >/dev/null

# A dead-letter topic with no subscription discards on arrival, which is the same outcome as
# having no dead-letter policy while looking like diligence. This pull subscription retains
# failed messages for inspection; nothing consumes it automatically, by design -- a message that
# defeated five delivery attempts wants a human, not another retry.
gcloud pubsub subscriptions describe nav-exceptions-dlq-hold --project "$PROJECT" >/dev/null 2>&1 \
  || gcloud pubsub subscriptions create nav-exceptions-dlq-hold \
       --topic "$DLQ_TOPIC" --project "$PROJECT" \
       --message-retention-duration 7d --expiration-period never >/dev/null

say "Done"
echo "Service : ${URL}"
echo "Runtime : ${RUNTIME_SA}"
echo "Push as : ${PUSH_SA}"
echo
echo "Verify:"
echo "  curl -H \"Authorization: Bearer \$(gcloud auth print-identity-token)\" ${URL}/readyz"
echo "  gcloud pubsub topics publish ${TOPIC} --message '{\"as_of\":\"2026-08-17\"}' --project ${PROJECT}"
