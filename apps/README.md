# ⭐ Data Modeler Agent

An agent that reads a **Silver** BigQuery dataset, analyzes its tables and
metadata, and **recommends a Gold-layer star schema** (facts, dimensions,
keys, relationships, partitioning/clustering, column mappings and
transformation SQL). It generates a full proposal with an ERD and SQL scripts,
saves the artifacts to a GCS bucket, and — **only after you approve in the
UI** — creates the Gold dataset in BigQuery and loads the data.

Nothing is created or overwritten in BigQuery without explicit approval.

---

## Features

- Connects to BigQuery and inspects a Silver dataset: table names, schemas,
  column types, row counts, sample rows, and declared constraints.
- Identifies entities, facts, dimensions, primary/foreign keys and
  relationships and designs a **Kimball star schema**.
- Produces:
  - Fact & dimension tables with surrogate/business keys
  - Primary key & foreign key definitions (BigQuery `NOT ENFORCED`)
  - Recommended partitioning & clustering
  - Silver → Gold column mappings and transformation SQL
  - Data quality assumptions and per-table rationale
  - A Mermaid **ERD**
- **Review-before-execution**: generates a proposal only, until you approve.
- Saves artifacts to GCS: proposal JSON, ERD (`.mmd`), DDL SQL, transformation
  SQL, summary report, and (after execution) logs + results.
- **Two modeling engines**:
  - **Gemini Flash** (set `GEMINI_API_KEY`) — richer reasoning & transform SQL.
  - **Heuristic** fallback — deterministic, works fully offline.

---

## Project layout

```
data-modeler-agent/
├── app.py                      # Streamlit UI (analyze → review → approve)
├── src/
│   ├── config.py               # Env-driven settings (no hardcoded ids)
│   ├── logging_config.py       # Logging + in-memory buffer for the UI/GCS
│   ├── validation.py           # Input validation before any GCP call
│   ├── bigquery_client.py      # BigQuery inspect + execute wrapper
│   ├── analyzer.py             # Silver dataset inspection
│   ├── models.py               # Pydantic proposal contract
│   ├── modeler.py              # LLM + heuristic star-schema modelers
│   ├── erd.py                  # Mermaid ERD generation + HTML render
│   ├── ddl_generator.py        # BigQuery DDL generation
│   ├── transform_generator.py  # Silver → Gold transformation SQL
│   ├── report.py               # Summary report + artifact bundle
│   ├── gcs_storage.py          # GCS uploads
│   └── executor.py             # Approval-gated dataset/table create + load
├── requirements.txt
├── .env.example
├── Dockerfile
└── README.md
```

---

## Prerequisites

- Python 3.11+ (3.12 recommended)
- A GCP project with the **BigQuery** and **Cloud Storage** APIs enabled
- A Silver dataset in BigQuery and a GCS bucket for outputs
- Credentials with at least:
  - `roles/bigquery.dataViewer` on the Silver dataset (read/inspect)
  - `roles/bigquery.dataEditor` + `roles/bigquery.jobUser` (create Gold + load)
  - `roles/storage.objectAdmin` on the output bucket
- *(Optional)* A Google Gemini API key (from [AI Studio](https://aistudio.google.com/apikey)) to enable the Gemini modeling engine

---

## Local setup

```bash
# 1. Clone / enter the project
cd data-modeler-agent

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env               # then edit .env

# 5. Authenticate to GCP (choose one)
#    a) Application Default Credentials (recommended for local dev):
gcloud auth application-default login
#    b) or set GOOGLE_APPLICATION_CREDENTIALS in .env to a service-account key

# 6. Run
streamlit run app.py
```

Open the URL Streamlit prints (default http://localhost:8501).

### Configuration (`.env`)

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_GENAI_USE_VERTEXAI` | optional | `true` to use **Vertex AI** (auth via ADC / service account; needs `roles/aiplatform.user`). Recommended on GCP. |
| `GOOGLE_CLOUD_PROJECT` | optional | Vertex AI project (defaults to the source project entered in the UI). |
| `GOOGLE_CLOUD_LOCATION` | optional | Vertex AI region (default `us-central1`). |
| `GEMINI_API_KEY` | optional | AI Studio API key backend, used only when Vertex is **not** enabled. (`GOOGLE_API_KEY` also accepted.) |
| `GEMINI_MODEL` | optional | Model id (default `gemini-2.5-flash`). |

> If neither Vertex AI nor an API key is configured, the deterministic
> heuristic modeler is used.
| `GOOGLE_APPLICATION_CREDENTIALS` | optional | Path to a service-account key. Omit to use ADC / attached identity. |
| `BQ_LOCATION` | optional | BigQuery location (default `US`). Must match your datasets. |
| `DEFAULT_GCP_PROJECT` / `DEFAULT_SILVER_DATASET` / `DEFAULT_GOLD_DATASET` / `DEFAULT_GCS_BUCKET` | optional | Pre-fill the form only. Users can override. |
| `SAMPLE_ROW_LIMIT` | optional | Rows sampled per table (default 20). |
| `MAX_TABLES` | optional | Max tables inspected (default 100). |
| `LOG_LEVEL` | optional | `DEBUG`/`INFO`/`WARNING` (default `INFO`). |

> Project id, dataset names and bucket are **never hardcoded** — they come from
> the UI (the `DEFAULT_*` values are only optional convenience pre-fills).

---

## Using the app

1. In the sidebar, enter **Source GCP project ID**, **Silver dataset**,
   **Gold dataset**, and **GCS bucket** (plus optional business domain and a
   comma-separated table filter).
2. Click **🔍 Analyze Silver Dataset**. The agent inspects Silver, designs the
   star schema, builds artifacts, and uploads the **proposal** to GCS.
3. Review the **Proposal**, **ERD**, **Tables**, **SQL** and **Artifacts** tabs.
4. Tick the confirmation box and click **✅ Approve and Create Gold Dataset**.
   The agent creates the dataset + tables, runs the transformations, uploads
   execution logs/results, and shows a per-step status table.

### Artifacts written to GCS

```
gs://<bucket>/data-modeler/<gold_dataset>/<timestamp>/
├── proposal/
│   ├── proposal.json          # full structured proposal
│   ├── erd.mmd                # Mermaid ERD
│   ├── gold_ddl.sql           # BigQuery DDL
│   ├── transformations.sql    # Silver → Gold SQL
│   └── summary_report.md      # human-readable summary
└── execution/                 # created only after approval
    ├── execution_summary.txt
    ├── execution_log.txt
    └── result.json
```

---

## Deploy to Cloud Run

Set your variables:

```bash
export PROJECT_ID="your-gcp-project"
export REGION="us-central1"
export REPO="data-modeler"
export SERVICE="data-modeler-agent"
export IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$SERVICE:latest"
```

Enable APIs and create an Artifact Registry repo (once):

```bash
gcloud services enable run.googleapis.com bigquery.googleapis.com \
    storage.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
    --project "$PROJECT_ID"

gcloud artifacts repositories create "$REPO" \
    --repository-format=docker --location="$REGION" --project "$PROJECT_ID"
```

Create a runtime service account with the needed roles:

```bash
export SA="data-modeler-sa"
gcloud iam service-accounts create "$SA" --project "$PROJECT_ID"

export SA_EMAIL="$SA@$PROJECT_ID.iam.gserviceaccount.com"
for ROLE in roles/bigquery.dataEditor roles/bigquery.jobUser roles/storage.objectAdmin; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" --role="$ROLE"
done
```

Build, push and deploy:

```bash
gcloud builds submit --tag "$IMAGE" --project "$PROJECT_ID"

gcloud run deploy "$SERVICE" \
    --image "$IMAGE" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --service-account "$SA_EMAIL" \
    --allow-unauthenticated \
    --set-env-vars "BQ_LOCATION=US,GEMINI_MODEL=gemini-2.5-flash,GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=$REGION"
```

> Using **Vertex AI** (as above), no API key/secret is needed — the runtime
> service account authenticates. Grant it `roles/aiplatform.user` and enable
> `aiplatform.googleapis.com`. For internal use, drop `--allow-unauthenticated`.
>
> To use the **AI Studio** key backend instead, omit the Vertex env vars and add
> `--set-secrets "GEMINI_API_KEY=gemini-api-key:latest"` after storing the key in
> Secret Manager and granting the SA `roles/secretmanager.secretAccessor`.

On Cloud Run you do **not** set `GOOGLE_APPLICATION_CREDENTIALS`; the attached
service account is used automatically.

---

## Design notes & extensibility

- **DDL is generated deterministically** from a validated Pydantic model, so it
  is always consistent. Only the transformation SQL body can come from the LLM.
- **Safety**: dataset/table creation and loads live in `executor.py` and only
  run after explicit UI approval. `CREATE TABLE IF NOT EXISTS` avoids clobbering
  existing tables; loads use `CREATE OR REPLACE TABLE ... AS`.
- **Adding domains**: the modeler is prompt/heuristic driven and schema-agnostic.
  To specialise, extend `SYSTEM_PROMPT` in `modeler.py` or add domain rules to
  `HeuristicModeler`. The `Proposal` contract in `models.py` is the stable
  extension point.

## Troubleshooting

- **"Silver dataset was not found"** — check the project id, dataset name and
  that `BQ_LOCATION` matches the dataset's region.
- **GCS access errors** — the bucket must exist and the identity needs
  `storage.objectAdmin` (or equivalent) on it.
- **Heuristic engine used unexpectedly** — set `GEMINI_API_KEY`; the sidebar
  shows which engine is active.
- **Transformation SQL marked "TODO"** — the heuristic modeler could not infer a
  mapping; review/edit before loading, or use the LLM engine.
