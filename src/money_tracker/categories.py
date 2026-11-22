"""Stable starter category IDs and user-confirmed initial match rules."""

from datetime import datetime, timedelta, timezone


IST = timezone(timedelta(hours=5, minutes=30), "IST")

try:
    from .private_config import CATEGORIES, RULES
except ImportError:
    CATEGORIES = ()
    RULES = ()




def seed_categories_and_rules(connection):
    """Insert missing seed records without overwriting user renames or edits."""
    with connection:
        for category_id, name, parent_id in CATEGORIES:
            connection.execute(
                "INSERT OR IGNORE INTO categories (id, name, parent_id) VALUES (?, ?, ?)",
                (category_id, name, parent_id),
            )
        for rule_id, source, direction, match_type, match_value, category_id in RULES:
            connection.execute(
                """INSERT OR IGNORE INTO categorization_rules
                   (id, match_type, match_value, source, direction, category_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (rule_id, match_type, match_value, source, direction, category_id, datetime.now(IST).isoformat()),
            )
        # The old fallback auto-categorized every unmatched debit. Disable it:
        # unmatched debits now receive only a review suggestion, never auto-assignment.
        connection.execute(
            "UPDATE categorization_rules SET enabled = 0 WHERE id = 'rule-default-unmatched-debit'"
        )
        # Existing unresolved debits should get the same review suggestion as new ones.
        # This is only a suggestion; no category or expense allocation is assigned.
        connection.execute(
            """UPDATE transaction_reviews
               SET suggested_category_id = 'travel-auto-cab',
                   reason = 'Unmatched outgoing payment; Auto / Cab is a suggested category.'
               WHERE status = 'pending' AND transaction_id IN (
                   SELECT id FROM transactions
                   WHERE direction = 'debit' AND classification = 'expense' AND category_id IS NULL
               )"""
        )
        prior_fallbacks = connection.execute(
            "SELECT id FROM transactions WHERE category_id = 'travel-auto-cab' AND classification_source = 'rule'"
        ).fetchall()
        for row in prior_fallbacks:
            connection.execute("DELETE FROM transaction_allocations WHERE transaction_id = ?", (row["id"],))
            connection.execute(
                """UPDATE transactions SET category_id = NULL, classification = 'expense',
                   classification_source = 'system', reviewed_at = NULL WHERE id = ?""",
                (row["id"],),
            )
            review = connection.execute(
                "SELECT transaction_id FROM transaction_reviews WHERE transaction_id = ?", (row["id"],)
            ).fetchone()
            if review:
                connection.execute(
                    """UPDATE transaction_reviews SET suggested_category_id = NULL,
                       reason = 'Expense purpose needs your category.', status = 'pending',
                       resolved_at = NULL WHERE transaction_id = ?""",
                    (row["id"],),
                )
            else:
                connection.execute(
                    """INSERT INTO transaction_reviews (transaction_id, suggested_category_id, reason, created_at)
                       VALUES (?, NULL, 'Expense purpose needs your category.', ?)""",
                    (row["id"], datetime.now(IST).isoformat()),
                )
