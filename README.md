# E-thesis processing automation (sanitized)

Python utilities that automate e-thesis file validation, metadata checks, and standardized output naming.

## The problem
Manual e-thesis processing took about 10 minutes per submission. In practice, staff had to repeat the same steps:

1. Check filename conventions and rename files.
2. Validate submission format (for example, PDF and minimum page expectations).
3. Verify metadata completeness and consistency.
4. Extract basic metadata clues from filenames.
5. Prepare normalized output values for downstream repository entry.

## The approach
This sanitized project demonstrates the same workflow as production, with synthetic data only:

1. Load thesis records from CSV.
2. Validate each record against rules:
   - student ID format
   - allowed file extension
   - page count threshold
   - title length threshold
   - abstract minimum length
3. Build a standardized output filename for each thesis.
4. Extract title-like tokens from the original filename for quick manual verification.
5. Print a concise pass/fail report per record.

## Impact
Time saved per term:

- Formula: $10 \text{ minutes} \times N \text{ submissions} = \frac{10N}{60} \text{ hours saved}$
- Fill-in value: replace $N$ with your term submission count.

## Before / after

| Before (manual) | After (automated) |
|---|---|
| Read each submission file manually | Load all records from one CSV |
| Manually rename files to standard format | Generate standardized filenames consistently |
| Check file type and page length one by one | Run rule checks in one pass |
| Spot metadata issues by inspection | Get explicit validation errors per record |
| Prepare outputs by hand | Produce a repeatable processing report |

## How to run

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Run on synthetic sample data:

```bash
python -m src.ethesis_processing.main --input sample_data/thesis_records.csv
```

## Sanitization note
Sanitized version of a production script used at a university library; all data shown is synthetic.

## Sanitization changes applied
Potentially sensitive elements from the original working scripts were identified and replaced or isolated:

1. Internal portal URL replaced with placeholder config variable pattern (for publishable code, use values like `PORTAL_BASE_URL`).
2. Local machine paths (for example Windows user profile paths) removed from publishable workflow and replaced by relative paths.
3. Institutional identifiers (organization-specific portal names and labels) generalized to neutral wording.
4. Contact details in request headers (for example direct email addresses) should be moved to environment configuration before publication.
5. Browser profile/session artifacts are excluded from version control via `.gitignore`.
