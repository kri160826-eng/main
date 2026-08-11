#!/usr/bin/env bash
# setup.sh
# One-time project bootstrap, fully driven by config.json.
# Enables APIs, creates the Artifact Registry repo, grants IAM to the
# Cloud Build service account, and creates the git-push trigger.
#
# Prereq: connect the GitHub repo to Cloud Build once via
#         Console -> Cloud Build -> Triggers -> "Connect Repository" (OAuth).
#
# Usage:  ./setup.sh
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_config.sh"

echo "==> Using project '$PROJECT_ID' (region $REGION)"
gcloud config set project "$PROJECT_ID" >/dev/null

echo "==> Enabling required APIs"
gcloud services enable \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  iam.googleapis.com

echo "==> Ensuring Artifact Registry repo '$AR_REPO' in $AR_LOCATION"
if ! gcloud artifacts repositories describe "$AR_REPO" --location="$AR_LOCATION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$AR_REPO" \
    --repository-format=docker \
    --location="$AR_LOCATION" \
    --description="Container images for Cloud Run"
else
  echo "    repo exists, skipping"
fi

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
CB_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"
echo "==> Granting deploy roles to $CB_SA"
for role in roles/run.admin roles/iam.serviceAccountUser roles/artifactregistry.writer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$CB_SA" --role="$role" --condition=None >/dev/null
  echo "    granted $role"
done

# --- 2nd-gen trigger (host connection, regional, uses --repository) ---
[ -n "$TRIGGER_CONNECTION" ] || { echo "ERROR: .trigger.connection is required" >&2; exit 1; }

echo "==> Verifying host connection '$TRIGGER_CONNECTION' in $TRIGGER_REGION"
if ! gcloud builds connections describe "$TRIGGER_CONNECTION" --region="$TRIGGER_REGION" >/dev/null 2>&1; then
  echo "ERROR: connection '$TRIGGER_CONNECTION' not found in region '$TRIGGER_REGION'." >&2
  echo "  Create & authorize it once (installs the Cloud Build GitHub App):" >&2
  echo "  Console -> Cloud Build -> Repositories -> 2nd gen -> Create host connection" >&2
  echo "  Existing connections:" >&2
  gcloud builds connections list --region="$TRIGGER_REGION" >&2 || true
  exit 1
fi

echo "==> Ensuring repository '$TRIGGER_REPO_ID' is linked to the connection"
if ! gcloud builds repositories describe "$TRIGGER_REPO_ID" \
       --connection="$TRIGGER_CONNECTION" --region="$TRIGGER_REGION" >/dev/null 2>&1; then
  echo "    linking https://github.com/$GH_OWNER/$GH_REPO.git"
  gcloud builds repositories create "$TRIGGER_REPO_ID" \
    --remote-uri="https://github.com/$GH_OWNER/$GH_REPO.git" \
    --connection="$TRIGGER_CONNECTION" \
    --region="$TRIGGER_REGION"
else
  echo "    repository already linked"
fi

REPO_RESOURCE="projects/$PROJECT_ID/locations/$TRIGGER_REGION/connections/$TRIGGER_CONNECTION/repositories/$TRIGGER_REPO_ID"
echo "==> Creating trigger '$TRIGGER_NAME' (region: $TRIGGER_REGION)"
if gcloud builds triggers describe "$TRIGGER_NAME" --region="$TRIGGER_REGION" >/dev/null 2>&1; then
  echo "    trigger exists. To apply config changes, delete & re-run:"
  echo "    gcloud builds triggers delete $TRIGGER_NAME --region=$TRIGGER_REGION"
else
  gcloud builds triggers create github \
    --name="$TRIGGER_NAME" \
    --region="$TRIGGER_REGION" \
    --repository="$REPO_RESOURCE" \
    --branch-pattern="$BRANCH_PATTERN" \
    --build-config="deployment/cloudbuild.yaml" \
    --substitutions="$(build_subs)"
fi

echo "==> Done. Push to a branch matching '$BRANCH_PATTERN' to deploy."
