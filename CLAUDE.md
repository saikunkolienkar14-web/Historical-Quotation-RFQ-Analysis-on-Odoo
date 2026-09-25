# CLAUDE.md

Working instructions for this repository. For installation and run
detail see [`README.md`](README.md); for current status and known issues
see [`PROJECT_NOTES.md`](PROJECT_NOTES.md); for the schema of any output
CSV see [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md). This file
does not restate those — it carries the rules that must hold across every
piece of work here.


## Objective

Build a structured understanding of historical quotations and RFQs by
extracting, organizing and analysing past quoting data from Odoo and its
attachments.

The end goal is two things:

1. A reusable **knowledge bank** — one normalized dataset covering what we
   quoted (item, make, model, version, quantity, price), who we quoted it
   to (customer, industry, region), and when.
2. An **analytics layer** over it, surfacing pricing trends, customer
   patterns, and repetitive quoting modules.

Analysis targets: per-item price trends over time, customer-wise quoting
and pricing trends, industry-wise breakdowns, commonly quoted modules and
product bundles, and the repetitive quoting patterns that point at
standardization opportunities.


## Pipeline

| Stage | Script | Input | Output |
|---|---|---|---|
| 1 | `quotation_pdf_preprocessor.py` | `Downloads/` | `Quotation_Preprocessed/text/` |
| 2 | `preprocess_quotation_text.py` | `Quotation_Preprocessed/text/` | `02_clean_text/` |
| 3 | `remove_repeated _messagev2.py` | `02_clean_text/` | `03_preprocessed_text_2/` |
| QA | `validate_quotations.py` | `02_clean_text/` | `04_validation/` |
| 4 | `quotation_parser_v1.py` | `03_preprocessed_text_2/` | `03_structured_current/` |
| 5 | `odoo_export/export_customers.py` | live Odoo XML-RPC | `05_odoo_export/` |
| 6 | `odoo_export/customer_industry_proxy.py` | `05_odoo_export/sale_orders.csv` | `05_odoo_export/customer_industry_proxy.csv` |
| 7 | `odoo_match_customer/match_customers.py` | `03_structured_current/` + `05_odoo_export/` | `06_customer_matching/` |
| 8 | `knowledge_bank/build_knowledge_bank.py` | `03_structured_current/` + `06_` + `05_` | `07_knowledge_bank/knowledge_bank_items.csv` |
| 9 | `knowledge_bank/build_quotation_bank.py` | `07_knowledge_bank/knowledge_bank_items.csv` | `07_knowledge_bank/knowledge_bank_quotations.csv` |
| Review | `knowledge_bank/suggest_aliases.py` | `07_knowledge_bank/knowledge_bank_items.csv` | `07_knowledge_bank/alias_suggestions.csv` |
| Review | `knowledge_bank/suggest_product_families.py` | `07_knowledge_bank/knowledge_bank_items.csv` | `07_knowledge_bank/product_family_suggestions.csv` |

Stage 8 reads the human-curated `07_knowledge_bank/make_aliases.csv` and
`model_aliases.csv` (via `knowledge_bank/canonicalize.py`), which are
seeded by reviewing `alias_suggestions.csv`. It also reads
`model_family_rules.csv` / `description_family_rules.csv` (via
`knowledge_bank/product_family.py`), seeded by reviewing
`product_family_suggestions.csv` — see Data rules below.

All output paths are under `Quotation_Data/`. `quotation_parser_v1.py` is
still technically the **parser of record** for the *main* join
(`07_knowledge_bank/`) — `boq_coords/` isn't wired into it yet, and
`match_customers_coords.py` still reuses v1's own `customer` /
`quotation_number` extraction rather than duplicating it — but **as of
2026-09-25, `boq_coords/` is the priority extraction method by explicit
user direction**: default to its outputs
(`07_knowledge_bank_coords/knowledge_bank_items_coords.csv` /
`_slim.csv`) over v1's (`07_knowledge_bank/knowledge_bank_items.csv`)
for review, verification, and analysis work unless there's a specific
reason to look at v1. See `PROJECT_NOTES.md`'s "Odoo field-mapping fix,
and boq_coords now the priority extractor" entry for the full context.
`boq_coords/` has its own parallel, evaluation-only join
(`boq_coords/match_customers_coords.py` →
`knowledge_bank/build_knowledge_bank_coords.py`, writing to
`06_customer_matching_coords/` and `07_knowledge_bank_coords/`) so its
item extraction can be compared against v1's without touching v1's
output — see the Commands section below.


## Data rules — do not break these

**Confidentiality.** Never print a customer name to stdout, the terminal,
or chat. Progress and summary output is aggregate counts only. Scripts
that do handle names must have their stdout redirected to a file:

    python odoo_match_customer\match_customers.py > run_match.log 2>&1

Real quotation PDFs, hand-labelled fixtures and any Odoo export are
commercially sensitive and gitignored. Never commit them.

**No stage modifies its input files.** Every script reads its inputs and
writes to its own output folder. `odoo_export/` is strictly read-only
against Odoo — no create/write/unlink calls anywhere in it.

**Before any item-level analysis**, filter `data_source == ATTACHMENT_ITEM`.
`knowledge_bank_items.csv` also holds `ODOO_ORDER_ONLY` rows (Odoo orders
no document resolved to) whose item fields are blank by design.

**Before treating a price as quoted**, check `price_basis`. Only
`REPORTED` was actually printed on the document; `DERIVED_FROM_UNIT` and
`DERIVED_FROM_TOTAL` were computed from quantity; `NONE` has no price.

**Exclude negative prices from all price analysis.** Rows flagged
`IMPLAUSIBLE_NEGATIVE_PRICE` in `knowledge_bank_review.csv` come from the
upstream parser turning prose into a number (`"CAPACITY) QTY-2"` → `-2`),
not from the join. They are never legitimate.

**Treat `date_ambiguous == YES` dates as approximate.** Day and month were
both ≤ 12 and day-first was assumed (Indian convention). Check
`date_source` too: `ODOO_ORDER` is structured and reliable, `PARSED_DOC`
is regex-extracted from the PDF.

**Segment by confidence, don't average blind.** Around half of all parsed
items are `item_confidence == LOW`. Weight or split by it rather than
treating every row as equally trustworthy.

**Group by canonical makes/models; compare unit prices within one
`unit_class`.** Use `make_canonical` / `model_canonical`, not the raw or
`*_normalized` columns — only the canonical ones merge spelling variants
(`SIEMENS AG, GERMANY` → `SIEMENS`). A multi-make alternate is one value
(`E&H|EMERSON`). A price per `BUNDLE` (SET/LOT) is not comparable to a
price per `COUNT` (NOS). Never fuzzy-merge makes or models in code:
`OXYMAT 61` and `OXYMAT 64` are different products — new merges go
through `suggest_aliases.py` and a human-reviewed alias table.

**`product_family` is blank for most rows — that's expected, not a
defect.** Only 5.5% of items have a recognized `model_canonical` and
68.3% have any `description` at all, so coverage is inherently partial
(19.5% on the main knowledge bank with the starter tables). Group by
`product_family` for "commonly quoted modules" analysis, but don't treat
a blank value as "uncategorized junk" — it usually just means neither
rule table matched. The starter rule tables
(`Quotation_Data/07_knowledge_bank/model_family_rules.csv` /
`description_family_rules.csv`) are drafts needing domain review before
being trusted — see `PROJECT_NOTES.md` Future Work #3.

**`knowledge_bank_items_coords.csv` has `price_quality` / `arithmetic_check`;
`knowledge_bank_items.csv` (v1) does not.** boq_coords already parses
prices with a strict, anchored grammar at extraction
(`boq_coords/money.py`), so this is a cheap rollup of signals already on
the row — not a new parsing pass, and not something applied to v1's
price columns. v1's `unit_price_final`/`total_price_final` carry no
equivalent check: measured 2026-09-16, 60% of its `total_price_raw`
cells with any text aren't actually a price (quantity/spec/address text
that leaked into the price column) — bigger than the known
`IMPLAUSIBLE_NEGATIVE_PRICE` issue, deliberately left unfixed here since
fixing it means re-parsing all 3,876 documents. See `PROJECT_NOTES.md`.

**`quotation_number` is a bridge key, not a primary key.** It is ~95%
populated but not unique, and junk values (`EMAIL`, `Verbal`, `1`, `R1`)
leak in from a bare `ref` label. Use `source_path` as the unique
within-pipeline key; use `quotation_number` only to reach Odoo's
`x_studio_internal_rfq_assignment_number`, which is what
`match_customers.py` already does.

**Raw values stay beside derived ones.** When adding a normalized or
computed column, keep the raw value and record how the derived value was
obtained, so a computed number can never be mistaken for a quoted one.


## Commands

Run from the project root with the venv active:

    .\venv\Scripts\activate

    python quotation_pdf_preprocessor.py
    python preprocess_quotation_text.py
    python "remove_repeated _messagev2.py"
    python validate_quotations.py            # optional QA pass
    python quotation_parser_v1.py

    cd odoo_export
    python export_customers.py
    python customer_industry_proxy.py
    cd ..

    python odoo_match_customer\match_customers.py > run_match.log 2>&1
    python knowledge_bank\build_knowledge_bank.py > run_kb.log 2>&1
    python knowledge_bank\build_quotation_bank.py > run_qb.log 2>&1

Make/model alias review (after a knowledge-bank build; rebuild stages 8–9
once accepted rows are copied into `make_aliases.csv` / `model_aliases.csv`):

    python knowledge_bank\suggest_aliases.py > run_aliases.log 2>&1

Product family rule review (same rebuild-after-editing pattern, into
`model_family_rules.csv` / `description_family_rules.csv`):

    python knowledge_bank\suggest_product_families.py > run_product_families.log 2>&1

Tests:

    python -m unittest discover -s tests -v

Coordinate-aware extractor (independent path, writes to
`03f_structured_coords/`):

    python -m boq_coords --limit 50
    python -m boq_coords

Its parallel evaluation-only customer match + knowledge bank (writes to
`06_customer_matching_coords/` / `07_knowledge_bank_coords/`, never
touches `06_customer_matching/` or `07_knowledge_bank/`):

    python boq_coords\match_customers_coords.py > run_match_coords.log 2>&1
    python knowledge_bank\build_knowledge_bank_coords.py > run_kb_coords.log 2>&1

`build_knowledge_bank_coords.py` writes both the full audit file
(`knowledge_bank_items_coords.csv`, every column, raw kept beside
derived) and a slim, analyst-facing projection of it
(`knowledge_bank_items_coords_slim.csv`) with boq_coords-internal QA
columns and raw/derived duplicate pairs dropped — the guardrail columns
this file's own rules require (`data_source`, `price_basis`,
`date_source`/`date_ambiguous`, `item_confidence`) are kept in the slim
file too.


## Where things live

`Quotation_Data/NN_*` are the numbered pipeline stages listed above.
Stage 1's output is the separate `Quotation_Preprocessed/` tree — there is
no `01_` folder.

The **lettered** folders are not pipeline stages. The only one left is
`03f_structured_coords/`, `boq_coords/`'s output, which feeds only its
own evaluation-only `*_coords` path, never the main join. Any new
`03x_` folder is an ad-hoc experiment run: don't read it as current, and
don't wire it into the join. The old experiment runs (`03g_`–`03n_`) and
the LLM route's data (`03c_quotation_boq_text/`, `03d_extracted_boq/`)
were deleted on 2026-09-16, so `extract_boq.py` has no inputs until
those are regenerated.

`Quotation_Data/_backup_<date>/` holds snapshots taken before a stage was
re-run in place.


## Style

Match the surrounding code. The existing scripts use a module docstring
stating purpose/inputs/outputs/design, a `CONFIGURATION` constants block,
`# ====` section banners, and a `main()` that returns an exit code. Every
stage uses the stdlib `csv` module except the `knowledge_bank/` scripts,
which use **pandas** — the project's only non-stdlib data dependency.
Keep it that way unless there's a reason not to.
