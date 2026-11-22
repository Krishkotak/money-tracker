"""Persistent category creation and rename operations."""

import sqlite3
import uuid


def list_categories(connection):
    rows = connection.execute("SELECT id, name, parent_id FROM categories WHERE archived = 0 ORDER BY name COLLATE NOCASE").fetchall()
    by_id = {row["id"]: {"id": row["id"], "name": row["name"], "parent_id": row["parent_id"], "children": []} for row in rows}
    roots = []
    for row in rows:
        item = by_id[row["id"]]
        if row["parent_id"] is None:
            roots.append(item)
        elif row["parent_id"] in by_id:
            by_id[row["parent_id"]]["children"].append(item)
    return roots


def create_category(connection, name, parent_id=None):
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("Enter a category name.")
    with connection:
        if parent_id is not None:
            parent = connection.execute("SELECT parent_id FROM categories WHERE id = ? AND archived = 0", (parent_id,)).fetchone()
            if not parent:
                raise ValueError("Choose an existing main category.")
            if parent["parent_id"] is not None:
                raise ValueError("Subcategories can only be added under a main category.")
        category_id = "cat_{}".format(uuid.uuid4())
        try:
            connection.execute(
                "INSERT INTO categories (id, name, parent_id) VALUES (?, ?, ?)",
                (category_id, clean_name, parent_id),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("A category with this name already exists at this level.") from error
    return category_id


def rename_category(connection, category_id, new_name):
    clean_name = str(new_name or "").strip()
    if not clean_name:
        raise ValueError("Enter a category name.")
    with connection:
        target = connection.execute("SELECT id, name, parent_id FROM categories WHERE id = ?", (category_id,)).fetchone()
        if not target:
            raise ValueError("That category no longer exists.")
        descendants = [row[0] for row in connection.execute("SELECT id FROM categories WHERE parent_id = ?", (category_id,)).fetchall()]
        affected_ids = [category_id] + descendants
        marks = ",".join("?" for _ in affected_ids)
        transaction_ids = {
            row[0] for row in connection.execute(
                "SELECT id FROM transactions WHERE category_id IN ({})".format(marks), affected_ids
            )
        }
        transaction_ids.update(
            row[0] for row in connection.execute(
                "SELECT DISTINCT transaction_id FROM transaction_allocations WHERE category_id IN ({})".format(marks), affected_ids
            )
        )
        rule_count = connection.execute(
            "SELECT COUNT(*) FROM categorization_rules WHERE category_id IN ({})".format(marks), affected_ids
        ).fetchone()[0]
        suggestion_count = connection.execute(
            "SELECT COUNT(*) FROM transaction_reviews WHERE suggested_category_id IN ({})".format(marks), affected_ids
        ).fetchone()[0]
        try:
            connection.execute("UPDATE categories SET name = ? WHERE id = ?", (clean_name, category_id))
        except sqlite3.IntegrityError as error:
            raise ValueError("A category with this name already exists at this level.") from error
    return {
        "category_id": category_id,
        "old_name": target["name"],
        "new_name": clean_name,
        "transaction_count": len(transaction_ids),
        "rule_count": rule_count,
        "pending_suggestion_count": suggestion_count,
    }
