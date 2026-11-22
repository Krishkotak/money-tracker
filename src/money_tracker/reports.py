"""Recalculated date-range reports from saved transactions and allocations."""

from datetime import date


def available_date_range(connection):
    row = connection.execute("SELECT MIN(transaction_date), MAX(transaction_date) FROM transactions").fetchone()
    if not row or row[0] is None:
        return None
    return {"start": row[0], "end": row[1]}


def default_date_range(connection):
    """Latest calendar month with data, ending on its latest imported transaction."""
    available = available_date_range(connection)
    if not available:
        return None
    last_date = date.fromisoformat(available["end"])
    first_date = last_date.replace(day=1)
    return {"start": first_date.isoformat(), "end": last_date.isoformat()}


def _category_name(connection, category_id):
    row = connection.execute("SELECT name, parent_id FROM categories WHERE id = ?", (category_id,)).fetchone()
    if not row:
        return "Uncategorized"
    if row["parent_id"] is None:
        return row["name"]
    parent = connection.execute("SELECT name FROM categories WHERE id = ?", (row["parent_id"],)).fetchone()
    return "{} / {}".format(parent["name"], row["name"]) if parent else row["name"]


def _amounts_for_range(connection, start_date, end_date):
    return connection.execute(
        """SELECT a.category_id, a.amount_paise, a.allocation_kind,
                  COALESCE(target.transaction_date, source.transaction_date) AS report_date
           FROM transaction_allocations a
           JOIN transactions source ON source.id = a.transaction_id
           LEFT JOIN transactions target ON target.id = a.applies_to_transaction_id
           WHERE COALESCE(target.transaction_date, source.transaction_date) BETWEEN ? AND ?""",
        (start_date.isoformat(), end_date.isoformat()),
    ).fetchall()


def report_for_range(connection, start_date, end_date):
    """Calculate totals on demand; a selected range need not align to a calendar month."""
    start = date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
    end = date.fromisoformat(end_date) if isinstance(end_date, str) else end_date
    available = available_date_range(connection)
    if start > end:
        raise ValueError("The report start date must be on or before the end date.")
    if available and (start.isoformat() < available["start"] or end.isoformat() > available["end"]):
        raise ValueError("Choose dates within the imported transaction history.")
    if not available:
        return {
            "start": start.isoformat(), "end": end.isoformat(), "gross_expenses_paise": 0,
            "offsets_paise": 0, "net_spending_paise": 0, "income_paise": 0,
            "pending_review_count": 0, "categories": [], "income_by_category": [],
        }
    allocations = _amounts_for_range(connection, start, end)
    gross = sum(row["amount_paise"] for row in allocations if row["allocation_kind"] == "expense")
    offsets = sum(row["amount_paise"] for row in allocations if row["allocation_kind"] == "offset")
    by_category = {}
    for row in allocations:
        data = by_category.setdefault(row["category_id"], {"gross": 0, "offsets": 0})
        key = "gross" if row["allocation_kind"] == "expense" else "offsets"
        data[key] += row["amount_paise"]

    roots = connection.execute("SELECT id, name FROM categories WHERE parent_id IS NULL ORDER BY name COLLATE NOCASE").fetchall()
    categories = []
    for root in roots:
        child_rows = connection.execute("SELECT id, name FROM categories WHERE parent_id = ? ORDER BY name COLLATE NOCASE", (root["id"],)).fetchall()
        member_ids = [root["id"]] + [child["id"] for child in child_rows]
        root_gross = sum(by_category.get(category_id, {}).get("gross", 0) for category_id in member_ids)
        root_offsets = sum(by_category.get(category_id, {}).get("offsets", 0) for category_id in member_ids)
        if not root_gross and not root_offsets:
            continue
        subcategories = []
        for category_id in member_ids:
            values = by_category.get(category_id, {"gross": 0, "offsets": 0})
            if not values["gross"] and not values["offsets"]:
                continue
            category = connection.execute("SELECT parent_id, name FROM categories WHERE id = ?", (category_id,)).fetchone()
            name = "Direct / no subcategory" if category_id == root["id"] and child_rows else category["name"]
            subcategories.append({
                "category_id": category_id,
                "name": name,
                "gross_expenses_paise": values["gross"],
                "offsets_paise": values["offsets"],
                "net_spending_paise": values["gross"] - values["offsets"],
            })
        subcategories.sort(key=lambda item: item["net_spending_paise"], reverse=True)
        categories.append({
            "category_id": root["id"],
            "name": root["name"],
            "gross_expenses_paise": root_gross,
            "offsets_paise": root_offsets,
            "net_spending_paise": root_gross - root_offsets,
            "subcategories": subcategories,
        })
    categories.sort(key=lambda item: item["net_spending_paise"], reverse=True)

    income_rows = connection.execute(
        """SELECT category_id, SUM(amount_paise) AS total FROM transactions
           WHERE classification = 'income' AND transaction_date BETWEEN ? AND ?
           GROUP BY category_id""",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    income_by_category = [
        {"category_id": row["category_id"], "name": _category_name(connection, row["category_id"]), "amount_paise": row["total"]}
        for row in income_rows
    ]
    pending_reviews = connection.execute(
        """SELECT COUNT(*) FROM transaction_reviews r JOIN transactions t ON t.id = r.transaction_id
           WHERE r.status = 'pending' AND t.transaction_date BETWEEN ? AND ?""",
        (start.isoformat(), end.isoformat()),
    ).fetchone()[0]
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "gross_expenses_paise": gross,
        "offsets_paise": offsets,
        "net_spending_paise": gross - offsets,
        "income_paise": sum(row["total"] for row in income_rows),
        "pending_review_count": pending_reviews,
        "categories": categories,
        "income_by_category": income_by_category,
    }
