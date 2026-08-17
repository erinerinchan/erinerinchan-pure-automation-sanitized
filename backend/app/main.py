from __future__ import annotations

import json
import uuid
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .cache import SqliteResponseCache
from .jobs import JobRegistry, JobState
from .lookup_service import lookup_record
from .schemas import JobStatusResponse, RunValidationRequest, RunValidationResponse, UploadResponse
from .validation_runner import run_validation_job, spawn_validation_thread

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
JOBS_DIR = DATA_DIR / "jobs"
CACHE_DB_PATH = DATA_DIR / "cache.sqlite3"
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
TEMPLATES_DIR = APP_DIR / "templates"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="PURE Record Validation API", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

cache = SqliteResponseCache(CACHE_DB_PATH)
registry = JobRegistry()


class LookupRequest(BaseModel):
    query: str
    query_type: str = "auto"


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    index_path = TEMPLATES_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/lookup")
def lookup(payload: LookupRequest) -> dict:
    try:
        return lookup_record(
            query=payload.query,
            query_type=payload.query_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/sample-file")
def sample_file() -> FileResponse:
    project_root = Path(__file__).resolve().parents[2]
    sample_path = project_root / "sample_data" / "pure_records.csv"
    if not sample_path.exists():
        raise HTTPException(status_code=404, detail="Sample file not found")
    return FileResponse(sample_path, filename="pure_records.csv", media_type="text/csv")


@app.post("/api/uploads", response_model=UploadResponse)
async def upload_records(file: UploadFile = File(...)) -> UploadResponse:
    extension = Path(file.filename or "").suffix.lower()
    if extension not in {".csv", ".xlsx", ".xls"}:
        raise HTTPException(status_code=400, detail="Only CSV/XLS/XLSX files are supported")

    upload_id = str(uuid.uuid4())
    destination = UPLOAD_DIR / f"{upload_id}{extension}"
    file_bytes = await file.read()
    destination.write_bytes(file_bytes)

    record_count_hint = None
    try:
        if extension in {".xlsx", ".xls"}:
            record_count_hint = len(pd.read_excel(destination))
        else:
            record_count_hint = len(pd.read_csv(destination))
    except Exception:
        record_count_hint = None

    return UploadResponse(upload_id=upload_id, filename=file.filename or destination.name, record_count_hint=record_count_hint)


@app.post("/api/jobs", response_model=RunValidationResponse)
def start_validation(payload: RunValidationRequest) -> RunValidationResponse:
    upload_path = None
    for ext in [".csv", ".xlsx", ".xls"]:
        maybe = UPLOAD_DIR / f"{payload.upload_id}{ext}"
        if maybe.exists():
            upload_path = maybe
            break

    if upload_path is None:
        raise HTTPException(status_code=404, detail="Upload not found")

    job_id = str(uuid.uuid4())
    registry.create(JobState(job_id=job_id, upload_id=payload.upload_id))

    def _run_wrapped() -> None:
        try:
            run_validation_job(
                job_id=job_id,
                upload_path=upload_path,
                registry=registry,
                cache=cache,
                data_dir=DATA_DIR,
                sources=payload.sources,
                force_refresh=payload.force_refresh,
                duplicate_threshold=payload.duplicate_threshold,
                scopus_api_key=payload.scopus_api_key,
                wos_api_key=payload.wos_api_key,
            )
        except Exception as exc:
            registry.update(
                job_id,
                status="failed",
                progress=1.0,
                message="Validation failed",
                error=str(exc),
            )

    spawn_validation_thread(target=_run_wrapped, kwargs={})
    return RunValidationResponse(job_id=job_id, status="queued")


@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str) -> JobStatusResponse:
    job = registry.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        progress=job.progress,
        processed_records=job.processed_records,
        total_records=job.total_records,
        message=job.message,
        error=job.error,
    )


@app.get("/api/jobs/{job_id}/results")
def get_job_results(job_id: str, duplicate_threshold: int = Query(default=85, ge=0, le=100)) -> dict:
    job = registry.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed" or not job.result_path:
        raise HTTPException(status_code=400, detail="Job is not completed")

    payload = json.loads(job.result_path.read_text(encoding="utf-8"))
    rows = payload.get("rows", [])
    for row in rows:
        row["is_duplicate"] = row.get("duplicate_score_max", 0) >= duplicate_threshold
        if not row.get("doi"):
            row["status"] = "missing"
        elif row.get("is_duplicate"):
            row["status"] = "duplicate"
        elif row.get("mismatches"):
            row["status"] = "discrepancy"
        else:
            row["status"] = "verified"

    summary = {
        "total": len(rows),
        "verified": sum(1 for row in rows if row.get("status") == "verified"),
        "discrepancies": sum(1 for row in rows if row.get("status") == "discrepancy"),
        "duplicates": sum(1 for row in rows if row.get("status") == "duplicate"),
        "missing_doi": sum(1 for row in rows if not row.get("doi")),
        "unresolved": sum(1 for row in rows if not row.get("comparison")),
    }

    payload["rows"] = rows
    payload["summary"] = summary
    payload["duplicate_threshold"] = duplicate_threshold
    return payload


@app.get("/api/jobs/{job_id}/excel")
def download_excel(job_id: str) -> FileResponse:
    job = registry.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed" or not job.excel_path or not job.excel_path.exists():
        raise HTTPException(status_code=400, detail="Excel file not available")

    return FileResponse(
        path=job.excel_path,
        filename=f"pure_review_{job_id}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
