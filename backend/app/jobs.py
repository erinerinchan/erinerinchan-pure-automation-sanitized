from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class JobState:
    job_id: str
    upload_id: str
    status: str = "queued"
    progress: float = 0.0
    processed_records: int = 0
    total_records: int = 0
    message: str = "Queued"
    error: str | None = None
    result_path: Path | None = None
    excel_path: Path | None = None


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, JobState] = {}
        self._lock = threading.Lock()

    def create(self, job: JobState) -> None:
        with self._lock:
            self._jobs[job.job_id] = job

    def get(self, job_id: str) -> JobState | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in fields.items():
                setattr(job, key, value)
