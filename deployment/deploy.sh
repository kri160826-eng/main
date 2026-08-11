#!/usr/bin/env bash
# deploy.sh — manual one-off deploy. Submits the source to Cloud Build, which
# runs cloudbuild.yaml (it reads config.json itself; no substitutions).
#
# Run from the repo so the app/ context and config.json are included.
# Usage:  ./deploy.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_config.sh"

# Submit the repo root so both the config (deployment/) and app context are present.
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
echo "==> Submitting $REPO_ROOT to Cloud Build ($PROJECT_ID / $REGION)"
gcloud builds submit \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --config="$SCRIPT_DIR/cloudbuild.yaml" \
  "$REPO_ROOT"

echo "==> Submitted. Track it in Cloud Build > History."
