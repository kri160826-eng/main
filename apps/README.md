# Water Meter OCR

A review-first Streamlit application that uses Gemini on Vertex AI to extract
analog water-meter readings and handwritten customer/connection labels.

The application separates black whole-number digits from red decimal digits,
derives the final reading in code, flags uncertain results, lets an operator
correct the fields, and exports a reviewed CSV.

## What improved

- Validates uploads, applies EXIF rotation, and resizes large images consistently.
- Preserves leading zeros and decimal precision; unreadable readings remain blank
  instead of being silently converted to `0`.
- Calculates `reading_m3` in Python rather than accepting model arithmetic.
- Adds confidence and `review_required` fields with a configurable threshold.
- Caches successful results in the Streamlit session to avoid accidental repeat
  API calls during UI reruns.
- Adds an editable review table and recalculates corrected readings on CSV export.
- Adds offline tests, dependency ranges, credential exclusions, a non-root
  container, a health check, and an optional Cloud Build deployment definition.

## Project layout

| File | Purpose |
| --- | --- |
| `app.py` | Upload, analysis, review, correction, and CSV export UI |
| `meter_reader.py` | Image validation and Gemini extraction |
| `schema.py` | Structured model response and deterministic reading calculation |
| `config.py` | Environment validation and cached Vertex AI client |
| `test_reader.py` | Optional live smoke test against sample images |
| `tests/` | Offline unit tests; no Vertex AI request is made |
| `cloudbuild.yaml` | Container build and Cloud Run deployment pipeline |

## Prerequisites

- Python 3.12
- A Google Cloud project with billing enabled
- Vertex AI API enabled: `aiplatform.googleapis.com`
- A user or runtime service account allowed to call Vertex AI

## Local setup

```bash
python -m venv .venv
```

Activate the environment:

```bash
# Windows PowerShell
.venv\Scripts\Activate.ps1

# macOS/Linux
source .venv/bin/activate
```

Install dependencies and create the local configuration:

```bash
pip install -r requirements.txt
cp .env.example .env
```

On Windows Command Prompt, use `copy .env.example .env` instead of `cp`.

Set your project in `.env`:

```dotenv
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=us-central1
GEMINI_MODEL=gemini-2.5-flash
CONFIDENCE_THRESHOLD=0.80
MAX_UPLOAD_MB=15
MAX_IMAGE_EDGE=2400
```

Authenticate locally with Application Default Credentials:

```bash
gcloud auth application-default login
```

Run the application:

```bash
streamlit run app.py
```

## Operator workflow

1. Upload one or more clear PNG/JPEG meter photos.
2. Select **Analyze photos**. Results are reused during the browser session.
3. Review every row marked **Review required**, and correct the digit strings or
   handwritten label in the table.
4. Clear the review checkbox after confirming a row.
5. Download the reviewed CSV. `reading_m3` is recalculated from the edited black
   and red digit fields at export time.

> Model confidence is a review aid, not a calibrated probability. Keep a human
> review step for billing, compliance, or customer-facing decisions.

## Tests and linting

```bash
pip install -r requirements-dev.txt
pytest
ruff check .
```

The tests use a fake model client and do not require Google Cloud credentials.
To make live calls against images in `samples/`:

```bash
python test_reader.py
python test_reader.py "samples/Screenshot 2026-07-22 102217.png"
```

## Run with Docker

```bash
docker build -t water-meter-ocr .
docker run --rm -p 8080:8080 \
  -e GOOGLE_CLOUD_PROJECT=your-gcp-project-id \
  -v "$HOME/.config/gcloud:/home/appuser/.config/gcloud:ro" \
  water-meter-ocr
```

Open `http://localhost:8080`.

## Deploy to Cloud Run

Create the Artifact Registry repository once:

```bash
gcloud artifacts repositories create apps \
  --repository-format=docker \
  --location=us-central1
```

Deploy directly from source:

```bash
gcloud run deploy water-meter-ocr \
  --source=. \
  --region=us-central1 \
  --set-env-vars=GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID \
  --no-allow-unauthenticated
```

For continuous deployment, connect the repository to Cloud Build and use
`cloudbuild.yaml`. Its defaults are `us-central1`, Artifact Registry repository
`apps`, and Cloud Run service `water-meter-ocr`; override substitutions when your
names differ.

Grant the Cloud Run runtime service account only the permissions required to
invoke Vertex AI. Never bake a service-account JSON key into the image.

## Accuracy guidance

- Capture the meter straight-on with the complete digit strip visible.
- Avoid glare, shadows, motion blur, and fingers covering the display.
- Keep red digits distinguishable from black digits; do not use grayscale photos.
- Use the handwritten label as a separate identifier, never as a substitute for
  the meter reading.
- Build a labelled test set from real field photos and track digit-level accuracy,
  full-reading accuracy, and manual-review rate before production use.
