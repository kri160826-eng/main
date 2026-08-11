#!/usr/bin/env bash
# _config.sh — parses config.json for setup.sh / deploy.sh.
# The build pipeline reads config.json on its own, so this only exposes the
# values the local scripts need to create infra + the trigger.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${CONFIG:-$SCRIPT_DIR/config.json}"

command -v jq >/dev/null 2>&1 || { echo "ERROR: jq is required (apt-get/brew/choco install jq)"; exit 1; }
[ -f "$CONFIG" ] || { echo "ERROR: config not found: $CONFIG"; exit 1; }
q() { jq -r "$1" "$CONFIG"; }

PROJECT_ID="$(q '.project.id')"
REGION="$(q '.region')"
AR_LOCATION="$(q '.artifactRegistry.location')"
AR_REPO="$(q '.artifactRegistry.repo')"

TRIGGER_NAME="$(q '.trigger.name')"
TRIGGER_REGION="$(q '.trigger.region')"
TRIGGER_CONNECTION="$(q '.trigger.connection')"
TRIGGER_REPO_ID="$(q '.trigger.repositoryId // .trigger.github.repo')"
BUILD_CONFIG_PATH="$(q '.trigger.buildConfigPath // "cloudbuild.yaml"')"
# Custom build/deploy service account email that the trigger runs as.
SERVICE_ACCOUNT="$(q '.trigger.serviceAccount // ""')"
GH_OWNER="$(q '.trigger.github.owner')"
GH_REPO="$(q '.trigger.github.repo')"
BRANCH_PATTERN="$(q '.trigger.branchPattern')"
