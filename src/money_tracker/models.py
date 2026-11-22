"""Normalized statement data shared by the format-specific parsers."""

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional


@dataclass(frozen=True)
class ValidationFinding:
    code: str
    message: str
    severity: str = "warning"


@dataclass(frozen=True)
class StatementEntry:
    source: str
    account_key: str
    row_locator: str
    transaction_date: date
    direction: str
    amount_paise: int
    raw_description: str
    counterparty_raw: str
    source_transaction_id: Optional[str] = None
    source_reference: Optional[str] = None
    posted_time: Optional[str] = None
    value_date: Optional[date] = None
    balance_paise: Optional[int] = None
    identity_key: str = ""
    identity_basis: str = "content_signature"

    @property
    def payer(self) -> str:
        return "SELF:{}".format(self.account_key) if self.direction == "debit" else self.counterparty_raw

    @property
    def payee(self) -> str:
        return self.counterparty_raw if self.direction == "debit" else "SELF:{}".format(self.account_key)


@dataclass
class ParsedStatement:
    source: str
    account_key: str
    period_start: date
    period_end: date
    entries: List[StatementEntry] = field(default_factory=list)
    findings: List[ValidationFinding] = field(default_factory=list)


class StatementFormatError(ValueError):
    """Raised when a source file does not match its expected statement layout."""
