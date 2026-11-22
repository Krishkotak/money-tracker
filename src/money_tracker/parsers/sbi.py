"""Parser for selectable-text SBI Card statement PDFs."""

import re
from pathlib import Path

from pypdf import PdfReader

from ..models import ParsedStatement, StatementEntry, StatementFormatError, ValidationFinding
from .common import assign_identity_keys, parse_date, parse_money


TRANSACTION_ROW = re.compile(
    r"^\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{2})\s+(.+?)\s+([\d,]+\.\d{2})\s+([CD])\s*$"
)


def parse_sbi(path: Path) -> ParsedStatement:
    path = Path(path)
    try:
        reader = PdfReader(str(path))
        if not reader.pages:
            raise StatementFormatError("SBI PDF contains no pages.")
        text = reader.pages[0].extract_text(extraction_mode="layout") or ""
    except StatementFormatError:
        raise
    except Exception as error:
        raise StatementFormatError("Could not read the SBI statement PDF: {}".format(error)) from error
    period_match = re.search(
        r"Statement Period:\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{2})\s+to\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{2})",
        text,
        re.I,
    )
    if not period_match:
        raise StatementFormatError("Could not find the SBI statement period on its first page.")
    start = parse_date(period_match.group(1), ("%d %b %y",))
    end = parse_date(period_match.group(2), ("%d %b %y",))
    card_match = re.search(r"Credit Card Number\s+([X\d ]+)", text, re.I)
    account_key = "SBI-card-{}".format(re.sub(r"\D", "", card_match.group(1))[-2:]) if card_match else "SBI-card-unknown"
    entries = []
    findings = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = TRANSACTION_ROW.match(line)
        if not match:
            continue
        transaction_date = parse_date(match.group(1), ("%d %b %y",))
        description = re.sub(r"\s+", " ", match.group(2)).strip()
        amount = parse_money(match.group(3))
        direction = "credit" if match.group(4) == "C" else "debit"
        payment_ref = None
        payment_match = re.search(r"PAYMENT RECEIVED\s+([A-Z0-9]+)", description, re.I)
        if payment_match:
            payment_ref = payment_match.group(1)
        counterparty = description
        entries.append(
            StatementEntry(
                source="sbi",
                account_key=account_key,
                row_locator="PDF:1:line-{}".format(line_number),
                transaction_date=transaction_date,
                direction=direction,
                amount_paise=amount,
                raw_description=description,
                counterparty_raw=counterparty,
                source_reference=payment_ref,
            )
        )
    if not entries:
        raise StatementFormatError("No SBI transaction rows were found on the first page.")
    if min(entry.transaction_date for entry in entries) < start or max(entry.transaction_date for entry in entries) > end:
        findings.append(ValidationFinding("period_mismatch", "A transaction date falls outside the printed statement period.", "error"))
    # Purchases and payment credits reconcile to SBI's account summary.
    summary_match = re.search(
        r"ACCOUNT SUMMARY[\s\S]*?(\d[\d,]*\.\d{2})\s+(\d[\d,]*\.\d{2})\s+(\d[\d,]*\.\d{2})\s+(\d[\d,]*\.\d{2})\s+(\d[\d,]*\.\d{2})\s+(CR|DR)",
        text,
        re.I,
    )
    if summary_match:
        previous_balance, summary_credits, summary_debits, fees, total_outstanding = [parse_money(value) for value in summary_match.groups()[:5]]
        outstanding_side = summary_match.group(6)
        credits = sum(entry.amount_paise for entry in entries if entry.direction == "credit")
        debits = sum(entry.amount_paise for entry in entries if entry.direction == "debit")
        if credits != summary_credits or debits != summary_debits:
            findings.append(ValidationFinding("summary_mismatch", "SBI transaction totals do not match the printed account summary.", "error"))
        expected_net = previous_balance + debits + fees - credits
        if abs(expected_net) != total_outstanding:
            findings.append(ValidationFinding("outstanding_mismatch", "SBI account-summary arithmetic does not match total outstanding.", "error"))
        elif expected_net > 0 and outstanding_side != "DR":
            findings.append(ValidationFinding("outstanding_side_mismatch", "SBI outstanding balance side does not match the summary arithmetic.", "error"))
        elif expected_net < 0 and outstanding_side != "CR":
            findings.append(ValidationFinding("outstanding_side_mismatch", "SBI outstanding balance side does not match the summary arithmetic.", "error"))
    else:
        findings.append(ValidationFinding("summary_unreadable", "Could not extract SBI account-summary totals for reconciliation."))
    return ParsedStatement("sbi", account_key, start, end, assign_identity_keys(entries), findings)
