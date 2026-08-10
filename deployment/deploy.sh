#!/usr/bin/env bash
# deploy.sh
# Manual / one-off deploy, driven entirely by config.json.
# Submits the source to Cloud Build, which runs cloudbuild.yaml.
#
# Usage:  ./deploy.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_config.sh"

echo "==> Submitting build to project '$PROJECT_ID' (region $REGION)"
gcloud builds submit \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --config="$SCRIPT_DIR/cloudbuild.yaml" \
  --substitutions="$(build_subs)" \
  "$SCRIPT_DIR"

echo "==> Build submitted. Track it in Cloud Build > History."
