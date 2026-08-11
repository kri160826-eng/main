#!/usr/bin/env bash
# _config.sh
# Shared loader. Parses config.json and exports the values + a substitutions
# string used by setup.sh and deploy.sh. Portable: Linux, macOS, CI, Git Bash.
# Source it:  source "$(dirname "$0")/_config.sh"
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${CONFIG:-$SCRIPT_DIR/config.json}"

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: 'jq' is required but not installed." >&2
  echo "  Debian/Ubuntu: sudo apt-get install -y jq" >&2
  echo "  macOS:         brew install jq" >&2
  echo "  Windows:       choco install jq   (or scoop install jq)" >&2
  exit 1
fi
[ -f "$CONFIG" ] || { echo "ERROR: config not found: $CONFIG" >&2; exit 1; }

q() { jq -r "$1" "$CONFIG"; }

PROJECT_ID="$(q '.project.id')"
REGION="$(q '.region')"
AR_LOCATION="$(q '.artifactRegistry.location')"
AR_REPO="$(q '.artifactRegistry.repo')"
SERVICE="$(q '.service.name')"
SOURCE_DIR="$(q '.service.sourceDir')"
PORT="$(q '.service.port')"
MEMORY="$(q '.service.memory')"
CPU="$(q '.service.cpu')"
MIN_INSTANCES="$(q '.service.minInstances')"
MAX_INSTANCES="$(q '.service.maxInstances')"
ALLOW_UNAUTH="$(q '.service.allowUnauthenticated')"          # true|false
TRIGGER_NAME="$(q '.trigger.name')"
TRIGGER_REGION="$(q '.trigger.region')"
TRIGGER_CONNECTION="$(q '.trigger.connection')"
# repositoryId defaults to the github repo name when left empty.
TRIGGER_REPO_ID="$(q '(.trigger.repositoryId // "") | select(length>0) // empty' || true)"
[ -n "$TRIGGER_REPO_ID" ] || TRIGGER_REPO_ID="$(q '.trigger.github.repo')"
GH_OWNER="$(q '.trigger.github.owner')"
GH_REPO="$(q '.trigger.github.repo')"
BRANCH_PATTERN="$(q '.trigger.branchPattern')"

# derived
AR_HOST="${AR_LOCATION}-docker.pkg.dev"
# flatten envVars -> "K=V,K=V" (empty if none)
ENV_VARS_STR="$(q '(.service.envVars // {}) | to_entries | map("\(.key)=\(.value)") | join(",")')"

# gcloud splits --substitutions on commas; env-var values may contain commas,
# so use '|' as the delimiter via the ^|^ escape prefix.
build_subs() {
  local parts=(
    "_PROJECT_ID=$PROJECT_ID"
    "_REGION=$REGION"
    "_AR_HOST=$AR_HOST"
    "_REPO=$AR_REPO"
    "_SERVICE=$SERVICE"
    "_SOURCE_DIR=$SOURCE_DIR"
    "_PORT=$PORT"
    "_MEMORY=$MEMORY"
    "_CPU=$CPU"
    "_MIN_INSTANCES=$MIN_INSTANCES"
    "_MAX_INSTANCES=$MAX_INSTANCES"
    "_ALLOW_UNAUTH=$ALLOW_UNAUTH"
    "_ENV_VARS=$ENV_VARS_STR"
  )
  local IFS='|'
  echo "^|^${parts[*]}"
}
