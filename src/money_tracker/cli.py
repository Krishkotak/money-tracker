"""Small local command for inspecting parser results without printing transaction details."""

import argparse
from pathlib import Path

from .database import connect, import_statements, initialize
from .parsers import PARSERS
from .parsers import parse_statement


def _money(paise):
    return "₹{:,.2f}".format(paise / 100)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Parse and reconcile a supported local statement.")
    parser.add_argument("source", choices=sorted(PARSERS), help="Statement source format")
    parser.add_argument("file", type=Path, help="Path to the statement file")
    args = parser.parse_args(argv)
    statement = PARSERS[args.source](args.file)
    credits = sum(row.amount_paise for row in statement.entries if row.direction == "credit")
    debits = sum(row.amount_paise for row in statement.entries if row.direction == "debit")
    print("Source: {}".format(statement.source.upper()))
    print("Statement period: {} to {}".format(statement.period_start, statement.period_end))
    print("Rows parsed: {} ({} credits, {} debits)".format(
        len(statement.entries),
        sum(row.direction == "credit" for row in statement.entries),
        sum(row.direction == "debit" for row in statement.entries),
    ))
    print("Credits: {} | Debits: {}".format(_money(credits), _money(debits)))
    if statement.findings:
        print("Validation findings:")
        for finding in statement.findings:
            print("- [{}] {}: {}".format(finding.severity, finding.code, finding.message))
    else:
        print("Validation: passed")
    return 0 if not any(finding.severity == "error" for finding in statement.findings) else 2


def import_main(argv=None):
    parser = argparse.ArgumentParser(description="Parse and save available local bank/card statements.")
    parser.add_argument("--hsbc", type=Path, help="HSBC workbook (.xls OOXML export)")
    parser.add_argument("--sbi", type=Path, help="SBI Card statement PDF")
    parser.add_argument("--kotak", type=Path, help="Kotak account CSV")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path.home() / "Library" / "Application Support" / "Personal Money Tracker" / "money-tracker.sqlite3",
        help="Local SQLite database path",
    )
    args = parser.parse_args(argv)
    requested = [(source, getattr(args, source)) for source in ("hsbc", "sbi", "kotak") if getattr(args, source)]
    if not requested:
        parser.error("Provide at least one of --hsbc, --sbi, or --kotak.")
    # Parse every selected file before opening a write transaction.
    parsed = [(path, parse_statement(path, source)) for source, path in requested]
    connection = connect(args.database)
    try:
        initialize(connection)
        result = import_statements(connection, parsed)
    finally:
        connection.close()
    print("New transactions: {}".format(result.added_transactions))
    print("Rows matched to existing transactions: {}".format(result.matched_existing_transactions))
    print("Auto-categorized by confirmed rules: {}".format(result.auto_categorized_transactions))
    print("New transactions waiting for review: {}".format(result.pending_reviews_added))
    print("Ambiguous duplicate candidates for review: {}".format(result.duplicate_reviews))
    print("Unchanged files skipped: {}".format(result.skipped_unchanged_statements))
    print("Database: {}".format(args.database.expanduser()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
