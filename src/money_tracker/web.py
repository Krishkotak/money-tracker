"""Small local-only browser interface for monthly statement review."""

import html
from email import policy
from email.parser import BytesParser
import tempfile
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from .database import connect, initialize
from .workflows import add_category, category_tree, dashboard, delete_special_rule, edit_category_name, edit_rule, import_files, review_possible_duplicate, review_transaction, review_transaction_with_rule, rules_page


MAX_UPLOAD_BYTES = 40 * 1024 * 1024
SOURCES = ("hsbc", "sbi", "kotak")

STYLE = """
:root{font:16px/1.5 -apple-system,BlinkMacSystemFont,sans-serif;color:#18231d;background:#f4f7f4}
body{max-width:1180px;margin:40px auto;padding:0 22px}h1{font-size:2.3rem;line-height:1.1}h2{margin-top:0}a{color:#176b4b}
.top,.row,.actions{display:flex;align-items:center;justify-content:space-between;gap:16px}.cards,.layout{display:grid;gap:16px}.cards{grid-template-columns:repeat(3,1fr)}.layout{grid-template-columns:1.1fr .9fr}.cards.highlight{grid-template-columns:repeat(2,1fr)}.card.highlight-income{background:#e7f3eb;border-color:#c4dfce}.card.highlight-ratio{background:#eaf0f8;border-color:#cbd9ec}
section,.card{background:white;border:1px solid #dce5de;border-radius:18px;padding:24px;margin:16px 0}.big{font-size:2rem;font-weight:700}.muted{color:#637168}.notice{background:#eaf4ee;padding:16px;border-radius:12px}
.share{font-weight:700}
button,.button{border:1px solid #b9c9be;background:#fff;border-radius:10px;padding:11px 16px;font:inherit;font-weight:600;cursor:pointer;color:inherit}button.primary{background:#176b4b;color:#fff;border-color:#176b4b}input,select{font:inherit;padding:10px;border:1px solid #b9c9be;border-radius:8px;background:#fff;max-width:100%}label{display:block;margin:12px 0 5px}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:12px 8px;border-bottom:1px solid #e5ebe6}article{padding:16px 0;border-bottom:1px solid #e5ebe6}.category{margin:12px 0}.category small{color:#637168}.inline{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.right{text-align:right}
@media(max-width:760px){body{margin:20px auto}.cards,.cards.highlight,.layout{grid-template-columns:1fr}.big{font-size:1.7rem}}
"""


def _esc(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def _money(paise):
    return "₹{:,.2f}".format(paise / 100)


def _page(title, content):
    return "<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{}</title><style>{}</style><body><header class='top'><div><p class='muted'>PERSONAL MONEY TRACKER · LOCAL</p><h1>{}</h1></div><nav class='inline'><a href='/'>Report</a><a href='/categories'>Categories</a><a href='/rules'>Rules</a></nav></header>{}</body></html>".format(
        _esc(title), STYLE, _esc(title), content
    )


def _response(start_response, body, status=HTTPStatus.OK, headers=None):
    payload = body.encode("utf-8")
    response_headers = [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(payload))), ("Cache-Control", "no-store")]
    response_headers.extend(headers or [])
    start_response("{} {}".format(status.value, status.phrase), response_headers)
    return [payload]


def _redirect(start_response, location="/"):
    start_response("303 See Other", [("Location", location), ("Content-Length", "0"), ("Cache-Control", "no-store")])
    return [b""]


def _upload_form():
    return """<section><h2>Add statements</h2><p class='muted'>Choose any statements available for this period. Already imported transactions are recognized when statements overlap.</p><form method='post' action='/import' enctype='multipart/form-data'>
    <label for='hsbc'>HSBC bank statement (.xls)</label><input id='hsbc' name='hsbc' type='file' accept='.xls,.xlsx'>
    <label for='sbi'>SBI card statement (.pdf)</label><input id='sbi' name='sbi' type='file' accept='.pdf'>
    <label for='kotak'>Kotak bank statement (.csv)</label><input id='kotak' name='kotak' type='file' accept='.csv'>
    <p><button class='primary' type='submit'>Import statements</button></p></form></section>"""


def _category_options(categories, selected=None, allow_add=True):
    options = ["<option value=''>Choose a category…</option>"]
    for parent in categories:
        options.append("<optgroup label='{}'>".format(_esc(parent["name"])))
        options.append("<option value='{}'{}>{}</option>".format(_esc(parent["id"]), " selected" if selected == parent["id"] else "", _esc(parent["name"])))
        for child in parent["children"]:
            options.append("<option value='{}'{}>{}</option>".format(_esc(child["id"]), " selected" if selected == child["id"] else "", _esc(child["name"])))
        options.append("</optgroup>")
    if allow_add:
        options.append("<option value='__add__'>Add a category…</option>")
    return "".join(options)


def _category_label(categories, category_id):
    for parent in categories:
        if parent["id"] == category_id:
            return parent["name"]
        for child in parent["children"]:
            if child["id"] == category_id:
                return "{} / {}".format(parent["name"], child["name"])
    return None


def _rules_content(connection):
    rules = rules_page(connection)
    categories = category_tree(connection)
    if not rules:
        return "<section><h2>Categorization rules</h2><p class='muted'>No active rules are configured.</p></section>"
    blocks = ["<section><h2>Categorization rules</h2><p class='muted'>Ordinary rules match the counterparty name using their saved match type. Edits apply to matching transactions still waiting for review; completed transaction decisions stay unchanged.</p>"]
    for rule in rules:
        direction = "Outgoing" if rule["direction"] == "debit" else "Incoming" if rule["direction"] == "credit" else "Any direction"
        source = rule["source"].upper() if rule["source"] else "Any statement source"
        if rule["special"]:
            blocks.append("""<article><div class='row'><div><strong>{}</strong><div class='muted'>{} · {} · {}</div><div>{}</div></div><strong>{}</strong></div><p class='muted'>Special roommate rent / Splitwise allocation. This rule cannot be edited here. Deleting it leaves all existing transaction records unchanged.</p><form method='post' action='/rules'><input type='hidden' name='action' value='delete_start'><input type='hidden' name='rule_id' value='{}'><button>Delete special rule…</button></form></article>""".format(
                _esc(rule["id"]), _esc(source), _esc(direction), _esc(rule["match_type"]), _esc(rule["summary"]), _esc(rule["category"]), _esc(rule["id"])
            ))
        else:
            mode = "Exact counterparty" if rule["match_type"] == "counterparty_exact" else "Contains phrase"
            blocks.append("""<article><form method='post' action='/rules'><input type='hidden' name='action' value='update'><input type='hidden' name='rule_id' value='{}'><div class='row'><div><strong>{}</strong><div class='muted'>{} · {} · {}</div></div></div><label>Match type</label><p>{}</p><label for='match-{}'>Counterparty name / phrase</label><input id='match-{}' name='match_value' value='{}' maxlength='120' required><label for='category-{}'>Category</label><select id='category-{}' name='category_id'>{}</select><p><button class='primary' type='submit'>Save rule</button></p></form></article>""".format(
                _esc(rule["id"]), _esc(rule["id"]), _esc(source), _esc(direction), _esc(mode), _esc(mode),
                _esc(rule["id"]), _esc(rule["id"]), _esc(rule["match_value"]), _esc(rule["id"]), _esc(rule["id"]),
                _category_options(categories, rule["category_id"], allow_add=False),
            ))
    blocks.append("</section>")
    return "".join(blocks)


def _delete_rule_confirmation(rule, step):
    if step == 1:
        prompt = "First confirmation: are you sure you want to disable this special rule?"
        action, button = "delete_second", "Yes, continue"
    else:
        prompt = "Second confirmation: permanently stop this rule from applying to future transactions? Existing transaction records will remain unchanged."
        action, button = "delete_final", "Confirm deletion"
    content = """<section><h2>Delete special rule</h2><p>{}</p><p><strong>{}</strong>: {}</p>
    <form method='post' action='/rules'><input type='hidden' name='action' value='{}'><input type='hidden' name='rule_id' value='{}'><button class='primary' type='submit'>{}</button> <a class='button' href='/rules'>Cancel</a></form></section>""".format(
        _esc(prompt), _esc(rule["summary"]), _esc(rule["category"]), action, _esc(rule["id"]), _esc(button)
    )
    return _page("Confirm rule deletion", content)


def _rule_confirmation_page(transaction, category_id, categories, phrase=None):
    category_label = _category_label(categories, category_id)
    if not category_label:
        raise ValueError("Choose an active category before adding a rule.")
    phrase = transaction["counterparty_raw"] if phrase is None else phrase
    content = """<section><h2>Add a categorization rule</h2>
    <p>This will save <strong>{}</strong> for the current transaction and add a rule for future matching transactions.</p>
    <form method='post' action='/review'>
    <input type='hidden' name='action' value='save_with_rule'>
    <input type='hidden' name='transaction_id' value='{}'>
    <input type='hidden' name='category_id' value='{}'>
    <label for='rule-phrase'>Match this phrase in the counterparty name</label>
    <input id='rule-phrase' name='rule_phrase' value='{}' maxlength='120' required>
    <p class='muted'>This case-insensitive phrase match applies to {} transactions from HSBC, SBI, or Kotak.</p>
    <p><button class='primary' type='submit'>Add rule and save review</button> <a class='button' href='/'>Cancel</a></p>
    </form></section>""".format(
        _esc(category_label), _esc(transaction["id"]), _esc(category_id), _esc(phrase),
        _esc("outgoing" if transaction["direction"] == "debit" else "incoming"),
    )
    return _page("Confirm categorization rule", content)


def _review_panel(state):
    items = state["reviews"]
    duplicates = state["duplicates"]
    if not items and not duplicates:
        return ""
    categories = state["categories"]
    blocks = ["<section><div class='row'><div><h2>Review new items</h2><p class='muted'>Save a decision for each item. A review decision applies only to that transaction.</p></div><strong>{} items</strong></div>".format(len(items) + len(duplicates))]
    for item in items:
        suggested = "<p class='notice'>Suggested: {}</p>".format(_esc(item["suggested_category"])) if item["suggested_category"] else ""
        blocks.append("""<article><div class='row'><div><strong>{}</strong><div class='muted'>{} · {} · {} · {}</div><div>{}</div></div><strong>{}</strong></div><p class='muted'>{}</p>{}<form method='post' action='/review'><input type='hidden' name='transaction_id' value='{}'><label>Category</label><select name='category_id' onchange="if(this.value==='__add__')location.href='/categories?return=review'">{}</select><label class='inline'><input type='checkbox' name='transfer' value='yes'> This is a transfer between my accounts</label><p><button class='primary' type='submit' name='action' value='save_review'>Save review</button> <button type='submit' name='action' value='prepare_rule'>Add as a rule</button></p></form></article>""".format(
            _esc(item["counterparty"]), _esc(item["date"]), _esc(item["source"].upper()), _esc(item["account"]), _esc(item["direction"]), _esc(item["description"]),
            _money(item["amount_paise"]), _esc(item["reason"]), suggested, _esc(item["transaction_id"]),
            _category_options(categories, item["suggested_category_id"]),
        ))
    for item in duplicates:
        blocks.append("""<article><strong>Possible duplicate statement row</strong><div class='muted'>{} · {} · {}</div><p>{} · {} and possible match: {} · {} · {}</p><form method='post' action='/duplicate'><input type='hidden' name='review_id' value='{}'><button class='primary' name='same_transaction' value='yes'>Same transaction</button> <button name='same_transaction' value='no'>Different transaction</button></form></article>""".format(
            _esc(item["source"].upper()), _esc(item["transaction_date"]), _money(item["amount_paise"]), _esc(item["counterparty_raw"]), _esc(item["direction"]), _esc(item["candidate_date"]), _money(item["candidate_amount_paise"]), _esc(item["candidate_counterparty"]), _esc(item["review_id"])
        ))
    blocks.append("</section>")
    return "".join(blocks)


def _report_page(connection, query):
    state = dashboard(connection, query.get("start", [None])[0], query.get("end", [None])[0])
    if state["state"] == "review":
        return _page("Review transactions", _review_panel(state) + _upload_form())
    if state["state"] == "empty":
        return _page("Your money overview", "<p class='notice'>No statements have been imported yet. Add your first statements to begin.</p>" + _upload_form())

    available = state["available"]
    report = state["report"]
    start, end = state["start"], state["end"]
    income = report["income_paise"]
    net = report["net_spending_paise"]
    net_to_income = "{:.1f}%".format(net / income * 100) if income else "—"

    def percent_of_net(amount):
        return "{:.1f}%".format(amount / net * 100) if net > 0 else "—"

    summary_cards = "<div class='cards highlight'><div class='card highlight-income'><span>Income</span><div class='big'>{}</div></div><div class='card highlight-ratio'><span>Net spending as % of income</span><div class='big'>{}</div></div></div>".format(
        _money(income), _esc(net_to_income)
    )
    spending_cards = "<div class='cards'><div class='card'><span>Net spending</span><div class='big'>{}</div></div><div class='card'><span>Spending offsets</span><div class='big'>{}</div></div><div class='card'><span>Gross expenses</span><div class='big'>{}</div></div></div>".format(
        _money(net), _money(report["offsets_paise"]), _money(report["gross_expenses_paise"])
    )

    category_rows = []
    for category in report["categories"]:
        subcategories = "".join(
            "<li>{}: {} gross · {} offsets · {} net (<strong class='share'>{}</strong>)</li>".format(
                _esc(sub["name"]), _money(sub["gross_expenses_paise"]), _money(sub["offsets_paise"]),
                _money(sub["net_spending_paise"]), percent_of_net(sub["net_spending_paise"]),
            )
            for sub in category["subcategories"]
        )
        category_rows.append(
            "<tr><td><details><summary><strong>{}</strong></summary><ul>{}</ul></details></td><td>{}</td><td>{}</td><td>{} <strong class='muted share'>({})</strong></td></tr>".format(
                _esc(category["name"]), subcategories, _money(category["gross_expenses_paise"]),
                _money(category["offsets_paise"]), _money(category["net_spending_paise"]),
                percent_of_net(category["net_spending_paise"]),
            )
        )

    income_breakdown = "<ul>{}</ul>".format("".join(
        "<li>{}: {}</li>".format(_esc(row["name"]), _money(row["amount_paise"]))
        for row in sorted(report["income_by_category"], key=lambda row: row["amount_paise"], reverse=True)
    )) if report["income_by_category"] else "<p class='muted'>No income by category in this date range.</p>"
    content = "<section><div class='row'><div><h2>Spending report</h2><p class='muted'>Choose any date range within imported history.</p></div><form method='get' action='/' class='inline'><label>From <input type='date' name='start' min='{}' max='{}' value='{}'></label><label>To <input type='date' name='end' min='{}' max='{}' value='{}'></label><button>Update report</button></form></div><p class='muted'>Available history: {} to {}</p>{}<h2>Spending summary</h2>{}<h2>Category totals</h2><table><thead><tr><th>Category</th><th>Gross</th><th>Offsets</th><th>Net</th></tr></thead><tbody>{}</tbody></table><h2>Income</h2><p class='big'>{}</p>{}{}</section>".format(
        _esc(available["start"]), _esc(available["end"]), _esc(start),
        _esc(available["start"]), _esc(available["end"]), _esc(end),
        _esc(available["start"]), _esc(available["end"]), summary_cards, spending_cards,
        "".join(category_rows), _money(income), income_breakdown, _upload_form(),
    )
    return _page("Your money overview", content)


def _categories_page(categories, query):
    sections = ["<section><h2>Add a category</h2><form method='post' action='/categories'><input type='hidden' name='action' value='create'><label>Main category name</label><input name='main_name' maxlength='80'><label>Or add a subcategory under</label><select name='parent_id'><option value=''>No parent (main category)</option>{}</select><label>Subcategory name</label><input name='child_name' maxlength='80'><p><button class='primary'>Add category</button></p></form></section><section><h2>Rename a category</h2><p class='muted'>Renaming preserves its stable ID, so existing transactions, rules, and suggestions use the updated name.</p>""".format(
        "".join("<option value='{}'>{}</option>".format(_esc(parent["id"]), _esc(parent["name"])) for parent in categories)
    )]
    for parent in categories:
        sections.append("<div class='category'><form method='post' action='/categories' class='inline'><input type='hidden' name='action' value='rename'><input type='hidden' name='category_id' value='{}'><input name='name' value='{}' required><button>Rename main category</button></form>".format(_esc(parent["id"]), _esc(parent["name"])))
        for child in parent["children"]:
            sections.append("<form method='post' action='/categories' class='inline'><input type='hidden' name='action' value='rename'><input type='hidden' name='category_id' value='{}'><input name='name' value='{}' required><button>Rename subcategory</button><small>under {}</small></form>".format(_esc(child["id"]), _esc(child["name"]), _esc(parent["name"])))
        sections.append("</div>")
    sections.append("</section><p><a href='{}'>Return to {}</a></p>".format("/" if query.get("return", [""])[0] != "review" else "/", "report" if query.get("return", [""])[0] != "review" else "review"))
    return _page("Categories", "".join(sections))


def _form_values(environ):
    length = int(environ.get("CONTENT_LENGTH") or "0")
    if length > MAX_UPLOAD_BYTES:
        raise ValueError("Statement uploads must be smaller than 40 MB.")
    body = environ["wsgi.input"].read(length)
    content_type = environ.get("CONTENT_TYPE", "")
    values = {}
    if content_type.lower().startswith("multipart/form-data"):
        message = BytesParser(policy=policy.default).parsebytes(
            ("Content-Type: {}\r\nMIME-Version: 1.0\r\n\r\n".format(content_type)).encode("ascii") + body
        )
        if not message.is_multipart():
            raise ValueError("The uploaded form could not be read.")
        for part in message.iter_parts():
            disposition = part.get_content_disposition()
            name = part.get_param("name", header="content-disposition")
            if disposition != "form-data" or not name:
                continue
            payload = part.get_payload(decode=True) or b""
            filename = part.get_filename()
            values[name] = {"filename": filename, "data": payload} if filename is not None else payload.decode("utf-8", "replace")
    else:
        values = {key: items[-1] for key, items in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items()}
    return values


def _application(environ, start_response, database_path):
    method = environ["REQUEST_METHOD"]
    path = environ.get("PATH_INFO", "/")
    query = parse_qs(environ.get("QUERY_STRING", ""))
    connection = connect(database_path)
    initialize(connection)
    try:
        if method == "GET" and path == "/":
            return _response(start_response, _report_page(connection, query))
        if method == "GET" and path == "/categories":
            return _response(start_response, _categories_page(category_tree(connection), query))
        if method == "GET" and path == "/rules":
            return _response(start_response, _page("Categorization rules", _rules_content(connection)))
        if method != "POST":
            return _response(start_response, _page("Not found", "<p>That page does not exist.</p>"), HTTPStatus.NOT_FOUND)
        try:
            values = _form_values(environ)
            if path == "/import":
                with tempfile.TemporaryDirectory(prefix="money-tracker-upload-") as directory:
                    source_paths = []
                    for source in SOURCES:
                        uploaded = values.get(source)
                        if not isinstance(uploaded, dict) or not uploaded["filename"]:
                            continue
                        suffix = Path(uploaded["filename"]).suffix or {"hsbc": ".xls", "sbi": ".pdf", "kotak": ".csv"}[source]
                        file_path = Path(directory) / (source + suffix)
                        file_path.write_bytes(uploaded["data"])
                        if file_path.stat().st_size > MAX_UPLOAD_BYTES:
                            raise ValueError("Statement uploads must be smaller than 40 MB.")
                        source_paths.append((source, file_path))
                    if not source_paths:
                        raise ValueError("Choose at least one statement file.")
                    import_files(connection, source_paths)
                return _redirect(start_response, "/")
            if path == "/review":
                action = values.get("action", "save_review")
                transaction_id = values.get("transaction_id")
                if action == "prepare_rule":
                    if values.get("transfer") == "yes":
                        raise ValueError("A transfer does not need a category rule.")
                    transaction = connection.execute(
                        """SELECT t.* FROM transactions t JOIN transaction_reviews r ON r.transaction_id=t.id
                           WHERE t.id=? AND r.status='pending'""",
                        (transaction_id,),
                    ).fetchone()
                    if not transaction:
                        raise ValueError("This transaction is no longer waiting for review.")
                    return _response(
                        start_response,
                        _rule_confirmation_page(transaction, values.get("category_id"), category_tree(connection)),
                    )
                if action == "save_with_rule":
                    review_transaction_with_rule(
                        connection,
                        transaction_id,
                        category_id=values.get("category_id"),
                        phrase=values.get("rule_phrase"),
                    )
                elif action == "save_review":
                    review_transaction(
                        connection,
                        transaction_id,
                        category_id=values.get("category_id"),
                        transfer=values.get("transfer") == "yes",
                    )
                else:
                    raise ValueError("Choose how to save this review.")
                return _redirect(start_response, "/")
            if path == "/duplicate":
                review_possible_duplicate(connection, values.get("review_id"), same_transaction=values.get("same_transaction") == "yes")
                return _redirect(start_response, "/")
            if path == "/rules":
                action = values.get("action")
                rule_id = values.get("rule_id")
                if action == "update":
                    edit_rule(connection, rule_id, values.get("match_value"), values.get("category_id"))
                    return _redirect(start_response, "/rules")
                rule = next((item for item in rules_page(connection) if item["id"] == rule_id), None)
                if not rule or not rule["special"]:
                    raise ValueError("That special rule is no longer active.")
                if action == "delete_start":
                    step = 1
                elif action == "delete_second":
                    step = 2
                elif action == "delete_final":
                    delete_special_rule(connection, rule_id)
                    return _redirect(start_response, "/rules")
                else:
                    raise ValueError("Choose a rule action.")
                return _response(start_response, _delete_rule_confirmation(rule, step))
            if path == "/categories":
                if values.get("action") == "create":
                    parent_id = values.get("parent_id") or None
                    name = values.get("child_name") if parent_id else values.get("main_name")
                    add_category(connection, name, parent_id)
                elif values.get("action") == "rename":
                    edit_category_name(connection, values.get("category_id"), values.get("name"))
                else:
                    raise ValueError("Choose a category action.")
                return _redirect(start_response, "/categories")
            return _response(start_response, _page("Not found", "<p>That page does not exist.</p>"), HTTPStatus.NOT_FOUND)
        except (ValueError, OSError) as error:
            return _response(start_response, _page("Could not complete that action", "<p class='notice'>{}</p><p><a href='/'>Return to the tracker</a></p>".format(_esc(error))), HTTPStatus.BAD_REQUEST)
    finally:
        connection.close()


def create_app(database_path=None):
    """Return a WSGI app bound to a local SQLite database."""
    path = database_path or Path.home() / "Library" / "Application Support" / "Personal Money Tracker" / "money-tracker.sqlite3"

    def application(environ, start_response):
        try:
            return _application(environ, start_response, path)
        except Exception:
            return _response(start_response, _page("The tracker encountered an error", "<p class='notice'>The request could not be completed. Return to the tracker and check its current state before retrying.</p><p><a href='/'>Return to the tracker</a></p>"), HTTPStatus.INTERNAL_SERVER_ERROR)

    return application


def serve(database_path=None, host="127.0.0.1", port=8765):
    """Start the local-only browser app."""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("The browser app can only bind to this Mac.")
    application = create_app(database_path)
    with make_server(host, port, application) as server:
        print("Money Tracker running at http://{}:{}/".format(host, port))
        print("Press Ctrl+C to stop.")
        server.serve_forever()


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Run the local Personal Money Tracker browser interface.")
    parser.add_argument("--database", type=Path, help="Local SQLite database path")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    serve(args.database, port=args.port)
