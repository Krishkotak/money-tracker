# Personal Money Tracker

A local Mac application for importing monthly bank-h, bank-s Card, and bank-k statements, reviewing new transactions, and reporting category spending. It does not send financial data to an AI service.

## Current build step

The local browser workflow now supports statement import, per-transaction categorization review, category creation and renaming, categorization-rule management, and date-range reports. It uses deterministic parsers and a local SQLite database. The browser app binds to this Mac only.

## Parse a statement locally

Requires Python 3.9 or newer and the project dependencies. With `uv` installed:

```sh
uv sync
uv run money-tracker-parse bank-h /path/to/bank-h-statement.xls
uv run money-tracker-parse bank-s "/path/to/bank-s-card-statement.pdf"
uv run money-tracker-parse bank-k /path/to/bank-k-statement.csv
```

The command prints the parsed period, row counts, totals, and validation findings. It does not print the transaction ledger or write statement contents to another file.

For command-line development, `money-tracker-import` parses selected files, saves transaction and statement history, applies confirmed rules to new transactions, and stores pending review items. It has no review interface; use the browser app for the monthly workflow.

## Use the local browser app

For a one-click launch on this Mac, double-click `Money Tracker.app` in the project folder. It starts the local server if it is not already running, then opens the default browser at `http://127.0.0.1:8765/`.

To build the launcher bundle from a checkout, run `./scripts/build-mac-app.sh` on macOS. The app bundle is generated locally and is intentionally ignored by Git; the script requires Apple's Swift compiler.

To launch from Terminal during development, run:

```sh
uv run money-tracker
```

Then open `http://127.0.0.1:8765/` on this Mac. The first visit prompts for statements. Later imports add only new transactions, and the app opens the review queue until every new item has a saved decision. The report then defaults to the latest calendar month with imported data. Use `--database /private/tmp/money-tracker-scratch.sqlite3` to try the workflow without creating the normal local database.

## Product workflow decisions

- Imports are incremental. Overlapping statement rows are deduplicated; only new transactions are categorized or reviewed.
- **Save review** stores a category for that transaction only. **Add as a rule** is a separate confirmation flow; it adds a source-independent payee-phrase rule and saves the current review decision together.
- The Rules section edits ordinary match names and categories. Changes can resolve matching items still waiting for review, but completed transaction decisions remain unchanged. Roommate rent / Splitwise rules are protected from editing and require two confirmations to disable.
- Personal counterparty match values and custom allocation settings stay in the local SQLite database and ignored local configuration. Do not commit statement files, transaction databases, or the local private config.
- Reports recalculate from saved transactions and decisions. They are not frozen snapshots.
- The default report is the newest calendar month with data, from the first of that month through its latest imported date. Any range within imported history can be selected.
- Confirmed incoming reimbursement rules offset their configured spending category. Unmatched outgoing payments stay in review with Auto / Cab suggested; unmatched incoming payments have no Auto / Cab suggestion. See [categorization-rules.md](docs/categorization-rules.md).
- bank-s purchase rows have no unique transaction ID. Overlap matching uses card, date, description, amount, direction, and occurrence count. Ambiguous matches must be reviewed before exclusion.
