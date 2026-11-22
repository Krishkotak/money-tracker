"""Shared parsing and stable identity helpers."""

import hashlib
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

from ..models import StatementEntry


def parse_money(value) -> int:
    if value is None or str(value).strip() == "":
        return 0
    cleaned = str(value).strip().replace(",", "").replace("₹", "").replace("Rs.", "")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as error:
        raise ValueError("Invalid statement amount: {!r}".format(value)) from error
    paise = amount * 100
    if paise != paise.to_integral_value():
        raise ValueError("Statement amount has more than two decimal places: {!r}".format(value))
    return int(paise)


def parse_date(value, formats: Iterable[str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError("Unrecognized statement date: {!r}".format(value))


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def assign_identity_keys(entries):
    """Assign repeat-aware keys; signatures never collapse repeated identical rows."""
    seen = defaultdict(int)
    result = []
    for entry in entries:
        if entry.identity_key:
            result.append(entry)
            continue
        if entry.source_transaction_id:
            basis = "id:{}".format(normalized_text(entry.source_transaction_id))
            identity_basis = "source_transaction_id"
        elif entry.source_reference:
            basis = "reference:{}".format(normalized_text(entry.source_reference))
            identity_basis = "source_reference"
        else:
            basis = "signature:{}|{}|{}|{}|{}".format(
                entry.account_key,
                entry.transaction_date.isoformat(),
                entry.direction,
                entry.amount_paise,
                normalized_text(entry.counterparty_raw or entry.raw_description),
            )
            identity_basis = "content_signature"
        occurrence = seen[basis]
        seen[basis] += 1
        suffix = "#{}".format(occurrence) if identity_basis == "content_signature" else ""
        digest = hashlib.sha256((entry.source + "|" + entry.account_key + "|" + basis + suffix).encode("utf-8")).hexdigest()
        result.append(
            StatementEntry(
                source=entry.source,
                account_key=entry.account_key,
                row_locator=entry.row_locator,
                transaction_date=entry.transaction_date,
                direction=entry.direction,
                amount_paise=entry.amount_paise,
                raw_description=entry.raw_description,
                counterparty_raw=entry.counterparty_raw,
                source_transaction_id=entry.source_transaction_id,
                source_reference=entry.source_reference,
                posted_time=entry.posted_time,
                value_date=entry.value_date,
                balance_paise=entry.balance_paise,
                identity_key=digest,
                identity_basis=identity_basis,
            )
        )
    return result


def extract_hsbc_details(details: str):
    lines = [line.strip() for line in (details or "").splitlines() if line.strip()]
    transaction_id = next((line for line in lines if re.fullmatch(r"UPI[A-Z0-9]+", line, re.I)), None)
    reference = next((line for line in lines if re.fullmatch(r"\d{10,18}", line)), None)
    description = lines[0] if lines else ""
    counterparty_lines = [
        line for line in lines
        if line != description and line != transaction_id and line != reference
    ]
    if description.casefold() == "salary" and "TRANSFER FROM" in [line.upper() for line in lines]:
        transfer_index = next(i for i, line in enumerate(lines) if line.upper() == "TRANSFER FROM")
        counterparty = next(
            (line for line in lines[transfer_index + 1:] if not re.fullmatch(r"[\d-]+", line)),
            "",
        )
    else:
        counterparty = " ".join(counterparty_lines).strip() or (description if description != "TRANSFER" else "")
    return description, counterparty, transaction_id, reference


def signed_balance(amount_paise: int, direction: str) -> int:
    return amount_paise if direction == "credit" else -amount_paise
