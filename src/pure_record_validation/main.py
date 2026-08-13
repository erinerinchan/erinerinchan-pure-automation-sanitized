"""Command-line entrypoint for validating sample PURE publication records."""

from __future__ import annotations

import argparse
from pathlib import Path

from .processor import load_records, process_records


def run(input_csv: Path) -> int:
    """Run the processing workflow and print a concise report."""
    records = load_records(input_csv)
    results = process_records(records)

    print("PURE record validation report")
    print("=" * 30)

    valid_count = 0
    for result in results:
        status = "PASS" if result.is_valid else "FAIL"
        print(f"[{status}] {result.record_id}")
        print(f"  original : {result.original_filename}")
        print(f"  renamed  : {result.proposed_filename}")
        print(f"  tokens   : {', '.join(result.extracted_title_tokens)}")

        if result.errors:
            for error in result.errors:
                print(f"  error    : {error}")
        else:
            valid_count += 1

    print("-" * 30)
    print(f"Validated records: {valid_count}/{len(results)}")
    return 0


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Validate sanitized PURE sample records.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("sample_data") / "pure_records.csv",
        help="Path to sample PURE records CSV.",
    )
    return parser.parse_args()


def main() -> int:
    """Program entrypoint."""
    args = parse_args()
    return run(args.input)


if __name__ == "__main__":
    raise SystemExit(main())
