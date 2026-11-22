"""SQLite persistence for statement imports and transaction-level decisions."""

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, NamedTuple, Tuple

from .models import ParsedStatement, StatementEntry, StatementFormatError
from .parsers.common import normalized_text


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS imported_statements (
    id TEXT PRIMARY KEY,
    file_fingerprint TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL CHECK (source IN ('hsbc', 'sbi', 'kotak')),
    original_filename TEXT NOT NULL,
    account_key TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    entry_count INTEGER NOT NULL,
    findings_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS categories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id TEXT REFERENCES categories(id),
    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1))
);
CREATE UNIQUE INDEX IF NOT EXISTS categories_main_name_unique
    ON categories(name COLLATE NOCASE) WHERE parent_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS categories_child_name_unique
    ON categories(parent_id, name COLLATE NOCASE) WHERE parent_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    account_key TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    identity_basis TEXT NOT NULL,
    transaction_date TEXT NOT NULL,
    posted_time TEXT,
    value_date TEXT,
    direction TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
    amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
    balance_paise INTEGER,
    raw_description TEXT NOT NULL,
    counterparty_raw TEXT NOT NULL,
    category_id TEXT REFERENCES categories(id),
    classification TEXT,
    classification_source TEXT CHECK (classification_source IN ('rule', 'manual', 'system') OR classification_source IS NULL),
    reviewed_at TEXT,
    UNIQUE(source, account_key, identity_key)
);
CREATE INDEX IF NOT EXISTS transactions_date_idx
    ON transactions(transaction_date);

CREATE TABLE IF NOT EXISTS transaction_links (
    id TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL REFERENCES transactions(id),
    related_transaction_id TEXT NOT NULL REFERENCES transactions(id),
    relationship_type TEXT NOT NULL CHECK (relationship_type IN ('card_settlement', 'own_account_transfer', 'duplicate_representation')),
    amount_paise INTEGER,
    linked_at TEXT NOT NULL,
    CHECK (transaction_id <> related_transaction_id),
    UNIQUE(transaction_id, related_transaction_id, relationship_type)
);

CREATE TABLE IF NOT EXISTS statement_entries (
    id TEXT PRIMARY KEY,
    statement_id TEXT NOT NULL REFERENCES imported_statements(id),
    transaction_id TEXT REFERENCES transactions(id),
    row_locator TEXT NOT NULL,
    source_transaction_id TEXT,
    source_reference TEXT,
    source TEXT NOT NULL,
    account_key TEXT NOT NULL,
    transaction_date TEXT NOT NULL,
    posted_time TEXT,
    value_date TEXT,
    direction TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
    amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
    balance_paise INTEGER,
    identity_key TEXT NOT NULL,
    identity_basis TEXT NOT NULL,
    raw_description TEXT NOT NULL,
    counterparty_raw TEXT NOT NULL,
    UNIQUE(statement_id, row_locator)
);

CREATE TABLE IF NOT EXISTS transaction_allocations (
    id TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL REFERENCES transactions(id),
    category_id TEXT NOT NULL REFERENCES categories(id),
    applies_to_transaction_id TEXT REFERENCES transactions(id),
    amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
    applies_month TEXT,
    allocation_kind TEXT NOT NULL CHECK (allocation_kind IN ('expense', 'offset'))
);

CREATE TABLE IF NOT EXISTS categorization_rules (
    id TEXT PRIMARY KEY,
    match_type TEXT NOT NULL,
    match_value TEXT NOT NULL,
    source TEXT,
    direction TEXT CHECK (direction IN ('debit', 'credit') OR direction IS NULL),
    category_id TEXT NOT NULL REFERENCES categories(id),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_decisions (
    id TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL REFERENCES transactions(id),
    category_id TEXT REFERENCES categories(id),
    decision TEXT NOT NULL CHECK (decision IN ('accepted_suggestion', 'manual_category', 'income', 'transfer', 'duplicate')),
    note TEXT,
    decided_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transaction_reviews (
    transaction_id TEXT PRIMARY KEY REFERENCES transactions(id),
    suggested_category_id TEXT REFERENCES categories(id),
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'resolved')),
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS duplicate_reviews (
    id TEXT PRIMARY KEY,
    statement_entry_id TEXT NOT NULL REFERENCES statement_entries(id),
    candidate_transaction_id TEXT NOT NULL REFERENCES transactions(id),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'new_transaction', 'duplicate')),
    resolution_transaction_id TEXT REFERENCES transactions(id),
    resolved_at TEXT,
    UNIQUE(statement_entry_id, candidate_transaction_id)
);
"""
IST = timezone(timedelta(hours=5, minutes=30), "IST")


class ImportResult(NamedTuple):
    added_transactions: int
    matched_existing_transactions: int
    duplicate_reviews: int
    skipped_unchanged_statements: int
    imported_statement_ids: Tuple[str, ...]
    auto_categorized_transactions: int
    pending_reviews_added: int


def connect(database_path):
    """Open a local SQLite database with foreign-key checks enabled."""
    if str(database_path) != ":memory:":
        Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(database_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(connection):
    connection.executescript(SCHEMA)
    _migrate_statement_entries(connection)
    _migrate_transaction_allocations(connection)
    from .categories import seed_categories_and_rules

    seed_categories_and_rules(connection)
    from .categorization import apply_confirmed_rules_to_pending

    apply_confirmed_rules_to_pending(connection)


def _migrate_statement_entries(connection):
    required = {
        "source": "TEXT", "account_key": "TEXT", "transaction_date": "TEXT", "posted_time": "TEXT",
        "value_date": "TEXT", "direction": "TEXT", "amount_paise": "INTEGER", "balance_paise": "INTEGER",
        "identity_key": "TEXT", "identity_basis": "TEXT",
    }
    existing = {row[1] for row in connection.execute("PRAGMA table_info(statement_entries)")}
    for name, data_type in required.items():
        if name not in existing:
            connection.execute("ALTER TABLE statement_entries ADD COLUMN {} {}".format(name, data_type))
    connection.execute(
        """UPDATE statement_entries SET
           source = COALESCE(source, (SELECT source FROM transactions WHERE id = statement_entries.transaction_id)),
           account_key = COALESCE(account_key, (SELECT account_key FROM transactions WHERE id = statement_entries.transaction_id)),
           transaction_date = COALESCE(transaction_date, (SELECT transaction_date FROM transactions WHERE id = statement_entries.transaction_id)),
           posted_time = COALESCE(posted_time, (SELECT posted_time FROM transactions WHERE id = statement_entries.transaction_id)),
           value_date = COALESCE(value_date, (SELECT value_date FROM transactions WHERE id = statement_entries.transaction_id)),
           direction = COALESCE(direction, (SELECT direction FROM transactions WHERE id = statement_entries.transaction_id)),
           amount_paise = COALESCE(amount_paise, (SELECT amount_paise FROM transactions WHERE id = statement_entries.transaction_id)),
           balance_paise = COALESCE(balance_paise, (SELECT balance_paise FROM transactions WHERE id = statement_entries.transaction_id)),
           identity_key = COALESCE(identity_key, (SELECT identity_key FROM transactions WHERE id = statement_entries.transaction_id)),
           identity_basis = COALESCE(identity_basis, (SELECT identity_basis FROM transactions WHERE id = statement_entries.transaction_id))
           WHERE transaction_id IS NOT NULL"""
    )


def _migrate_transaction_allocations(connection):
    existing = {row[1] for row in connection.execute("PRAGMA table_info(transaction_allocations)")}
    if "applies_to_transaction_id" not in existing:
        connection.execute(
            "ALTER TABLE transaction_allocations ADD COLUMN applies_to_transaction_id TEXT REFERENCES transactions(id)"
        )


def _possible_duplicate(connection, entry: StatementEntry):
    if entry.identity_basis != "content_signature":
        return []
    rows = connection.execute(
        """SELECT id, counterparty_raw FROM transactions
           WHERE source = ? AND account_key = ? AND transaction_date = ?
             AND direction = ? AND amount_paise = ?""",
        (entry.source, entry.account_key, entry.transaction_date.isoformat(), entry.direction, entry.amount_paise),
    ).fetchall()
    signature_name = normalized_text(entry.counterparty_raw or entry.raw_description)
    return [row["id"] for row in rows if normalized_text(row["counterparty_raw"]) != signature_name]


def import_statements(connection, statements: Iterable[Tuple[Path, ParsedStatement]]) -> ImportResult:
    """Persist parsed files as one atomic import; exact overlaps reuse transactions."""
    added = matched = duplicate_reviews = skipped = 0
    imported_ids = []
    new_transaction_ids = []
    with connection:
        for file_path, parsed in statements:
            if not parsed.entries:
                raise StatementFormatError("Refusing to import an empty parsed statement.")
            errors = [finding.message for finding in parsed.findings if finding.severity == "error"]
            if errors:
                raise StatementFormatError("Refusing to import a statement with validation errors: {}".format("; ".join(errors)))
            path = Path(file_path)
            fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
            existing = connection.execute(
                "SELECT id FROM imported_statements WHERE file_fingerprint = ?", (fingerprint,)
            ).fetchone()
            if existing:
                skipped += 1
                continue
            statement_id = str(uuid.uuid4())
            findings_json = json.dumps(
                [{"code": f.code, "message": f.message, "severity": f.severity} for f in parsed.findings],
                ensure_ascii=False,
            )
            connection.execute(
                """INSERT INTO imported_statements
                   (id, file_fingerprint, source, original_filename, account_key, period_start, period_end, imported_at, entry_count, findings_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (statement_id, fingerprint, parsed.source, path.name, parsed.account_key,
                 parsed.period_start.isoformat(), parsed.period_end.isoformat(), datetime.now(IST).isoformat(),
                 len(parsed.entries), findings_json),
            )
            imported_ids.append(statement_id)
            for entry in parsed.entries:
                transaction = connection.execute(
                    "SELECT id FROM transactions WHERE source = ? AND account_key = ? AND identity_key = ?",
                    (entry.source, entry.account_key, entry.identity_key),
                ).fetchone()
                transaction_id = transaction["id"] if transaction else None
                ambiguous_candidates = _possible_duplicate(connection, entry) if not transaction else []
                if not transaction and not ambiguous_candidates:
                    transaction_id = str(uuid.uuid4())
                    connection.execute(
                        """INSERT INTO transactions
                           (id, source, account_key, identity_key, identity_basis, transaction_date, posted_time,
                            value_date, direction, amount_paise, balance_paise, raw_description, counterparty_raw)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (transaction_id, entry.source, entry.account_key, entry.identity_key, entry.identity_basis,
                         entry.transaction_date.isoformat(), entry.posted_time,
                         entry.value_date.isoformat() if entry.value_date else None,
                         entry.direction, entry.amount_paise, entry.balance_paise,
                         entry.raw_description, entry.counterparty_raw),
                    )
                    added += 1
                    new_transaction_ids.append(transaction_id)
                elif transaction:
                    matched += 1
                statement_entry_id = str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO statement_entries
                       (id, statement_id, transaction_id, row_locator, source_transaction_id, source_reference,
                        source, account_key, transaction_date, posted_time, value_date, direction, amount_paise,
                        balance_paise, identity_key, identity_basis, raw_description, counterparty_raw)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (statement_entry_id, statement_id, transaction_id, entry.row_locator,
                     entry.source_transaction_id, entry.source_reference, entry.source, entry.account_key,
                     entry.transaction_date.isoformat(), entry.posted_time,
                     entry.value_date.isoformat() if entry.value_date else None, entry.direction, entry.amount_paise,
                     entry.balance_paise, entry.identity_key, entry.identity_basis,
                     entry.raw_description, entry.counterparty_raw),
                )
                for candidate_id in ambiguous_candidates:
                    connection.execute(
                        "INSERT INTO duplicate_reviews (id, statement_entry_id, candidate_transaction_id) VALUES (?, ?, ?)",
                        (str(uuid.uuid4()), statement_entry_id, candidate_id),
                    )
                duplicate_reviews += len(ambiguous_candidates)
        from .categorization import categorize_new_transactions

        categorize_new_transactions(connection, new_transaction_ids)
        auto_categorized = 0
        pending_added = 0
        if new_transaction_ids:
            placeholders = ",".join("?" for _ in new_transaction_ids)
            auto_categorized = connection.execute(
                "SELECT COUNT(*) FROM transactions WHERE id IN ({}) AND classification_source = 'rule'".format(placeholders),
                new_transaction_ids,
            ).fetchone()[0]
            pending_added = connection.execute(
                """SELECT COUNT(*) FROM transaction_reviews WHERE status = 'pending'
                   AND transaction_id IN ({})""".format(placeholders),
                new_transaction_ids,
            ).fetchone()[0]
    return ImportResult(added, matched, duplicate_reviews, skipped, tuple(imported_ids), auto_categorized, pending_added)
