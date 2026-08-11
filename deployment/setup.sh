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

# Grant deploy roles to BOTH build identities (legacy Cloud Build SA and the
# Compute Engine default SA that regional builds use by default).
PN="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
for SA in "${PN}@cloudbuild.gserviceaccount.com" "${PN}-compute@developer.gserviceaccount.com"; do
  echo "==> Granting deploy roles to $SA"
  for role in roles/run.admin roles/iam.serviceAccountUser roles/artifactregistry.writer roles/logging.logWriter; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:$SA" --role="$role" --condition=None >/dev/null && echo "    $role"
  done
done

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
echo "==> Trigger '$TRIGGER_NAME' (build config: $BUILD_CONFIG_PATH)"
if gcloud builds triggers describe "$TRIGGER_NAME" --region="$TRIGGER_REGION" >/dev/null 2>&1; then
  echo "    exists. Delete & re-run to change it:"
  echo "    gcloud builds triggers delete $TRIGGER_NAME --region=$TRIGGER_REGION"
else
  gcloud builds triggers create github \
    --name="$TRIGGER_NAME" \
    --region="$TRIGGER_REGION" \
    --repository="$REPO_RESOURCE" \
    --branch-pattern="$BRANCH_PATTERN" \
    --build-config="$BUILD_CONFIG_PATH"
fi

echo "==> Done. Push to a branch matching '$BRANCH_PATTERN' to deploy."
