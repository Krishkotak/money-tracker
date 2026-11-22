# MMEX Repository Reuse Assessment

**Reviewed:** 21 November 2026
**Sources:** official `master` branch pages for the MMEX Desktop and database repositories. These branches are mutable; this is a point-in-time source review, not a pinned-version audit.

## Recommendation

Build this project as a small, purpose-built local application and use MMEX as a reference rather than forking MMEX Desktop as the application base. Reuse ideas from the database repository selectively, and keep MMEX-compatible export or import as an optional future decision. Do not adopt the MMEX schema unchanged as the application's own model without first checking it against the domain model already agreed for this project.

The main reason is scope fit. MMEX Desktop is a broad cross-platform personal-finance product written primarily in C++17 with wxWidgets, with accounts, reports, budgeting, investments, scheduled transactions, CSV/QIF import, and encryption. This project is a personal Mac-first monthly statement importer and review workflow for three known statement formats. Most MMEX UI and product surface would be unused, while the statement-specific parsing, duplicate protection, card settlement matching, and roommate reimbursement treatment would still need to be built. ([Desktop README](https://github.com/moneymanagerex/moneymanagerex/blob/master/README.md))

## Repository findings

### MMEX Desktop

- The project describes itself as a cross-platform finance application built primarily in C++17 and wxWidgets; it uses SQLite and lists CSV and QIF import in its feature set. Its source is organized into many application areas such as `app`, `db`, `import_export`, `manager`, `model`, `panel`, `report`, and `wizard`. ([README](https://github.com/moneymanagerex/moneymanagerex/blob/master/README.md), [`src` tree](https://github.com/moneymanagerex/moneymanagerex/tree/master/src), [`src/import_export` tree](https://github.com/moneymanagerex/moneymanagerex/tree/master/src/import_export))
- Import/export is a mature subsystem with OFX, QIF, CSV, and WebApp files visible in `src/import_export`. It is useful prior art for data import/export flows, but the public feature list does not claim support for the HSBC OOXML workbook with an `.xls` extension or the specific Kotak CSV/SBI card PDF formats in this project. The inputs still call for dedicated statement adapters and validation. ([Import/export source tree](https://github.com/moneymanagerex/moneymanagerex/tree/master/src/import_export), [README](https://github.com/moneymanagerex/moneymanagerex/blob/master/README.md))
- Desktop supports ordinary personal-finance concepts that overlap here: checking and credit-card accounts, nested categories, scheduled transactions, reports, and SQLite data. That makes it a useful product and vocabulary reference. Its full UI/runtime stack is a substantial base for a small private workflow, and a fork would inherit the larger product's conventions and maintenance surface. ([README](https://github.com/moneymanagerex/moneymanagerex/blob/master/README.md))
- The repository identifies its license as GPL-2.0. If code is copied or linked into a distributed derivative, license obligations need review. For a private local project, the more immediate practical point is that importing MMEX code would still bring its existing stack and conventions along. ([Repository license listing](https://github.com/moneymanagerex/moneymanagerex), [`LICENSE`](https://github.com/moneymanagerex/moneymanagerex/blob/master/LICENSE))

### MMEX database

- This repository is a database interface/schema repository, not a separate ready-made categorization engine. Its README points to `tables.sql` and documents a schema baseline, category defaults, and a PocketBase schema. It explicitly says PocketBase IDs, rules, indexes, and storage semantics differ from SQLite. ([README](https://github.com/moneymanagerex/database/blob/master/README.md))
- The SQLite schema separates accounts (`ACCOUNTLIST_V1`), categories and parent-child hierarchy (`CATEGORY_V1`), payees (`PAYEE_V1`), transactions (`CHECKINGACCOUNT_V1`), and split category lines (`SPLITTRANSACTIONS_V1`). Transactions store account, optional destination account, payee, direction/type (`Withdrawal`, `Deposit`, `Transfer`), amount, transaction number, category, and date. This is useful reference material for explicit account, payee, category, transfer, and optional split concepts. ([Schema: accounts/categories/transactions](https://github.com/moneymanagerex/database/blob/master/tables.sql), [raw schema](https://raw.githubusercontent.com/moneymanagerex/database/master/tables.sql))
- MMEX defaults include familiar category trees such as Bills → Electricity/Rent, Food → Groceries, Travel, and Income → Reimbursement/Refunds. These defaults are examples, not a requirement for this project's user-defined category names. ([Schema category seed data](https://github.com/moneymanagerex/database/blob/master/tables.sql), [README category list](https://github.com/moneymanagerex/database/blob/master/README.md))
- The schema has useful fields for a conventional finance register but does not directly provide the project's essential import provenance and workflow semantics as described in the inspected transaction table: source statement/file identity and row locator, statement transaction ID/reference, duplicate/import-batch tracking, categorization-rule match and confidence/review state, or links from a bank card-payment debit to the card-statement settlement pair. The project would need to model such data separately or extend the schema. The database README also notes that some polymorphic references are not enforced as SQL relations. ([Schema](https://github.com/moneymanagerex/database/blob/master/tables.sql), [README relation notes](https://github.com/moneymanagerex/database/blob/master/README.md))
- The repository license is GPL-2.0. Treat its schema as reference material unless there is a deliberate decision to be MMEX-compatible. ([Repository license listing](https://github.com/moneymanagerex/database))

## Fit to this project

| Need | MMEX reuse value | Assessment |
|---|---|---|
| Local personal accounts, categories, payees, transfers | Strong conceptual reference | The schema contains these concepts, including separate checking and credit-card accounts and transfer type. |
| HSBC workbook, Kotak CSV, SBI statement PDF parsing | Low direct reuse | Existing general import/export supports common interchange formats, but no evidence found of adapters for these exact statement layouts. Dedicated deterministic parsers remain necessary. |
| Apply confirmed merchant/payee rules, queue uncertain items | Partial reference | Payees can carry a pattern field and default category, but inspected source/schema does not evidence the review queue and rule provenance semantics this product needs. |
| Avoid card-payment double counting | Partial reference | MMEX supports transfer transactions and card accounts; this project still needs explicit reconciliation between bank-side settlement and card-side purchase entries. |
| Roommate rent and Splitwise offsets | Low direct reuse | Categories, income, and transfers can represent general flows, but the chosen rule—fixed rent shares allocated to the rent payment, other reimbursements offsetting Household & Groceries without category splits—needs project-specific reporting logic. |

## Practical decision

1. **Do not fork MMEX Desktop as the product foundation.** Its broad C++/wxWidgets application is more product than this workflow needs, and the custom ingestion and review behavior would remain project-specific.
2. **Use MMEX concepts and schema as a checklist, not as an automatic schema mandate.** Keep the domain model focused on source statement entries, transactions, payees/merchants, categories, confirmed rules, review decisions, settlements/transfers, and reimbursements. Only take on MMEX compatibility if opening the same database in MMEX later becomes useful.
This assessment covers repository docs, public source-tree structure, README, and the official database schema. It is not an exhaustive audit of every importer implementation, current build health, or database migration behavior. No MMEX code was copied, and no application implementation was performed.
