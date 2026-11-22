"""Apply confirmed rules to new transactions and preserve uncertain items for review."""

import json
import uuid
from datetime import datetime, timedelta, timezone

from .parsers.common import normalized_text
from .rules import SPECIAL_RULE_IDS

try:
    from . import private_config
except ImportError:
    private_config = None


IST = timezone(timedelta(hours=5, minutes=30), "IST")


def _rule_matches(rule, transaction, ignore_direction=False):
    if rule["source"] and rule["source"] != transaction["source"]:
        return False
    if not ignore_direction and rule["direction"] and rule["direction"] != transaction["direction"]:
        return False
    if rule["match_type"] == "counterparty_exact":
        return normalized_text(transaction["counterparty_raw"]) == normalized_text(rule["match_value"])
    if rule["match_type"] == "counterparty_contains":
        return normalized_text(rule["match_value"]) in normalized_text(transaction["counterparty_raw"])
    if rule["match_type"] == "counterparty_description":
        match = json.loads(rule["match_value"])
        return (
            normalized_text(transaction["counterparty_raw"]) == normalized_text(match["counterparty"])
            and normalized_text(match["description"]) in normalized_text(transaction["raw_description"])
        )
    if rule["match_type"] == "default_debit":
        return transaction["direction"] == "debit"
    return False


def _matching_rule(connection, transaction, *, require_direction=True):
    rules = connection.execute(
        """SELECT * FROM categorization_rules WHERE enabled = 1 AND (source IS NULL OR source = ?)
           ORDER BY CASE WHEN match_type = 'counterparty_exact' THEN 0
                         WHEN match_type = 'counterparty_description' THEN 1
                         WHEN match_type = 'counterparty_contains' THEN 2 ELSE 3 END,
                    CASE WHEN match_type = 'counterparty_contains' THEN length(match_value) ELSE 0 END DESC,
                    id""",
        (transaction["source"],),
    ).fetchall()
    for rule in rules:
        if not require_direction and rule["direction"] == "credit":
            continue
        if _rule_matches(rule, transaction, ignore_direction=not require_direction):
            return rule
    return None


def _income_category(connection, category_id):
    row = connection.execute("SELECT parent_id FROM categories WHERE id = ?", (category_id,)).fetchone()
    if not row:
        return False
    return category_id == "income" or row["parent_id"] == "income"


def _add_allocation(connection, transaction_id, category_id, amount_paise, applies_month, kind, applies_to=None):
    connection.execute(
        """INSERT INTO transaction_allocations
           (id, transaction_id, category_id, applies_to_transaction_id, amount_paise, applies_month, allocation_kind)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), transaction_id, category_id, applies_to, amount_paise, applies_month, kind),
    )


def _upcoming_rent(connection, transaction):
    match = getattr(private_config, "RENT_PAYMENT_MATCH", None) if private_config else None
    if not match:
        return None
    candidates = connection.execute(
        """SELECT id, transaction_date FROM transactions
           WHERE source = ? AND direction = ? AND amount_paise = ?
             AND transaction_date >= ? ORDER BY transaction_date""",
        (match["source"], match["direction"], match["amount_paise"], transaction["transaction_date"]),
    ).fetchall()
    for candidate in candidates:
        rent = connection.execute("SELECT counterparty_raw FROM transactions WHERE id = ?", (candidate["id"],)).fetchone()
        if normalized_text(rent["counterparty_raw"]) == match["counterparty_normalized"]:
            return candidate
    return None


def _apply_known_roommate_rule(connection, transaction, rule):
    behaviors = getattr(private_config, "ROOMMATE_RULE_BEHAVIORS", {}) if private_config else {}
    behavior = behaviors.get(rule["id"])
    if not behavior:
        raise ValueError("The local allocation-rule configuration is missing.")
    amount = transaction["amount_paise"]
    received_month = transaction["transaction_date"][:7]
    if behavior["mode"] == "single_offset":
        category_id = behavior["category_id"]
        connection.execute(
            "UPDATE transactions SET classification = 'reimbursement', classification_source = 'rule', category_id = ? WHERE id = ?",
            (category_id, transaction["id"]),
        )
        _add_allocation(connection, transaction["id"], category_id, amount, received_month, "offset")
        return
    if behavior["mode"] == "rent_split":
        rent_category = behavior["rent_category_id"]
        rent_share = min(behavior["rent_share_paise"], amount)
        remainder_category = behavior["remainder_category_id"]
        connection.execute(
            "UPDATE transactions SET classification = 'reimbursement', classification_source = 'rule', category_id = ? WHERE id = ?",
            (rent_category, transaction["id"]),
        )
        _add_allocation(connection, transaction["id"], rent_category, rent_share, None, "offset")
        if amount > rent_share:
            _add_allocation(connection, transaction["id"], remainder_category, amount - rent_share, received_month, "offset")
        return
    raise ValueError("Unsupported local allocation-rule behavior.")


def _apply_rule(connection, transaction, rule):
    if rule["id"] in SPECIAL_RULE_IDS:
        _apply_known_roommate_rule(connection, transaction, rule)
        return
    category_id = rule["category_id"]
    if _income_category(connection, category_id):
        classification = "income"
        allocation_kind = None
    elif transaction["direction"] == "debit":
        classification = "expense"
        allocation_kind = "expense"
    else:
        classification = "reimbursement"
        allocation_kind = "offset"
    connection.execute(
        """UPDATE transactions SET category_id = ?, classification = ?, classification_source = 'rule'
           WHERE id = ?""",
        (category_id, classification, transaction["id"]),
    )
    if allocation_kind:
        _add_allocation(
            connection,
            transaction["id"],
            category_id,
            transaction["amount_paise"],
            transaction["transaction_date"][:7],
            allocation_kind,
        )


def _create_review(connection, transaction, *, classification, suggestion_id, reason):
    connection.execute(
        """UPDATE transactions SET classification = ?, classification_source = 'system' WHERE id = ?""",
        (classification, transaction["id"]),
    )
    connection.execute(
        """INSERT OR IGNORE INTO transaction_reviews
           (transaction_id, suggested_category_id, reason, created_at)
           VALUES (?, ?, ?, ?)""",
        (transaction["id"], suggestion_id, reason, datetime.now(IST).isoformat()),
    )


def _is_card_settlement(transaction):
    description = normalized_text(transaction["raw_description"])
    counterparty = normalized_text(transaction["counterparty_raw"])
    return (transaction["source"] == "sbi" and transaction["direction"] == "credit" and "payment received" in description) or (
        transaction["source"] == "hsbc" and transaction["direction"] == "debit" and "sbi" in counterparty and "card" in (counterparty + " " + description)
    )


def _link_card_settlements(connection):
    bank_rows = connection.execute(
        "SELECT id, transaction_date, amount_paise FROM transactions WHERE source='hsbc' AND classification='settlement' AND direction='debit'"
    ).fetchall()
    card_rows = connection.execute(
        "SELECT id, transaction_date, amount_paise FROM transactions WHERE source='sbi' AND classification='settlement' AND direction='credit'"
    ).fetchall()
    used_cards = {row[0] for row in connection.execute(
        "SELECT related_transaction_id FROM transaction_links WHERE relationship_type='card_settlement' UNION SELECT transaction_id FROM transaction_links WHERE relationship_type='card_settlement'"
    ).fetchall()}
    used_banks = {row[0] for row in connection.execute(
        "SELECT related_transaction_id FROM transaction_links WHERE relationship_type='card_settlement' UNION SELECT transaction_id FROM transaction_links WHERE relationship_type='card_settlement'"
    ).fetchall()}
    for bank in bank_rows:
        matches = [card for card in card_rows if card["id"] not in used_cards and card["amount_paise"] == bank["amount_paise"]]
        if not matches:
            continue
        matches.sort(key=lambda card: _day_distance(card["transaction_date"], bank["transaction_date"]))
        if len(matches) > 1 and _day_distance(matches[0]["transaction_date"], bank["transaction_date"]) == _day_distance(matches[1]["transaction_date"], bank["transaction_date"]):
            continue
        card = matches[0]
        if bank["id"] in used_banks or card["id"] in used_cards:
            continue
        connection.execute(
            """INSERT OR IGNORE INTO transaction_links
               (id, transaction_id, related_transaction_id, relationship_type, amount_paise, linked_at)
               VALUES (?, ?, ?, 'card_settlement', ?, ?)""",
            (str(uuid.uuid4()), bank["id"], card["id"], bank["amount_paise"], datetime.now(IST).isoformat()),
        )
        used_banks.add(bank["id"])
        used_cards.add(card["id"])


def _day_distance(left, right):
    from datetime import date

    return abs((date.fromisoformat(left) - date.fromisoformat(right)).days)


def _link_pending_rent_contributions(connection):
    match = getattr(private_config, "RENT_PAYMENT_MATCH", None) if private_config else None
    if not match:
        return
    rent_category = match["rent_category_id"]
    pending = connection.execute(
        """SELECT a.id, a.transaction_id, a.amount_paise, t.transaction_date
           FROM transaction_allocations a JOIN transactions t ON t.id = a.transaction_id
           WHERE a.allocation_kind = 'offset' AND a.category_id = ?
             AND a.applies_to_transaction_id IS NULL AND t.classification = 'reimbursement'
           ORDER BY t.transaction_date""",
        (rent_category,),
    ).fetchall()
    for allocation in pending:
        contributor = connection.execute("SELECT * FROM transactions WHERE id = ?", (allocation["transaction_id"],)).fetchone()
        rent = _upcoming_rent(connection, contributor)
        if not rent:
            continue
        linked_total = connection.execute(
            """SELECT COALESCE(SUM(amount_paise), 0) FROM transaction_allocations
               WHERE applies_to_transaction_id = ? AND allocation_kind = 'offset' AND category_id = ?""",
            (rent["id"], rent_category),
        ).fetchone()[0]
        if linked_total + allocation["amount_paise"] > match["maximum_contributions_paise"]:
            continue
        connection.execute(
            "UPDATE transaction_allocations SET applies_to_transaction_id = ?, applies_month = ? WHERE id = ?",
            (rent["id"], rent["transaction_date"][:7], allocation["id"]),
        )


def categorize_new_transactions(connection, transaction_ids):
    """Classify only just-created transactions; never revisit existing decisions."""
    for transaction_id in transaction_ids:
        transaction = connection.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
        if not transaction or transaction["classification"] is not None:
            continue
        if _is_card_settlement(transaction):
            connection.execute(
                "UPDATE transactions SET classification='settlement', classification_source='system' WHERE id = ?",
                (transaction_id,),
            )
            continue
        rule = _matching_rule(connection, transaction)
        if rule:
            _apply_rule(connection, transaction, rule)
            continue
        if transaction["direction"] == "debit":
            _create_review(
                connection,
                transaction,
                classification="expense",
                suggestion_id="travel-auto-cab",
                reason="Unmatched outgoing payment; Auto / Cab is a suggested category.",
            )
        else:
            suggestion = _matching_rule(connection, transaction, require_direction=False)
            suggestion_id = suggestion["category_id"] if suggestion else None
            reason = "Possible refund; confirm its category." if suggestion_id else "Incoming payment needs your purpose and category."
            _create_review(
                connection,
                transaction,
                classification="refund" if suggestion_id else "incoming_review",
                suggestion_id=suggestion_id,
                reason=reason,
            )
    _link_card_settlements(connection)
    _link_pending_rent_contributions(connection)


def apply_confirmed_rules_to_pending(connection):
    """Apply newly added/changed confirmed rules to unresolved items only."""
    now = datetime.now(IST).isoformat()
    with connection:
        pending_ids = [row[0] for row in connection.execute(
            "SELECT transaction_id FROM transaction_reviews WHERE status = 'pending'"
        ).fetchall()]
        for transaction_id in pending_ids:
            transaction = connection.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
            if not transaction or _is_card_settlement(transaction):
                continue
            rule = _matching_rule(connection, transaction)
            if not rule:
                continue
            connection.execute("DELETE FROM transaction_allocations WHERE transaction_id = ?", (transaction_id,))
            _apply_rule(connection, transaction, rule)
            connection.execute(
                "UPDATE transaction_reviews SET status='resolved', resolved_at=? WHERE transaction_id=? AND status='pending'",
                (now, transaction_id),
            )
        _link_card_settlements(connection)
        _link_pending_rent_contributions(connection)
