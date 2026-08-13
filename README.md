# PURE DOI Metadata Helper (Sanitized)

Extracts publication metadata from a DOI into a plain-text report and opens Scopus, Web of Science, and OpenAlex tabs for quick cross-checking.

## What this script does

The main script is [pure_metadata.py](pure_metadata.py).

1. Accepts a DOI (or title) from the user.
2. Queries metadata sources (OpenAlex and Scopus when API key is available).
3. Merges results into a structured plain-text report.
4. Saves the report to a local .txt file.
5. Opens browser tabs for cross-checking the same publication in:
	- Scopus
	- Web of Science
	- OpenAlex

## Scope and limitations

This tool does not automate full PURE record entry.

1. It does **not** fill PURE fields end-to-end.
2. It does **not** replace manual review/validation decisions in PURE.
3. It is intended to save time only on:
	- Looking up and reviewing metadata from a DOI
	- Opening Scopus, Web of Science, and OpenAlex tabs for cross-checking

## Why this exists

Manual metadata lookup and opening multiple database tabs are repetitive and time-consuming. This helper removes those two repeated steps so staff can focus on the judgment-based part of record validation.

## How to run

```bash
python pure_metadata.py
```

Then enter a DOI when prompted.

## Configuration notes

1. Scopus lookups require an API key (for example via environment variable such as SCOPUS_API_KEY).
2. If no Scopus key is present, the script still runs using OpenAlex.

## Sanitization note

Sanitized version of a production script used at a university library; all data shown is synthetic.
