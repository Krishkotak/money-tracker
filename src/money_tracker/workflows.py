"""Use-case boundary between the browser adapter and finance operations."""

from .category_manager import create_category, list_categories, rename_category
from .database import import_statements as persist_statements
from .duplicate_review import pending_duplicate_reviews, resolve_duplicate_review
from .parsers import parse_statement
from .reports import available_date_range, default_date_range, report_for_range
from .reviews import pending_reviews, resolve_review, resolve_review_with_rule
from .rules import disable_special_rule, list_categorization_rules, update_categorization_rule


def import_files(connection, source_paths):
    """Parse all selected files before atomically persisting their statement rows."""
    parsed = [(path, parse_statement(path, source)) for source, path in source_paths]
    return persist_statements(connection, parsed)


def dashboard(connection, start=None, end=None):
    """Return the next screen and its data for a dashboard request."""
    reviews = pending_reviews(connection)
    duplicates = pending_duplicate_reviews(connection)
    if reviews or duplicates:
        return {
            "state": "review",
            "reviews": reviews,
            "duplicates": duplicates,
            "categories": list_categories(connection),
        }
    available = available_date_range(connection)
    if not available:
        return {"state": "empty"}
    default = default_date_range(connection)
    selected_start = start or default["start"]
    selected_end = end or default["end"]
    try:
        report = report_for_range(connection, selected_start, selected_end)
    except (ValueError, TypeError):
        selected_start, selected_end = default["start"], default["end"]
        report = report_for_range(connection, selected_start, selected_end)
    return {
        "state": "report",
        "available": available,
        "start": selected_start,
        "end": selected_end,
        "report": report,
    }


def review_transaction(connection, transaction_id, *, category_id=None, transfer=False):
    return resolve_review(connection, transaction_id, category_id=category_id, transfer=transfer)


def review_transaction_with_rule(connection, transaction_id, *, category_id, phrase):
    return resolve_review_with_rule(connection, transaction_id, category_id=category_id, phrase=phrase)


def review_possible_duplicate(connection, review_id, *, same_transaction):
    return resolve_duplicate_review(connection, review_id, same_transaction=same_transaction)


def add_category(connection, name, parent_id=None):
    return create_category(connection, name, parent_id)


def category_tree(connection):
    return list_categories(connection)


def edit_category_name(connection, category_id, new_name):
    return rename_category(connection, category_id, new_name)


def rules_page(connection):
    return list_categorization_rules(connection)


def edit_rule(connection, rule_id, match_value, category_id):
    return update_categorization_rule(
        connection, rule_id=rule_id, match_value=match_value, category_id=category_id
    )


def delete_special_rule(connection, rule_id):
    return disable_special_rule(connection, rule_id)
