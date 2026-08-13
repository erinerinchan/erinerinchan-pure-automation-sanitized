"""Core processing logic: rename rules, metadata parsing, and validation."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from .models import PureRecord, ValidationResult

RECORD_ID_PATTERN = re.compile(r"^PR-\d{4}-\d{4}$")
ALLOWED_EXTENSIONS = {".pdf"}
MIN_PAGE_COUNT = 15
MAX_TITLE_LENGTH = 180


def load_records(csv_path: Path) -> list[PureRecord]:
    """Load PURE publication records from a CSV file."""
    records: list[PureRecord] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            records.append(
                PureRecord(
                    record_id=row["record_id"].strip(),
                    author_name=row["author_name"].strip(),
                    title=row["title"].strip(),
                    journal=row["journal"].strip(),
                    publication_year=int(row["publication_year"]),
                    original_filename=row["original_filename"].strip(),
                    file_extension=row["file_extension"].strip().lower(),
                    page_count=int(row["page_count"]),
                    abstract=row["abstract"].strip(),
                )
            )
    return records


def extract_metadata_from_filename(filename: str) -> list[str]:
    """Extract title-like tokens from a filename for quick verification."""
    stem = Path(filename).stem.lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", stem)
    tokens = [token for token in cleaned.split() if len(token) > 2]
    return tokens[:8]


def build_output_filename(record: PureRecord) -> str:
    """Build normalized output filename used by archive conventions."""
    safe_author = re.sub(r"[^A-Za-z0-9]+", "_", record.author_name).strip("_")
    safe_title = re.sub(r"[^A-Za-z0-9]+", "_", record.title).strip("_")
    # Keep filenames readable while preventing very long path issues.
    safe_title = safe_title[:60]
    return f"{record.publication_year}_{record.record_id}_{safe_author}_{safe_title}.pdf"


def validate_record(record: PureRecord) -> ValidationResult:
    """Run format and metadata validations on one PURE record."""
    errors: list[str] = []

    if not RECORD_ID_PATTERN.match(record.record_id):
        errors.append("Record ID must match pattern PR-YYYY-NNNN.")

    if record.file_extension not in ALLOWED_EXTENSIONS:
        errors.append("File must be in PDF format.")

    if record.page_count < MIN_PAGE_COUNT:
        errors.append(f"Page count must be >= {MIN_PAGE_COUNT}.")

    if len(record.title) > MAX_TITLE_LENGTH:
        errors.append(f"Title length exceeds {MAX_TITLE_LENGTH} characters.")

    if not record.journal:
        errors.append("Journal field is required for validation workflows.")

    if len(record.abstract.split()) < 40:
        errors.append("Abstract is too short for repository policy checks.")

    proposed = build_output_filename(record)
    extracted = extract_metadata_from_filename(record.original_filename)

    return ValidationResult(
        record_id=record.record_id,
        original_filename=record.original_filename,
        proposed_filename=proposed,
        extracted_title_tokens=extracted,
        is_valid=not errors,
        errors=errors,
    )


def process_records(records: list[PureRecord]) -> list[ValidationResult]:
    """Process a list of records and return per-record validation results."""
    return [validate_record(record) for record in records]
