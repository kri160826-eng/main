# Cloud Run Deployment via Cloud Build (2nd-gen, config-driven)

**git push → Cloud Build → Artifact Registry → Cloud Run.** Every dynamic value
lives in `config.json`. The pipeline reads that file itself, so the trigger needs
no substitutions.

The 2nd-gen host connection and its linked repository are created **manually**
in the Console; the scripts only reference them by name and never create them.

## Repo layout assumed

```
main/                     # GitHub repo (default branch: main)
├── app/                  # your application + Dockerfile   <- service.sourceDir
│   └── Dockerfile
└── deployment/           # these files                      <- committed to the repo
    ├── config.json
    ├── config.example.jsonc  # annotated reference for config.json
    ├── cloudbuild.yaml   # <- trigger.buildConfigPath = deployment/cloudbuild.yaml
    ├── _config.sh
    ├── setup.sh
    └── deploy.sh
```

## Files

| File | Purpose |
|------|---------|
| `config.json` | Single source of truth (strict JSON). |
| `config.example.jsonc` | Annotated, comment-per-key reference for `config.json`. |
| `cloudbuild.yaml` | Build → push → deploy; parses `config.json` at build time. |
| `_config.sh` | Loader for the local scripts (needs `jq`). |
| `setup.sh` | Bootstrap: APIs, registry, IAM/permissions, trigger. Verifies (doesn't create) the connection/repo. |
| `deploy.sh` | Manual one-off deploy. |

## config.json — key fields

- `project.id`, `region` — GCP project and region.
- `artifactRegistry.location`/`repo` — image host is `<location>-docker.pkg.dev`.
- `service.name` — Cloud Run service name.
- `service.sourceDir` — folder with the Dockerfile, **relative to repo root** (`app`).
- `service.port` — must match what the container listens on (`$PORT`).
- `trigger.connection` — name of the manually created 2nd-gen host connection (`GitHub2`).
- `trigger.repositoryId` — id of the manually linked repository resource (`kri160826-eng-main`).
- `trigger.buildConfigPath` — path to this yaml **in the repo** (`deployment/cloudbuild.yaml`).
- `trigger.serviceAccount` / `trigger.roles` — SA the build runs as + its roles.
- `trigger.branchPattern` — branches whose pushes fire the trigger (`^main$`).

## One-time setup

1. **Host connection + repository (manual, in Console):**
   Cloud Build → Repositories → 2nd gen → Create host connection (region
   `us-central1`, authorize the GitHub App), then **Link repository** to link
   your GitHub repo under that connection.
   Put the connection name in `trigger.connection` and the linked repository id
   in `trigger.repositoryId`.
2. Fill the rest of `config.json` (e.g. `service.name`). See `config.example.jsonc`.
3. **Commit & push** `deployment/` to GitHub (the trigger reads the config from GitHub):
   ```bash
   cd .. && git add deployment apps && git commit -m "add deploy pipeline" && git push
   ```
4. Bootstrap the rest (APIs, registry, permissions, trigger). This verifies the
   connection + repository exist, then creates everything else:
   ```bash
   cd deployment && ./setup.sh
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
