#!/usr/bin/env bash
# setup.sh — one-time bootstrap, driven by config.json.
# Enables APIs, creates the Artifact Registry repo, grants IAM to the build
# service accounts, links the GitHub repo to the 2nd-gen connection, and
# creates the regional push trigger.
#
# Prereq: the 2nd-gen host connection (config.trigger.connection) already exists
#         and is COMPLETE (created once in Console -> Cloud Build -> Repositories).
# Prereq: cloudbuild.yaml + config.json are committed & pushed to GitHub at the
#         paths in config (buildConfigPath), so the trigger can find the config.
#
# Usage:  ./setup.sh
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_config.sh"

echo "==> Project $PROJECT_ID"
gcloud config set project "$PROJECT_ID" >/dev/null

echo "==> Enabling APIs"
gcloud services enable \
  cloudbuild.googleapis.com run.googleapis.com \
  artifactregistry.googleapis.com iam.googleapis.com

echo "==> Artifact Registry repo '$AR_REPO' ($AR_LOCATION)"
if ! gcloud artifacts repositories describe "$AR_REPO" --location="$AR_LOCATION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$AR_REPO" \
    --repository-format=docker --location="$AR_LOCATION" \
    --description="Cloud Run images"
else
  echo "    exists"
fi

# --- Custom deploy service account (config.trigger.serviceAccount) ---
# The trigger runs builds AS this SA, so it needs the deploy roles (and
# logging.logWriter, required when a build uses a user-specified SA).
[ -n "$SERVICE_ACCOUNT" ] || { echo "ERROR: .trigger.serviceAccount is required" >&2; exit 1; }
SA_EMAIL="$SERVICE_ACCOUNT"
SA_ID="${SA_EMAIL%%@*}"
PN="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

echo "==> Ensuring deploy service account $SA_EMAIL"
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_ID" --project="$PROJECT_ID" \
    --display-name="Cloud Build deployer"
else
  echo "    exists"
fi

echo "==> Granting deploy roles to $SA_EMAIL"
for role in roles/run.admin roles/iam.serviceAccountUser roles/artifactregistry.writer roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" --role="$role" --condition=None >/dev/null && echo "    $role"
done

# Let the Cloud Build service agent impersonate this SA for triggered builds.
CB_AGENT="service-${PN}@gcp-sa-cloudbuild.iam.gserviceaccount.com"
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" --project="$PROJECT_ID" \
  --member="serviceAccount:$CB_AGENT" --role="roles/iam.serviceAccountTokenCreator" >/dev/null 2>&1 \
  && echo "    Cloud Build agent may impersonate $SA_ID" || true

echo "==> Verifying host connection '$TRIGGER_CONNECTION' in $TRIGGER_REGION"
gcloud builds connections describe "$TRIGGER_CONNECTION" --region="$TRIGGER_REGION" >/dev/null 2>&1 || {
  echo "ERROR: connection '$TRIGGER_CONNECTION' not found. Create it in Console:" >&2
  echo "  Cloud Build -> Repositories -> 2nd gen -> Create host connection" >&2
  exit 1
}

echo "==> Linking repo '$TRIGGER_REPO_ID' (github.com/$GH_OWNER/$GH_REPO)"
if ! gcloud builds repositories describe "$TRIGGER_REPO_ID" \
       --connection="$TRIGGER_CONNECTION" --region="$TRIGGER_REGION" >/dev/null 2>&1; then
  gcloud builds repositories create "$TRIGGER_REPO_ID" \
    --remote-uri="https://github.com/$GH_OWNER/$GH_REPO.git" \
    --connection="$TRIGGER_CONNECTION" --region="$TRIGGER_REGION"
else
  echo "    already linked"
fi

REPO_RESOURCE="projects/$PROJECT_ID/locations/$TRIGGER_REGION/connections/$TRIGGER_CONNECTION/repositories/$TRIGGER_REPO_ID"
SA_RESOURCE="projects/$PROJECT_ID/serviceAccounts/$SA_EMAIL"
echo "==> Trigger '$TRIGGER_NAME' (build config: $BUILD_CONFIG_PATH, SA: $SA_ID)"
if gcloud builds triggers describe "$TRIGGER_NAME" --region="$TRIGGER_REGION" >/dev/null 2>&1; then
  echo "    exists. Delete & re-run to change it:"
  echo "    gcloud builds triggers delete $TRIGGER_NAME --region=$TRIGGER_REGION"
else
  # Specifying --service-account is what makes this succeed under org policies
  # that require a build identity (otherwise: opaque INVALID_ARGUMENT).
  gcloud builds triggers create github \
    --project="$PROJECT_ID" \
    --region="$TRIGGER_REGION" \
    --name="$TRIGGER_NAME" \
    --repository="$REPO_RESOURCE" \
    --branch-pattern="$BRANCH_PATTERN" \
    --build-config="$BUILD_CONFIG_PATH" \
    --service-account="$SA_RESOURCE"
fi

echo "==> Done. Push to a branch matching '$BRANCH_PATTERN' to deploy."
