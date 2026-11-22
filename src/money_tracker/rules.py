"""Create reusable counterparty rules from an explicit review decision."""

import json
import uuid
from datetime import datetime, timedelta, timezone

from .parsers.common import normalized_text


IST = timezone(timedelta(hours=5, minutes=30), "IST")
try:
    from .private_config import SPECIAL_RULE_IDS
except ImportError:
    SPECIAL_RULE_IDS = frozenset()


def _category_path(connection, category_id):
    row = connection.execute("SELECT name, parent_id FROM categories WHERE id = ?", (category_id,)).fetchone()
    if not row:
        return "Unknown category"
    if row["parent_id"] is None:
        return row["name"]
    parent = connection.execute("SELECT name FROM categories WHERE id = ?", (row["parent_id"],)).fetchone()
    return "{} / {}".format(parent["name"], row["name"]) if parent else row["name"]


def list_categorization_rules(connection):
    """List active rules with their editable or protected status and readable match."""
    rows = connection.execute(
        """SELECT r.*, c.name AS category_name FROM categorization_rules r
           JOIN categories c ON c.id = r.category_id WHERE r.enabled = 1
           ORDER BY r.match_type, r.id"""
    ).fetchall()
    result = []
    for row in rows:
        if row["match_type"] == "counterparty_description":
            match = json.loads(row["match_value"])
            summary = "Counterparty: {} · Description contains: {}".format(match["counterparty"], match["description"])
        elif row["match_type"] == "counterparty_contains":
            summary = "Counterparty contains: {}".format(row["match_value"])
        elif row["match_type"] == "counterparty_exact":
            summary = "Counterparty exactly: {}".format(row["match_value"])
        else:
            summary = "{}: {}".format(row["match_type"], row["match_value"])
        result.append({
            "id": row["id"],
            "match_type": row["match_type"],
            "match_value": row["match_value"],
            "summary": summary,
            "source": row["source"],
            "direction": row["direction"],
            "category_id": row["category_id"],
            "category": _category_path(connection, row["category_id"]),
            "special": row["id"] in SPECIAL_RULE_IDS,
        })
    return result


def _active_rule(connection, rule_id):
    rule = connection.execute(
        "SELECT * FROM categorization_rules WHERE id = ? AND enabled = 1", (rule_id,)
    ).fetchone()
    if not rule:
        raise ValueError("That rule is no longer active.")
    return rule


def _validate_rule_category(connection, category_id, direction):
    category = connection.execute(
        "SELECT id, parent_id FROM categories WHERE id = ? AND archived = 0", (category_id,)
    ).fetchone()
    if not category:
        raise ValueError("Choose an active category for this rule.")
    is_income = category_id == "income" or category["parent_id"] == "income"
    if direction == "debit" and is_income:
        raise ValueError("An outgoing payment cannot be assigned to an income category.")


def update_categorization_rule(connection, *, rule_id, match_value, category_id):
    """Edit an ordinary rule while preserving its source and direction scope."""
    clean_value = " ".join(str(match_value or "").split())
    normalized_value = normalized_text(clean_value)
    if not normalized_value:
        raise ValueError("Enter a counterparty name or phrase.")
    if len(clean_value) > 120:
        raise ValueError("The counterparty name or phrase must be 120 characters or fewer.")
    with connection:
        rule = _active_rule(connection, rule_id)
        if rule_id in SPECIAL_RULE_IDS:
            raise ValueError("This roommate rule has custom allocation behavior and cannot be edited here.")
        if rule["match_type"] not in ("counterparty_exact", "counterparty_contains"):
            raise ValueError("This rule type cannot be edited here.")
        _validate_rule_category(connection, category_id, rule["direction"])
        overlapping = connection.execute(
            """SELECT id, match_value, category_id, source FROM categorization_rules
               WHERE enabled = 1 AND id <> ? AND match_type = ? AND direction = ?""",
            (rule_id, rule["match_type"], rule["direction"]),
        ).fetchall()
        for other in overlapping:
            same_source_scope = other["source"] == rule["source"] or other["source"] is None or rule["source"] is None
            if same_source_scope and normalized_text(other["match_value"]) == normalized_value:
                raise ValueError("An overlapping rule already uses that name or phrase.")
        connection.execute(
            "UPDATE categorization_rules SET match_value = ?, category_id = ? WHERE id = ?",
            (clean_value, category_id, rule_id),
        )


def disable_special_rule(connection, rule_id):
    """Retire a protected roommate rule without changing transaction history."""
    with connection:
        _active_rule(connection, rule_id)
        if rule_id not in SPECIAL_RULE_IDS:
            raise ValueError("Only protected roommate rules can be deleted here.")
        connection.execute("UPDATE categorization_rules SET enabled = 0 WHERE id = ?", (rule_id,))


def add_counterparty_phrase_rule(connection, *, phrase, direction, category_id):
    """Create or reuse a source-independent contains rule for one direction."""
    clean_phrase = " ".join(str(phrase or "").split())
    normalized_phrase = normalized_text(clean_phrase)
    if not normalized_phrase:
        raise ValueError("Enter a counterparty phrase for the rule.")
    if len(clean_phrase) > 120:
        raise ValueError("The counterparty phrase must be 120 characters or fewer.")
    if direction not in ("debit", "credit"):
        raise ValueError("This transaction has no supported direction for a rule.")
    category = connection.execute(
        "SELECT id FROM categories WHERE id = ? AND archived = 0", (category_id,)
    ).fetchone()
    if not category:
        raise ValueError("Choose an active category before adding a rule.")

    existing_rules = connection.execute(
        """SELECT id, match_value, category_id FROM categorization_rules
           WHERE enabled = 1 AND match_type = 'counterparty_contains'
             AND source IS NULL AND direction = ?""",
        (direction,),
    ).fetchall()
    for rule in existing_rules:
        if normalized_text(rule["match_value"]) != normalized_phrase:
            continue
        if rule["category_id"] != category_id:
            raise ValueError(
                "That counterparty phrase already has a rule for another category. Choose a different phrase."
            )
        return rule["id"]

    rule_id = "rule-{}".format(uuid.uuid4())
    connection.execute(
        """INSERT INTO categorization_rules
           (id, match_type, match_value, source, direction, category_id, created_at)
           VALUES (?, 'counterparty_contains', ?, NULL, ?, ?, ?)""",
        (rule_id, clean_phrase, direction, category_id, datetime.now(IST).isoformat()),
    )
    return rule_id
