from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SourceSelection(BaseModel):
    openalex: bool = True
    crossref: bool = True
    scopus: bool = False
    wos: bool = False


class RunValidationRequest(BaseModel):
    upload_id: str
    force_refresh: bool = False
    duplicate_threshold: int = Field(default=85, ge=0, le=100)
    sources: SourceSelection = SourceSelection()
    scopus_api_key: str | None = None
    wos_api_key: str | None = None


class JobStatusResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed"]
    progress: float
    processed_records: int
    total_records: int
    message: str
    error: str | None = None


class UploadResponse(BaseModel):
    upload_id: str
    filename: str
    record_count_hint: int | None = None


class RunValidationResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed"]


class CacheStats(BaseModel):
    hits: int = 0
    misses: int = 0
    bypassed: int = 0


class CacheSourceStats(BaseModel):
    openalex: CacheStats = CacheStats()
    crossref: CacheStats = CacheStats()
    scopus: CacheStats = CacheStats()
    wos: CacheStats = CacheStats()
