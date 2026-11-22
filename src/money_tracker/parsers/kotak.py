"""Parser for Kotak's CSV account statement export."""

import csv
import re
from datetime import datetime
from pathlib import Path

from ..models import ParsedStatement, StatementEntry, StatementFormatError, ValidationFinding
from .common import assign_identity_keys, parse_date, parse_money, signed_balance


def _key(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def parse_kotak(path: Path) -> ParsedStatement:
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as source_file:
        rows = list(csv.reader(source_file))
    header_index = next(
        (i for i, row in enumerate(rows) if {"transaction date", "value date", "description", "amount", "dr / cr", "balance"}.issubset({_key(cell) for cell in row})),
        None,
    )
    if header_index is None:
        raise StatementFormatError("Kotak CSV is missing its expected transaction header.")
    headers = {}
    for i, value in enumerate(rows[header_index]):
        headers.setdefault(_key(value), i)
    account_key = "KOTAK-unknown-account"
    period_start = period_end = None
    metadata = " ".join(cell for row in rows[:header_index] for cell in row if cell)
    account_match = re.search(r"Account No\.\s*,?\s*([0-9]+)", metadata, re.I)
    if account_match:
        account_key = "KOTAK-{}".format(account_match.group(1))
    period_match = re.search(r"From\s+(\d{2}/\d{2}/\d{4})\s+To\s+(\d{2}/\d{2}/\d{4})", metadata, re.I)
    if period_match:
        period_start = parse_date(period_match.group(1), ("%d/%m/%Y",))
        period_end = parse_date(period_match.group(2), ("%d/%m/%Y",))
    raw_entries = []
    findings = []
    for row_number, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        if not row or not row[0].strip():
            continue
        if not row[0].strip().isdigit():
            if raw_entries:
                break
            continue
        try:
            posted_text = row[headers["transaction date"]].strip()
            try:
                posted_datetime = datetime.strptime(posted_text, "%d-%m-%Y %H:%M:%S")
                posted_time = posted_datetime.strftime("%H:%M:%S")
            except ValueError:
                posted_datetime = datetime.strptime(posted_text, "%d-%m-%Y")
                posted_time = None
            posted_at = posted_datetime.date()
            value_date = parse_date(row[headers["value date"]], ("%d-%m-%Y",))
            amount = parse_money(row[headers["amount"]])
            balance = parse_money(row[headers["balance"]])
        except (ValueError, IndexError) as error:
            findings.append(ValidationFinding("invalid_row", "CSV row {}: {}".format(row_number, error), "error"))
            continue
        direction_text = row[headers["dr / cr"]].strip().casefold()
        if direction_text not in ("cr", "dr"):
            findings.append(ValidationFinding("invalid_direction", "CSV row {} has an unknown debit/credit marker.".format(row_number), "error"))
            continue
        description = row[headers["description"]].strip()
        reference = row[headers["chq /ref no."]].strip() if "chq /ref no." in headers else ""
        balance_marker_index = headers["balance"] + 1
        if balance_marker_index < len(row) and row[balance_marker_index].strip().casefold() == "dr":
            balance = -balance
        counterparty_match = re.match(r"UPI/([^/]+)(?:/|$)", description, re.I)
        if not counterparty_match:
            counterparty_match = re.match(r"SentIMPS\d+([^/]+)(?:/|$)", description, re.I)
        counterparty = counterparty_match.group(1).strip() if counterparty_match else description
        raw_entries.append(
            StatementEntry(
                source="kotak",
                account_key=account_key,
                row_locator="CSV:{}".format(row_number),
                transaction_date=posted_at,
                posted_time=posted_time,
                value_date=value_date,
                direction="credit" if direction_text == "cr" else "debit",
                amount_paise=amount,
                raw_description=description,
                counterparty_raw=counterparty,
                source_reference=reference or None,
                balance_paise=balance,
            )
        )
    if not raw_entries:
        raise StatementFormatError("No Kotak transaction rows were found below the CSV header.")
    # Kotak rows are chronological and contain a running balance.
    for previous, current in zip(raw_entries, raw_entries[1:]):
        expected = previous.balance_paise + signed_balance(current.amount_paise, current.direction)
        if current.balance_paise != expected:
            findings.append(ValidationFinding("balance_mismatch", "Balance movement does not reconcile between {} and {}.".format(previous.row_locator, current.row_locator), "error"))
    entries = assign_identity_keys(raw_entries)
    dates = [entry.transaction_date for entry in entries]
    return ParsedStatement("kotak", account_key, period_start or min(dates), period_end or max(dates), entries, findings)
