# PURE record validation automation (sanitized)

Python utilities that automate publication-record validation checks and standardized filename generation for PURE workflows.

## The problem
Manual PURE record validation took about 10 minutes per submission. Typical repeated steps were:

1. Open each record and check required metadata fields.
2. Verify attachment format and basic document constraints.
3. Normalize naming patterns for consistency and traceability.
4. Spot missing or inconsistent values before final validation.
5. Record issues for manual correction.

## The approach
This sanitized project mirrors that workflow using synthetic data:

1. Load publication records from CSV.
2. Validate each record against explicit rules:
3. Check record ID format.
4. Check attachment type (PDF only).
5. Check minimum page count.
6. Check title length and journal presence.
7. Check abstract minimum length.
8. Generate a standardized output filename.
9. Extract metadata-like filename tokens for quick review.
10. Print a pass/fail report with concrete error messages.

## Impact

Time saved per term:

$10 \text{ minutes} \times N \text{ submissions} = \frac{10N}{60} \text{ hours saved}$

Replace $N$ with your submission count per term.

## Before / after

| Before (manual) | After (automated) |
|---|---|
| Open each record and scan fields by eye | Load all records in one run |
| Manually check formatting and constraints | Apply rule checks automatically |
| Rename files one at a time | Generate standardized filenames consistently |
| Note issues in ad-hoc comments | Output explicit validation errors per record |
| Repeat process for every submission | Reuse one deterministic pipeline |

## How to run on sample data

```bash
python -m src.pure_record_validation.main --input sample_data/pure_records.csv
```

## Sanitization note

Sanitized version of a production script used at a university library; all data shown is synthetic.
