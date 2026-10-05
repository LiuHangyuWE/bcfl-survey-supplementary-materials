#!/usr/bin/env python3
"""Screen exported IEEE Xplore metadata against the BC-FL search query."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import hashlib
from pathlib import Path

if __package__:
    from .query_screening import (
        make_report, parse_fields, save_results, screen_records, split_records,
    )
else:
    from query_screening import (
        make_report, parse_fields, save_results, screen_records, split_records,
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="UTF-8 RIS file to screen")
    parser.add_argument(
        "--output-dir", type=Path, help="New directory for results and audit files",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Replace the input after saving a backup; requires --output-dir",
    )
    parser.add_argument(
        "--expected-sha256", help="Optional checksum of the expected input",
    )
    parser.add_argument(
        "--require-abstract", action="store_true",
        help="Fail the audit if a retained record has an empty AB field",
    )
    args = parser.parse_args(argv)
    if args.apply and args.output_dir is None:
        parser.error("--apply requires --output-dir")
    source = args.input.expanduser()
    try:
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if args.expected_sha256 and digest != args.expected_sha256.lower():
            raise ValueError("The input does not match --expected-sha256.")
        records = split_records(data)
        result = screen_records(records)
        if args.require_abstract:
            missing = [
                row["original_position"]
                for row, record in zip(result.decisions, records)
                if row["decision"] == "retain"
                and not any(value.strip() for value in parse_fields(record)["AB"])
            ]
            if missing:
                raise ValueError(f"Retained records have empty abstracts: {missing}")
        if args.output_dir is not None:
            save_results(
                result, data, source, args.output_dir.expanduser(), apply=args.apply,
            )
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(make_report(result), end="")


if __name__ == "__main__":
    main()
