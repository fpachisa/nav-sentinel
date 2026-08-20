#!/usr/bin/env bash
#
# Remove the deployed service and its subscription.
#
# Deliberately narrow. It deletes the things that cost money while running -- the Cloud Run
# revision and the push subscription -- and leaves the service accounts, the Pub/Sub topic, the
# Firestore database and the Model Armor template alone. Those cost nothing idle, and deleting
# them would make the deployment unreproducible from `make deploy` alone.
#
# The brief's own guidance is to tear down *after* capturing video and code proof, so this is not
# run as part of a deploy. `--dry-run` prints what would go without touching anything, which is
# how it gets verified on a day when the service still needs to be up.
set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:-all-things-agentic-hack-fp}"
REGION="${NAV_REGION:-us-central1}"
SERVICE="${NAV_SERVICE:-nav-sentinel}"
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

run() {
  if (( DRY_RUN )); then
    printf "  would run: %s\n" "$*"
  else
    "$@"
  fi
}

if [[ "$(gcloud config get-value project 2>/dev/null)" != "$PROJECT" ]]; then
  echo "gcloud is pointed elsewhere; refusing to tear down the wrong project." >&2
  exit 1
fi

printf "\n\033[1m== Teardown%s\033[0m\n" "$( ((DRY_RUN)) && echo ' (dry run)')"

if gcloud pubsub subscriptions describe nav-exceptions-push --project "$PROJECT" >/dev/null 2>&1; then
  run gcloud pubsub subscriptions delete nav-exceptions-push --project "$PROJECT" --quiet
else
  echo "  subscription nav-exceptions-push: absent"
fi

if gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" >/dev/null 2>&1; then
  run gcloud run services delete "$SERVICE" --region "$REGION" --project "$PROJECT" --quiet
else
  echo "  service $SERVICE: absent"
fi

echo
echo "Kept on purpose: per-agent service accounts, the nav-exceptions topic, Firestore, and the"
echo "Model Armor template. All are free at rest, and removing them would make the deployment"
echo "unreproducible from 'make deploy'."
