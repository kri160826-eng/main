# Config-Driven Cloud Run Deployment (Cloud Build)

CI/CD pipeline — **git push → Cloud Build → Artifact Registry → Cloud Run** — where
**every dynamic value comes from `config.json`**. No values are hardcoded in the
pipeline or scripts.

## Files

| File | Purpose |
|------|---------|
| `config.json` | **Single source of truth.** All project/region/repo/service values. |
| `cloudbuild.yaml` | Build → push → deploy. Reads only injected substitutions. |
| `_config.sh` | Shared loader: parses `config.json`, derives values, builds substitutions. |
| `setup.sh` | One-time bootstrap: APIs, registry, IAM, git-push trigger. |
| `deploy.sh` | Manual one-off deploy from your machine. |

Scripts are portable POSIX **bash** (Linux, macOS, CI runners, and Windows Git Bash)
and depend only on `gcloud` and `jq`.

## `config.json` reference

```jsonc
{
  "project":  { "id": "your-gcp-project-id" },
  "region":   "us-central1",                  // Cloud Build + Cloud Run region
  "artifactRegistry": {
    "location": "us-central1",                // AR host = <location>-docker.pkg.dev
    "repo":     "apps"
  },
  "service": {
    "name":         "my-service",             // Cloud Run service name
    "sourceDir":    ".",                       // Docker build context (repo-relative)
    "port":         8080,
    "memory":       "512Mi",
    "cpu":          "1",
    "minInstances": 0,
    "maxInstances": 10,
    "allowUnauthenticated": true,
    "envVars":      { "EXAMPLE_KEY": "example-value" }
  },
  "trigger": {
    "name":          "deploy-my-service",
    "github":        { "owner": "org-or-user", "repo": "repo-name" },
    "branchPattern": "^main$"
  }
}
```

Change a value here and re-run `setup.ps1` (or `deploy.ps1`) — nothing else to edit.

## How values flow

`config.json` → `_config.sh` (parse + derive `_AR_HOST`, flatten `envVars`) →
`--substitutions` → `cloudbuild.yaml` (`${_VAR}`). The YAML holds no config; it only
references substitutions.

## Prerequisites

- `gcloud` installed and authenticated (`gcloud auth login`), billing enabled.
- `jq` installed (`apt-get install jq` / `brew install jq` / `choco install jq`).
- A **Dockerfile** in `sourceDir` that listens on the port set in `config.json` (Cloud Run passes it as `$PORT`).
- GitHub repo **connected to Cloud Build** once via Console → Cloud Build → Triggers → *Connect Repository* (OAuth step; can't be scripted).

## Setup (once)

1. Fill in `config.json`.
2. Run:

   ```bash
   ./setup.sh
   ```

Enables APIs, creates the Artifact Registry repo, grants the Cloud Build service
account `run.admin` + `iam.serviceAccountUser` + `artifactregistry.writer`, and
creates the push trigger with substitutions from your config.

## Automated deploy

Push to the branch matching `trigger.branchPattern`. The trigger runs
`cloudbuild.yaml`: build (with layer cache) → push `:$SHORT_SHA` + `:latest` →
deploy the commit-pinned image to Cloud Run.

## Manual deploy

```bash
./deploy.sh
```

Tags the image `manual-<timestamp>` and deploys it.

## Changing config later

Re-run `deploy.sh` for a manual deploy with new values. For the **trigger**,
substitutions are stored at creation time — delete and recreate it to pick up changes:

```bash
gcloud builds triggers delete deploy-my-service --region=us-central1
./setup.sh
```

## Rollback

```bash
gcloud run services update-traffic <service> --region=<region> --to-revisions=<PREVIOUS_REVISION>=100
```

## Notes

- Env var values must not contain commas (Cloud Run `--set-env-vars` is comma-delimited).
- For secrets, prefer Secret Manager: add a `--set-secrets` flag in the deploy step of `cloudbuild.yaml`.
