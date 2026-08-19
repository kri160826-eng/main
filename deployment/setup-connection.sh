#!/usr/bin/env bash
# setup-connection.sh — create the 2nd-gen host CONNECTION and the Cloud Build
# REPOSITORY resource that links your GitHub repo. Run this ONCE per project.
#
# This is separated from setup.sh because it involves the one-time GitHub OAuth
# authorization (a browser step that can't be fully scripted). All values come
# from config.json.
#
# Usage:  ./setup-connection.sh   (re-run after authorizing if it stops)
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_config.sh"

[ -n "$TRIGGER_CONNECTION" ] || { echo "ERROR: .trigger.connection is required" >&2; exit 1; }
[ -n "$TRIGGER_REPO_ID" ]    || { echo "ERROR: .trigger.repositoryId (or github.repo) is required" >&2; exit 1; }

gcloud config set project "$PROJECT_ID" >/dev/null
PN="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
CB_AGENT="service-${PN}@gcp-sa-cloudbuild.iam.gserviceaccount.com"

echo "==> Enabling APIs (Cloud Build, Secret Manager)"
gcloud services enable --project="$PROJECT_ID" \
  cloudbuild.googleapis.com secretmanager.googleapis.com

# ---- host connection ----
echo "==> Host connection '$TRIGGER_CONNECTION' ($TRIGGER_REGION)"
if gcloud builds connections describe "$TRIGGER_CONNECTION" \
     --region="$TRIGGER_REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "    already exists"
else
  # The connection stores its OAuth token in Secret Manager, so the Cloud Build
  # service agent (P4SA) needs Secret Manager admin before creation.
  echo "    granting Secret Manager admin to $CB_AGENT"
  gcloud beta services identity create --service=cloudbuild.googleapis.com \
    --project="$PROJECT_ID" >/dev/null 2>&1 || true
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$CB_AGENT" --role="roles/secretmanager.admin" \
    --condition=None >/dev/null
  echo "    creating connection (IAM may take a few seconds — re-run if it errors once)"
  if [ -n "${GH_APP_INSTALL_ID:-}" ] && [ -n "${GH_TOKEN_SECRET:-}" ]; then
    # Non-interactive: authorize from an existing PAT secret + GitHub App
    # installation id. The connection is created in state COMPLETE, so the
    # authorization gate below passes straight through — no browser step.
    echo "    using PAT secret + installation id (non-interactive)"
    # Strip any "/versions/<v>" suffix — add-iam-policy-binding wants the secret.
    SECRET_RES="${GH_TOKEN_SECRET%/versions/*}"
    gcloud secrets add-iam-policy-binding "$SECRET_RES" \
      --member="serviceAccount:$CB_AGENT" \
      --role="roles/secretmanager.secretAccessor" \
      --project="$PROJECT_ID" >/dev/null 2>&1 || true
    gcloud builds connections create github "$TRIGGER_CONNECTION" \
      --authorizer-token-secret-version="$GH_TOKEN_SECRET" \
      --app-installation-id="$GH_APP_INSTALL_ID" \
      --region="$TRIGGER_REGION" --project="$PROJECT_ID"
  else
    # Interactive: creates a pending connection; the gate below prints the
    # browser authorization URL and exits until you authorize and re-run.
    gcloud builds connections create github "$TRIGGER_CONNECTION" \
      --region="$TRIGGER_REGION" --project="$PROJECT_ID"
  fi
fi

# ---- authorization gate ----
stage="$(gcloud builds connections describe "$TRIGGER_CONNECTION" \
           --region="$TRIGGER_REGION" --project="$PROJECT_ID" \
           --format='value(installationState.stage)' 2>/dev/null || true)"
if [ -n "$stage" ] && [ "$stage" != "COMPLETE" ]; then
  if [ -n "${GH_APP_INSTALL_ID:-}" ] && [ -n "${GH_TOKEN_SECRET:-}" ]; then
    # CLI completion: no browser. There is no in-place token update for github
    # connections, so re-create it non-interactively with the PAT + install id.
    # Safe here — no repositories are attached at this stage.
    echo "    connection stage '$stage' — completing via CLI (PAT + installation id)"
    SECRET_RES="${GH_TOKEN_SECRET%/versions/*}"
    gcloud secrets add-iam-policy-binding "$SECRET_RES" \
      --member="serviceAccount:$CB_AGENT" \
      --role="roles/secretmanager.secretAccessor" \
      --project="$PROJECT_ID" >/dev/null 2>&1 || true
    gcloud builds connections delete "$TRIGGER_CONNECTION" \
      --region="$TRIGGER_REGION" --project="$PROJECT_ID" --quiet
    gcloud builds connections create github "$TRIGGER_CONNECTION" \
      --authorizer-token-secret-version="$GH_TOKEN_SECRET" \
      --app-installation-id="$GH_APP_INSTALL_ID" \
      --region="$TRIGGER_REGION" --project="$PROJECT_ID"
    stage="$(gcloud builds connections describe "$TRIGGER_CONNECTION" \
               --region="$TRIGGER_REGION" --project="$PROJECT_ID" \
               --format='value(installationState.stage)' 2>/dev/null || true)"
    [ "$stage" = "COMPLETE" ] || {
      echo "ERROR: connection still '$stage' after CLI auth — check the PAT scopes/installation id" >&2
      exit 1
    }
  else
    # No CLI credentials configured — fall back to the one-time browser OAuth.
    action="$(gcloud builds connections describe "$TRIGGER_CONNECTION" \
               --region="$TRIGGER_REGION" --project="$PROJECT_ID" \
               --format='value(installationState.actionUri)' 2>/dev/null || true)"
    echo "" >&2
    echo "ACTION NEEDED: authorize the Cloud Build GitHub App (stage: $stage)." >&2
    echo "  Either set trigger.github.appInstallationId + authorizerTokenSecret" >&2
    echo "  in config.json for CLI auth, or open this URL, authorize, and re-run:" >&2
    echo "  ${action:-Console -> Cloud Build -> Repositories -> 2nd gen -> $TRIGGER_CONNECTION}" >&2
    exit 1
  fi
fi
echo "    connection authorized"

# ---- Cloud Build repository (link to GitHub repo) ----
echo "==> Cloud Build repository '$TRIGGER_REPO_ID' -> $REMOTE_URI"
if gcloud builds repositories describe "$TRIGGER_REPO_ID" \
     --connection="$TRIGGER_CONNECTION" --region="$TRIGGER_REGION" \
     --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "    already exists"
else
  gcloud builds repositories create "$TRIGGER_REPO_ID" \
    --remote-uri="$REMOTE_URI" \
    --connection="$TRIGGER_CONNECTION" \
    --region="$TRIGGER_REGION" \
    --project="$PROJECT_ID"
  echo "    created"
fi

echo "==> Connection + repository ready. Now run ./setup.sh"
