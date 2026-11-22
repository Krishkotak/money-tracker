# Categorization rule notes

This note describes generic rule behavior. Counterparty names and user-specific match values are local data; they live in the SQLite database and optional ignored `src/money_tracker/private_config.py` file.

## Confirmed rules and proposed classification behavior

- Apply exact source and merchant rules before any general classifier.
- Any unmatched outgoing payment stays uncategorized in the review queue with **Travel / Auto / Cab** suggested. This is a suggestion only; accepting it is a manual transaction decision and does not create a reusable rule. Known exact rules are applied first.
- Confirmed incoming sender rules can offset a chosen spending category. Keep unmatched incoming credits in review without the Auto / Cab suggestion.

No LLM or cloud classifier is used. Incoming credits do not receive the Auto / Cab suggestion; unmatched credits remain in review without that suggestion.

## Adding a rule from review

- **Save review** stores the selected category for only that transaction.
- **Add as a rule** opens a confirmation step with the selected category and counterparty prefilled. The user may edit the phrase before confirming.
- A confirmed rule matches the phrase anywhere in the normalized counterparty name, regardless of statement source, and keeps the reviewed transaction's direction.
- Confirming creates the rule and saves the current transaction's review decision together. The rule applies to future matching transactions and matching transactions still waiting for review; completed manual decisions are never changed.
- If the same phrase and direction already point to a different category, rule creation must fail without saving the review decision. This avoids silently making rule outcomes depend on insertion order.

## Managing existing rules

- Ordinary active rules can be edited in the Rules section. Editing preserves each rule's match type, statement-source scope, and direction; only its counterparty match text and category change.
- After an edit, apply the updated rule to matching transactions still waiting for review. Do not alter completed transaction decisions or their allocations.
- Roommate rent / Splitwise rules have custom allocation behavior and are displayed as protected special rules. They cannot be edited. They may be disabled only after two separate confirmations. Disabling a rule does not rewrite existing transaction records, and disabled seeded rules must remain disabled on later app startup.

## Applying rule changes to existing transactions

When a new confirmed rule is seeded, apply it to matching transactions that are still unresolved in the review queue. Save the resulting category and allocation on those transactions. Do not overwrite a transaction that has already been manually resolved; that decision is transaction-specific.

## Updating incoming sender rules

Add the exact statement counterparty name, direction, source scope (if needed), and intended category/offset behavior to the local database or ignored private config. Do not put personal counterparty names or statement details in tracked documentation.
