"""Human resolution for source rows that only resemble an existing transaction."""

import uuid
from datetime import datetime, timedelta, timezone


IST = timezone(timedelta(hours=5, minutes=30), "IST")


def pending_duplicate_reviews(connection):
    rows = connection.execute(
        """SELECT d.id AS review_id, d.statement_entry_id, d.candidate_transaction_id,
                  e.source, e.account_key, e.transaction_date, e.direction, e.amount_paise,
                  e.counterparty_raw, e.raw_description,
                  t.transaction_date AS candidate_date, t.direction AS candidate_direction,
                  t.amount_paise AS candidate_amount_paise, t.counterparty_raw AS candidate_counterparty,
                  t.raw_description AS candidate_description
           FROM duplicate_reviews d
           JOIN statement_entries e ON e.id = d.statement_entry_id
           JOIN transactions t ON t.id = d.candidate_transaction_id
           WHERE d.status = 'pending'
           ORDER BY e.transaction_date, e.row_locator"""
    ).fetchall()
    return [dict(row) for row in rows]


def resolve_duplicate_review(connection, review_id, *, same_transaction):
    with connection:
        review = connection.execute(
            "SELECT * FROM duplicate_reviews WHERE id = ? AND status = 'pending'", (review_id,)
        ).fetchone()
        if not review:
            raise ValueError("This possible duplicate has already been resolved.")
        now = datetime.now(IST).isoformat()
        if same_transaction:
            chosen_id = review["candidate_transaction_id"]
            connection.execute(
                "UPDATE duplicate_reviews SET status='duplicate', resolution_transaction_id=?, resolved_at=? WHERE id=?",
                (chosen_id, now, review_id),
            )
            connection.execute(
                """UPDATE duplicate_reviews SET status='new_transaction', resolution_transaction_id=?, resolved_at=?
                   WHERE statement_entry_id=? AND status='pending'""",
                (chosen_id, now, review["statement_entry_id"]),
            )
            connection.execute(
                "UPDATE statement_entries SET transaction_id=? WHERE id=?",
                (chosen_id, review["statement_entry_id"]),
            )
            return {"resolved": True, "is_duplicate": True, "transaction_id": chosen_id}

        connection.execute(
            "UPDATE duplicate_reviews SET status='new_transaction', resolved_at=? WHERE id=?",
            (now, review_id),
        )
        remaining = connection.execute(
            "SELECT COUNT(*) FROM duplicate_reviews WHERE statement_entry_id=? AND status='pending'",
            (review["statement_entry_id"],),
        ).fetchone()[0]
        if remaining:
            return {"resolved": False, "remaining_candidates": remaining, "transaction_id": None}
        entry = connection.execute("SELECT * FROM statement_entries WHERE id=?", (review["statement_entry_id"],)).fetchone()
        transaction_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO transactions
               (id, source, account_key, identity_key, identity_basis, transaction_date, posted_time, value_date,
                direction, amount_paise, balance_paise, raw_description, counterparty_raw)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (transaction_id, entry["source"], entry["account_key"], entry["identity_key"], entry["identity_basis"],
             entry["transaction_date"], entry["posted_time"], entry["value_date"], entry["direction"],
             entry["amount_paise"], entry["balance_paise"], entry["raw_description"], entry["counterparty_raw"]),
        )
        connection.execute(
            "UPDATE statement_entries SET transaction_id=? WHERE id=?",
            (transaction_id, review["statement_entry_id"]),
        )
        connection.execute(
            """UPDATE duplicate_reviews SET resolution_transaction_id=?, resolved_at=?
               WHERE statement_entry_id=?""",
            (transaction_id, now, review["statement_entry_id"]),
        )
        from .categorization import categorize_new_transactions

        categorize_new_transactions(connection, [transaction_id])
        return {"resolved": True, "is_duplicate": False, "transaction_id": transaction_id}
