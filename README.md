# PURE Publication Validation Web App

## 1. What The App Does

This project provides a web interface and API to look up publication metadata by DOI or title and prepare structured output for HKUST PURE workflows.

It includes:

- A FastAPI backend (`backend/app/main.py`)
- A browser UI served by FastAPI (no Node required for the lookup UI)
- Metadata aggregation from OpenAlex and Scopus (`pure_metadata.py`)
- Optional Web of Science support when API key is available

Live production URL:

- https://pure-validation-api.onrender.com

## 2. Local Run Steps

From repository root:

1. Install backend dependencies:

```bash
pip install -r backend/requirements.txt
```

2. Run the API:

```bash
python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

3. Open:

- http://localhost:8000

If port `8000` is occupied locally, run on `8001` instead.

## 3. Render Deployment Steps

This repo includes a Render Blueprint file: `render.yaml`.

Deployment flow:

1. Connect repo in Render.
2. Deploy using Blueprint (`render.yaml`).
3. Ensure environment variables are set (see section 4).
4. Trigger deploy (or wait for auto deploy on push to `main`).

Configured service:

- Web service name: `pure-validation-api`
- Runtime: Docker (`backend/Dockerfile`)
- Health endpoint: `GET /api/health`

## 4. Required Environment Variables

- `SCOPUS_API_KEY` (required for Scopus metadata and Scopus IDs)
- `WOS_API_KEY` (optional)
- `OPENALEX_API_KEY` (optional; OpenAlex works without it)

Notes:

- Without `SCOPUS_API_KEY`, Scopus fields and Scopus IDs will be empty.
- `WOS_API_KEY` is only needed for WoS API enrichment.

## 5. API Endpoints

Primary lookup endpoints:

- `POST /api/lookup`
  - Body: `{"query": "<doi-or-title>", "query_type": "doi|title|auto"}`
  - Returns resolved metadata, external IDs, and structured report output.

- `GET /api/health`
  - Returns service health (`{"ok": true}`).

Additional batch endpoints remain available in the backend (uploads/jobs/results/excel) for broader validation workflows.

## 6. Known Behavior (Scopus ID Retrieval)

Scopus ID retrieval depends on Scopus key entitlement.

The app now attempts Scopus search with fallback views:

1. `COMPLETE`
2. `STANDARD`
3. default view

This fallback exists because some Scopus keys are not authorized for `COMPLETE` and return:

- `AUTHORIZATION_ERROR`

When authorized for at least one view, the app extracts Scopus identifiers from available fields (`scopus_doc_id`, `eid`, and link-derived fallback patterns).

## 7. Live URL

- https://pure-validation-api.onrender.com

## Project Notes

- Core metadata logic lives in `pure_metadata.py`.
- Backend source is under `backend/app/`.
- Render deployment config is in `render.yaml`.
