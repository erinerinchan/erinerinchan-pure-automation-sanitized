"""Typed models for e-thesis processing records."""

from dataclasses import dataclass, field


@dataclass(slots=True)
class ThesisRecord:
    """Single thesis submission record used for automated checks."""

    student_id: str
    author_name: str
    title: str
    degree_program: str
    submission_term: str
    original_filename: str
    file_extension: str
    page_count: int
    abstract: str


@dataclass(slots=True)
class ValidationResult:
    """Validation and transformation output for one record."""

    student_id: str
    original_filename: str
    proposed_filename: str
    extracted_title_tokens: list[str]
    is_valid: bool
    errors: list[str] = field(default_factory=list)
