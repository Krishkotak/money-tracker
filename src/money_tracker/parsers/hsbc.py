"""Parser for HSBC's OOXML workbook (sometimes named with an .xls suffix)."""

import io
import re
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

from ..models import ParsedStatement, StatementEntry, StatementFormatError, ValidationFinding
from .common import assign_identity_keys, extract_hsbc_details, parse_date, parse_money, signed_balance


def _header_key(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _find_header(sheet):
    required = {"date", "transaction details", "deposits", "withdrawals", "balance"}
    for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
        headers = {_header_key(value): index for index, value in enumerate(row) if value is not None}
        if required.issubset(headers):
            return row_number, headers
    raise StatementFormatError("HSBC workbook is missing its expected transaction columns.")


def parse_hsbc(path: Path) -> ParsedStatement:
    path = Path(path)
    try:
        # HSBC's current export is OOXML even though the filename ends in .xls.
        workbook = load_workbook(io.BytesIO(path.read_bytes()), data_only=True, read_only=True)
    except Exception as error:
        raise StatementFormatError("Could not read the HSBC workbook: {}".format(error)) from error
    try:
        sheet = workbook.active
        header_row, headers = _find_header(sheet)
        account_key = "HSBC-unknown-account"
        for row in sheet.iter_rows(min_row=1, max_row=header_row, values_only=True):
            for value in row:
                match = re.search(r"ACCOUNT-\s*([0-9-]+)", str(value or ""), re.I)
                if match:
                    account_key = "HSBC-{}".format(match.group(1))
        raw_entries = []
        findings = []
        for row_number, row in enumerate(sheet.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1):
            if not row or not row[headers["date"]]:
                continue
            date_value = row[headers["date"]]
            try:
                transaction_date = parse_date(date_value, ("%d%b%Y", "%d %b %Y", "%d/%m/%Y"))
                deposited = parse_money(row[headers["deposits"]] if headers["deposits"] < len(row) else None)
                withdrawn = parse_money(row[headers["withdrawals"]] if headers["withdrawals"] < len(row) else None)
                balance = parse_money(row[headers["balance"]] if headers["balance"] < len(row) else None)
            except (ValueError, IndexError) as error:
                findings.append(ValidationFinding("invalid_row", "Sheet row {}: {}".format(row_number, error), "error"))
                continue
            if bool(deposited) == bool(withdrawn):
                findings.append(ValidationFinding("invalid_direction", "Sheet row {} must have exactly one of Deposit or Withdrawal.".format(row_number), "error"))
                continue
            details = str(row[headers["transaction details"]] or "")
            description, counterparty, transaction_id, reference = extract_hsbc_details(details)
            raw_entries.append(
                StatementEntry(
                    source="hsbc",
                    account_key=account_key,
                    row_locator="{}!{}".format(sheet.title, row_number),
                    transaction_date=transaction_date,
                    direction="credit" if deposited else "debit",
                    amount_paise=deposited or withdrawn,
                    raw_description=details.strip(),
                    counterparty_raw=counterparty,
                    source_transaction_id=transaction_id,
                    source_reference=reference,
                    balance_paise=balance,
                )
            )
        if not raw_entries:
            raise StatementFormatError("No HSBC transaction rows were found below the header.")
        # The workbook is newest-first. Compare each newer balance to the previous row.
        for newer, older in zip(raw_entries, raw_entries[1:]):
            expected = older.balance_paise + signed_balance(newer.amount_paise, newer.direction)
            if newer.balance_paise != expected:
                findings.append(ValidationFinding("balance_mismatch", "Balance movement does not reconcile between {} and {}.".format(older.row_locator, newer.row_locator), "error"))
        entries = assign_identity_keys(raw_entries)
        dates = [entry.transaction_date for entry in entries]
        return ParsedStatement("hsbc", account_key, min(dates), max(dates), entries, findings)
    finally:
        workbook.close()
