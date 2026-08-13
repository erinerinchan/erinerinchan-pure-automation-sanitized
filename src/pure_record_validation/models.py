"""Typed models for PURE publication record validation."""

from dataclasses import dataclass, field


@dataclass(slots=True)
class PureRecord:
    """Single PURE publication record used for automated checks."""

    record_id: str
    author_name: str
    title: str
    journal: str
    publication_year: int
    original_filename: str
    file_extension: str
    page_count: int
    abstract: str


@dataclass(slots=True)
class ValidationResult:
    """Validation and transformation output for one record."""

    record_id: str
    original_filename: str
    proposed_filename: str
    extracted_title_tokens: list[str]
    is_valid: bool
    errors: list[str] = field(default_factory=list)
