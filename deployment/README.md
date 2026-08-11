# Cloud Run Deployment via Cloud Build (2nd-gen, config-driven)

**git push → Cloud Build → Artifact Registry → Cloud Run.** Every dynamic value
lives in `config.json`. The pipeline reads that file itself, so the trigger needs
no substitutions.

## Repo layout assumed

```
main/                     # GitHub repo (default branch: main)
├── app/                  # your application + Dockerfile   <- service.sourceDir
│   └── Dockerfile
└── deployment/           # these files                      <- committed to the repo
    ├── config.json
    ├── cloudbuild.yaml   # <- trigger.buildConfigPath = deployment/cloudbuild.yaml
    ├── _config.sh
    ├── setup.sh
    └── deploy.sh
```

## Files

| File | Purpose |
|------|---------|
| `config.json` | Single source of truth. |
| `cloudbuild.yaml` | Build → push → deploy; parses `config.json` at build time. |
| `_config.sh` | Loader for the local scripts (needs `jq`). |
| `setup-connection.sh` | **Run first, once.** Creates the host connection + Cloud Build repository (may require GitHub OAuth). |
| `setup.sh` | Bootstrap: APIs, registry, IAM/permissions, trigger. Verifies (doesn't create) the connection/repo. |
| `deploy.sh` | Manual one-off deploy. |

## config.json — key fields

- `project.id`, `region` — GCP project and region.
- `artifactRegistry.location`/`repo` — image host is `<location>-docker.pkg.dev`.
- `service.name` — Cloud Run service name.
- `service.sourceDir` — folder with the Dockerfile, **relative to repo root** (`app`).
- `service.port` — must match what the container listens on (`$PORT`).
- `trigger.connection` — 2nd-gen host connection name (`GitHub`).
- `trigger.repositoryId` — linked repository resource id (`main`).
- `trigger.buildConfigPath` — path to this yaml **in the repo** (`deployment/cloudbuild.yaml`).
- `trigger.github.owner`/`repo` — used to link the repo (`owner` must be set).

## One-time setup

1. **Host connection** (once, in Console — the OAuth step can't be scripted):
   Cloud Build → Repositories → 2nd gen → Create host connection (region `us-central1`).
   Put its name in `trigger.connection`.
2. Fill `config.json` — especially `trigger.github.owner` and `service.name`.
3. **Commit & push** `deployment/` to GitHub (the trigger reads the config from GitHub):
   ```bash
   cd .. && git add deployment apps && git commit -m "add deploy pipeline" && git push
   ```
4. Create the connection + Cloud Build repository (once; may prompt GitHub OAuth):
   ```bash
   cd deployment && ./setup-connection.sh
   ```
5. Bootstrap the rest (APIs, registry, permissions, trigger):
   ```bash
   ./setup.sh
   ```

## Deploy

- **Automatic:** push to a branch matching `trigger.branchPattern` (`^main$`).
- **Manual:** `./deploy.sh`

## Rollback

```bash
gcloud run services update-traffic my-service --region=us-central1 \
  --to-revisions=PREVIOUS_REVISION=100
```

## Notes

- Build context is `service.sourceDir` (`app`), so the Dockerfile can only `COPY`
  files inside `app/`. If it needs repo-root files, set `sourceDir` to `.`.
- Requires `gcloud` (authenticated) and `jq` for the local scripts.
