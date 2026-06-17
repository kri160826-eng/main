#!/usr/bin/env bash
# =============================================================================
# bootstrap-gcp.sh
#
# Run ONCE locally to wire up everything GCP needs for the GitHub Actions
# CI/CD pipeline:
#   • Enables required APIs
#   • Creates Artifact Registry repo
#   • Creates a dedicated service account
#   • Grants IAM roles (Cloud Run, Artifact Registry, BigQuery, GCS, Vertex AI)
#   • Creates Workload Identity Federation pool + provider
#   • Prints the exact values to paste into GitHub Secrets / Variables
#
# Prerequisites:
#   gcloud CLI installed and authenticated (`gcloud auth login`)
#   Owner or roles/iam.workloadIdentityPoolAdmin on the project
# =============================================================================

set -euo pipefail

# ─── EDIT THESE ──────────────────────────────────────────────────────────────
GCP_PROJECT_ID="project-612d0540-c843-44b0-a04"          # e.g. my-project-123
GCP_REGION="us-central1"
GITHUB_ORG="kri160826-eng"  # e.g. acme-corp
GITHUB_REPO="main"              # e.g. adk-agent
SERVICE_NAME="adk-dm-agent"
AR_REPO="cloud-run-apps"                  # Artifact Registry repo name
SA_NAME="adk-cloud-run-deployer"          # Service account short name
WIF_POOL="github-pool"
WIF_PROVIDER="github-provider"
# ─────────────────────────────────────────────────────────────────────────────

SA_EMAIL="${SA_NAME}@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
PROJECT_NUMBER=$(gcloud projects describe "$GCP_PROJECT_ID" --format="value(projectNumber)")

echo "============================================================"
echo " Bootstrapping GCP project: $GCP_PROJECT_ID"
echo " GitHub repo               : ${GITHUB_ORG}/${GITHUB_REPO}"
echo "============================================================"
echo ""

# ── 1. Enable required APIs ──────────────────────────────────────────────────
echo "▶ Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  bigquery.googleapis.com \
  storage.googleapis.com \
  aiplatform.googleapis.com \
  --project="$GCP_PROJECT_ID"

# ── 2. Create Artifact Registry repository ───────────────────────────────────
echo "▶ Creating Artifact Registry repo: $AR_REPO ..."
gcloud artifacts repositories create "$AR_REPO" \
  --repository-format=docker \
  --location="$GCP_REGION" \
  --project="$GCP_PROJECT_ID" \
  --description="Docker images for Cloud Run services" 2>/dev/null || \
  echo "  (repo already exists — skipping)"

# ── 3. Create service account ────────────────────────────────────────────────
echo "▶ Creating service account: $SA_EMAIL ..."
gcloud iam service-accounts create "$SA_NAME" \
  --project="$GCP_PROJECT_ID" \
  --display-name="ADK Cloud Run Deployer" 2>/dev/null || \
  echo "  (service account already exists — skipping)"

# ── 4. Grant IAM roles ───────────────────────────────────────────────────────
echo "▶ Granting IAM roles to service account..."

ROLES=(
  "roles/run.admin"                    # Deploy Cloud Run services
  "roles/artifactregistry.writer"      # Push Docker images
  "roles/iam.serviceAccountUser"       # Attach SA to Cloud Run
  "roles/bigquery.dataEditor"          # Agent: read/write BigQuery
  "roles/bigquery.jobUser"             # Agent: run BigQuery jobs
  "roles/storage.objectAdmin"          # Agent: read/write GCS
  "roles/aiplatform.user"              # Agent: call Vertex AI / Gemini
)

for ROLE in "${ROLES[@]}"; do
  gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$ROLE" \
    --condition=None \
    --quiet
  echo "  ✔ $ROLE"
done

# ── 5. Create Workload Identity Pool ─────────────────────────────────────────
echo "▶ Creating Workload Identity Pool: $WIF_POOL ..."
gcloud iam workload-identity-pools create "$WIF_POOL" \
  --project="$GCP_PROJECT_ID" \
  --location="global" \
  --display-name="GitHub Actions Pool" 2>/dev/null || \
  echo "  (pool already exists — skipping)"

# ── 6. Create Workload Identity Provider (OIDC) ──────────────────────────────
echo "▶ Creating Workload Identity Provider: $WIF_PROVIDER ..."
gcloud iam workload-identity-pools providers create-oidc "$WIF_PROVIDER" \
  --project="$GCP_PROJECT_ID" \
  --location="global" \
  --workload-identity-pool="$WIF_POOL" \
  --display-name="GitHub Provider" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='${GITHUB_ORG}/${GITHUB_REPO}'" 2>/dev/null || \
  echo "  (provider already exists — skipping)"

# ── 7. Bind WIF to service account ───────────────────────────────────────────
echo "▶ Binding Workload Identity to service account..."
WIF_MEMBER="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/attribute.repository/${GITHUB_ORG}/${GITHUB_REPO}"

gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --project="$GCP_PROJECT_ID" \
  --role="roles/iam.workloadIdentityUser" \
  --member="$WIF_MEMBER" \
  --quiet

# ── 8. Print GitHub configuration values ─────────────────────────────────────
WIF_PROVIDER_FULL="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/providers/${WIF_PROVIDER}"

echo ""
echo "============================================================"
echo " ✅  Bootstrap complete!"
echo " Copy these values into GitHub:"
echo "============================================================"
echo ""
echo "── GitHub Repository Variables  (Settings → Secrets and variables → Actions → Variables) ──"
echo "  GCP_PROJECT_ID          = $GCP_PROJECT_ID"
echo "  GCP_REGION              = $GCP_REGION"
echo "  CLOUD_RUN_SERVICE_NAME  = $SERVICE_NAME"
echo ""
echo "── GitHub Repository Secrets  (Settings → Secrets and variables → Actions → Secrets) ──"
echo "  WIF_PROVIDER            = $WIF_PROVIDER_FULL"
echo "  WIF_SERVICE_ACCOUNT     = $SA_EMAIL"
echo ""
echo "── Optional: update Dockerfile ENV ──────────────────────────────────────"
echo "  ENV GOOGLE_CLOUD_PROJECT=$GCP_PROJECT_ID"
echo "  ENV GOOGLE_CLOUD_LOCATION=$GCP_REGION"
echo ""
