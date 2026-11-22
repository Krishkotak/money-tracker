"""Transaction-level categorization review persistence."""

import uuid
from datetime import datetime, timedelta, timezone

from .categorization import _add_allocation, _income_category
from .rules import add_counterparty_phrase_rule


IST = timezone(timedelta(hours=5, minutes=30), "IST")


def _category_path(connection, category_id):
    category = connection.execute("SELECT id, name, parent_id FROM categories WHERE id = ?", (category_id,)).fetchone()
    if not category:
        return None
    if category["parent_id"] is None:
        return category["name"]
    parent = connection.execute("SELECT name FROM categories WHERE id = ?", (category["parent_id"],)).fetchone()
    return "{} / {}".format(parent["name"], category["name"]) if parent else category["name"]


def pending_reviews(connection):
    """Return unresolved transactions with the facts needed for a human decision."""
    rows = connection.execute(
        """SELECT t.id, t.source, t.account_key, t.transaction_date, t.posted_time,
                  t.direction, t.amount_paise, t.counterparty_raw, t.raw_description,
                  t.classification, r.suggested_category_id, r.reason
           FROM transaction_reviews r JOIN transactions t ON t.id = r.transaction_id
           WHERE r.status = 'pending' ORDER BY t.transaction_date, t.posted_time, t.id"""
    ).fetchall()
    return [
        {
            "transaction_id": row["id"],
            "source": row["source"],
            "account": row["account_key"],
            "date": row["transaction_date"],
            "time": row["posted_time"],
            "direction": row["direction"],
            "amount_paise": row["amount_paise"],
            "counterparty": row["counterparty_raw"],
            "description": row["raw_description"],
            "classification": row["classification"],
            "suggested_category_id": row["suggested_category_id"],
            "suggested_category": _category_path(connection, row["suggested_category_id"]) if row["suggested_category_id"] else None,
            "reason": row["reason"],
        }
        for row in rows
    ]


def _resolve_review_in_transaction(connection, transaction_id, *, category_id=None, transfer=False, note=None):
    review = connection.execute(
        "SELECT * FROM transaction_reviews WHERE transaction_id = ? AND status = 'pending'",
        (transaction_id,),
    ).fetchone()
    transaction = connection.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    if not review or not transaction:
        raise ValueError("This transaction is no longer waiting for review.")
    now = datetime.now(IST).isoformat()
    if transfer:
        connection.execute(
            """UPDATE transactions SET category_id = NULL, classification = 'transfer',
               classification_source = 'manual', reviewed_at = ? WHERE id = ?""",
            (now, transaction_id),
        )
        connection.execute("DELETE FROM transaction_allocations WHERE transaction_id = ?", (transaction_id,))
        decision = "transfer"
        decided_category_id = None
    else:
        category = connection.execute("SELECT id FROM categories WHERE id = ? AND archived = 0", (category_id,)).fetchone()
        if not category:
            raise ValueError("Choose an active category before completing the review.")
        is_income = _income_category(connection, category_id)
        if transaction["direction"] == "debit" and is_income:
            raise ValueError("An expense cannot be assigned to an income category.")
        if transaction["direction"] == "credit" and transaction["classification"] == "refund" and is_income:
            raise ValueError("A refund must offset an expense category, not income.")
        if is_income:
            classification, kind, decision = "income", None, "income"
        elif transaction["direction"] == "debit":
            classification, kind, decision = "expense", "expense", "manual_category"
        elif transaction["classification"] == "refund":
            classification, kind, decision = "refund", "offset", "manual_category"
        else:
            classification, kind, decision = "reimbursement", "offset", "manual_category"
        connection.execute(
            """UPDATE transactions SET category_id = ?, classification = ?,
               classification_source = 'manual', reviewed_at = ? WHERE id = ?""",
            (category_id, classification, now, transaction_id),
        )
        connection.execute("DELETE FROM transaction_allocations WHERE transaction_id = ?", (transaction_id,))
        if kind:
            _add_allocation(
                connection,
                transaction_id,
                category_id,
                transaction["amount_paise"],
                transaction["transaction_date"][:7],
                kind,
            )
        decided_category_id = category_id
        if review["suggested_category_id"] == category_id:
            decision = "accepted_suggestion"
    connection.execute(
        """INSERT INTO review_decisions (id, transaction_id, category_id, decision, note, decided_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), transaction_id, decided_category_id, decision, note, now),
    )
    connection.execute(
        "UPDATE transaction_reviews SET status='resolved', resolved_at=? WHERE transaction_id=?",
        (now, transaction_id),
    )


def resolve_review(connection, transaction_id, *, category_id=None, transfer=False, note=None):
    """Store a one-transaction decision without creating a reusable rule."""
    with connection:
        _resolve_review_in_transaction(
            connection, transaction_id, category_id=category_id, transfer=transfer, note=note
        )


def resolve_review_with_rule(connection, transaction_id, *, category_id, phrase):
    """Atomically save the selected category and a source-independent phrase rule."""
    with connection:
        transaction = connection.execute(
            "SELECT direction FROM transactions WHERE id = ?", (transaction_id,)
        ).fetchone()
        if not transaction:
            raise ValueError("This transaction is no longer waiting for review.")
        _resolve_review_in_transaction(connection, transaction_id, category_id=category_id)
        return add_counterparty_phrase_rule(
            connection,
            phrase=phrase,
            direction=transaction["direction"],
            category_id=category_id,
        )
