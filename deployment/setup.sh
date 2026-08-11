#!/usr/bin/env bash
# setup.sh — one-time bootstrap, fully driven by config.json. Idempotent:
# every step checks first and SKIPS if the resource already exists.
#
# Trigger creation is the entry point and cascades to its dependent components:
#   ensure_trigger -> ensure_repository -> ensure_connection
#                  -> ensure_service_account
#
# Prereq: the 2nd-gen host connection (trigger.connection) exists & is COMPLETE
#         (one-time OAuth: Console -> Cloud Build -> Repositories -> 2nd gen).
# Prereq: cloudbuild.yaml + config.json are committed & pushed to the repo.
#
# Usage:  ./setup.sh
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_config.sh"

[ -n "$SERVICE_ACCOUNT" ]   || { echo "ERROR: .trigger.serviceAccount is required" >&2; exit 1; }
[ -n "$TRIGGER_CONNECTION" ]|| { echo "ERROR: .trigger.connection is required" >&2; exit 1; }
[ -n "$TRIGGER_REPO_ID" ]   || { echo "ERROR: .trigger.repositoryId (or github.repo) is required" >&2; exit 1; }

SA_EMAIL="$SERVICE_ACCOUNT"
SA_ID="${SA_EMAIL%%@*}"
REPO_RESOURCE="projects/$PROJECT_ID/locations/$TRIGGER_REGION/connections/$TRIGGER_CONNECTION/repositories/$TRIGGER_REPO_ID"
SA_RESOURCE="projects/$PROJECT_ID/serviceAccounts/$SA_EMAIL"

ensure_apis() {
  echo "==> Enabling APIs"
  gcloud services enable --project="$PROJECT_ID" \
    cloudbuild.googleapis.com run.googleapis.com \
    artifactregistry.googleapis.com iam.googleapis.com
}

ensure_artifact_registry() {
  echo "==> Artifact Registry repo '$AR_REPO' ($AR_LOCATION)"
  if gcloud artifacts repositories describe "$AR_REPO" \
       --location="$AR_LOCATION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "    already exists, skipping"
  else
    gcloud artifacts repositories create "$AR_REPO" \
      --repository-format=docker --location="$AR_LOCATION" \
      --project="$PROJECT_ID" --description="Cloud Run images"
    echo "    created"
  fi
}

ensure_service_account() {
  echo "==> Deploy service account $SA_EMAIL"
  if gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "    already exists, skipping create"
  else
    gcloud iam service-accounts create "$SA_ID" --project="$PROJECT_ID" \
      --display-name="Cloud Build deployer"
    echo "    created"
  fi
  # Roles the build runs with (from config.trigger.roles; logging.logWriter is
  # required for user-specified service accounts).
  for role in $DEPLOY_ROLES; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:$SA_EMAIL" --role="$role" --condition=None >/dev/null \
      && echo "    grant $role"
  done
  # Let the Cloud Build service agent impersonate this SA for triggered builds.
  gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" --project="$PROJECT_ID" \
    --member="serviceAccount:$CB_AGENT" --role="roles/iam.serviceAccountTokenCreator" >/dev/null 2>&1 \
    && echo "    CB agent may impersonate $SA_ID" || true
}

# Connection + repository are created by ./setup-connection.sh (one-time, may
# involve GitHub OAuth). Here we only VERIFY they exist.
ensure_connection() {
  echo "==> Host connection '$TRIGGER_CONNECTION' ($TRIGGER_REGION)"
  if gcloud builds connections describe "$TRIGGER_CONNECTION" \
       --region="$TRIGGER_REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "    found"
  else
    echo "ERROR: connection '$TRIGGER_CONNECTION' not found. Run ./setup-connection.sh first." >&2
    exit 1
  fi
}

ensure_repository() {
  ensure_connection                                  # dependency
  echo "==> Cloud Build repository '$TRIGGER_REPO_ID'"
  if gcloud builds repositories describe "$TRIGGER_REPO_ID" \
       --connection="$TRIGGER_CONNECTION" --region="$TRIGGER_REGION" \
       --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "    found"
  else
    echo "ERROR: repository '$TRIGGER_REPO_ID' not found. Run ./setup-connection.sh first." >&2
    exit 1
  fi
}

ensure_trigger() {
  ensure_repository                                   # dependent component
  ensure_service_account                              # dependent component
  echo "==> Trigger '$TRIGGER_NAME' (config: $BUILD_CONFIG_PATH, SA: $SA_ID)"
  if gcloud builds triggers describe "$TRIGGER_NAME" \
       --region="$TRIGGER_REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "    already exists, skipping"
  else
    gcloud builds triggers create github \
      --project="$PROJECT_ID" \
      --region="$TRIGGER_REGION" \
      --name="$TRIGGER_NAME" \
      --repository="$REPO_RESOURCE" \
      --branch-pattern="$BRANCH_PATTERN" \
      --build-config="$BUILD_CONFIG_PATH" \
      --service-account="$SA_RESOURCE"
    echo "    created"
  fi
}

# ---- main ----
echo "==> Project $PROJECT_ID"
gcloud config set project "$PROJECT_ID" >/dev/null
PN="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
CB_AGENT="service-${PN}@gcp-sa-cloudbuild.iam.gserviceaccount.com"
ensure_apis
ensure_artifact_registry
ensure_trigger        # cascades: repository -> connection, and service account
echo "==> Done. Push to a branch matching '$BRANCH_PATTERN' to deploy."
