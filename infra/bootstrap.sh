#!/usr/bin/env bash
# Provision the Google Cloud footprint for NAV Sentinel.
#
# Idempotent: safe to re-run. Every agent in the registry receives its own service account
# with only the roles its manifest declares, so identity is derived from the registry rather
# than maintained by hand in a second place.
set -euo pipefail

PROJECT="${PROJECT:-all-things-agentic-hack-fp}"
REGION="${REGION:-us-central1}"
ARMOR_TEMPLATE="${ARMOR_TEMPLATE:-nav-sentinel-untrusted-ingest}"

# Model Armor is only reachable on its regional endpoint; the global endpoint returns
# PERMISSION_DENIED even for a project owner, which is misleading enough to call out.
export CLOUDSDK_API_ENDPOINT_OVERRIDES_MODELARMOR="https://modelarmor.${REGION}.rep.googleapis.com/"

say() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

say "Enabling APIs"
gcloud services enable \
  aiplatform.googleapis.com modelarmor.googleapis.com run.googleapis.com \
  pubsub.googleapis.com firestore.googleapis.com cloudscheduler.googleapis.com \
  secretmanager.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  cloudtrace.googleapis.com telemetry.googleapis.com observability.googleapis.com \
  logging.googleapis.com monitoring.googleapis.com iamcredentials.googleapis.com \
  --project "$PROJECT"

say "Model Armor template: $ARMOR_TEMPLATE"
if gcloud model-armor templates describe "$ARMOR_TEMPLATE" \
     --location="$REGION" --project="$PROJECT" >/dev/null 2>&1; then
  echo "already exists"
else
  gcloud model-armor templates create "$ARMOR_TEMPLATE" \
    --location="$REGION" --project="$PROJECT" \
    --pi-and-jailbreak-filter-settings-enforcement=enabled \
    --pi-and-jailbreak-filter-settings-confidence-level=LOW_AND_ABOVE \
    --malicious-uri-filter-settings-enforcement=enabled \
    --basic-config-filter-enforcement=enabled \
    --template-metadata-log-sanitize-operations \
    --template-metadata-log-operations
fi

say "Per-agent service accounts (Agent Identity)"
# Read the identities straight out of the registry manifests: one source of truth.
.venv/bin/python - "$PROJECT" <<'PYEOF'
import subprocess, sys
sys.path.insert(0, "src")
from nav_sentinel.composition import configure
from nav_sentinel.registry.models import load_manifests

# Manifests are sourced from the registered process packs, so the composition root has to run
# before any of them are visible. Without this the script mints zero service accounts.
configure()

project = sys.argv[1]
for m in load_manifests():
    sa = m.service_account_id
    email = f"{sa}@{project}.iam.gserviceaccount.com"
    exists = subprocess.run(
        ["gcloud", "iam", "service-accounts", "describe", email, "--project", project],
        capture_output=True,
    ).returncode == 0
    if exists:
        print(f"  {sa:32s} exists")
        continue
    subprocess.run(
        ["gcloud", "iam", "service-accounts", "create", sa,
         "--display-name", m.display_name,
         "--description", f"{m.ref} -- scopes: {','.join(m.data_scopes.read) or 'none'}",
         "--project", project],
        check=True, capture_output=True,
    )
    print(f"  {sa:32s} created")

    # Least privilege: read-only telemetry write plus model access. No agent gets
    # datastore.user unless its manifest declares a write scope.
    roles = ["roles/aiplatform.user", "roles/cloudtrace.agent", "roles/telemetry.tracesWriter"]
    if m.data_scopes.write:
        roles.append("roles/datastore.user")
    for role in roles:
        subprocess.run(
            ["gcloud", "projects", "add-iam-policy-binding", project,
             "--member", f"serviceAccount:{email}", "--role", role, "--condition", "None"],
            check=False, capture_output=True,
        )
    print(f"    roles: {', '.join(roles)}")
PYEOF

say "Pub/Sub topic for asynchronous exception dispatch"
gcloud pubsub topics describe nav-exceptions --project "$PROJECT" >/dev/null 2>&1 \
  || gcloud pubsub topics create nav-exceptions --project "$PROJECT"

say "Firestore (native mode) for case state and Memory Bank index"
gcloud firestore databases describe --database="(default)" --project "$PROJECT" >/dev/null 2>&1 \
  || gcloud firestore databases create --location="$REGION" --project "$PROJECT" --type=firestore-native

say "Done"
echo "Project : $PROJECT"
echo "Region  : $REGION"
