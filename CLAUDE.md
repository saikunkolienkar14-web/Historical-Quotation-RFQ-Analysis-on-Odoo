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

All output paths are under `Quotation_Data/`. `quotation_parser_v1.py` is
the **parser of record**; `boq_coords/` is a second, independent
coordinate-aware extractor not yet wired into the *main* join
(`07_knowledge_bank/`). It has its own parallel, evaluation-only join
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


## Where things live

`Quotation_Data/NN_*` are the numbered pipeline stages listed above.
Stage 1's output is the separate `Quotation_Preprocessed/` tree — there is
no `01_` folder.

The **lettered** folders (`03c_`, `03d_`, `03f_`, `03g_`, `03h_`, `03j_`,
`03k_`, `03l_`) are ad-hoc experiment and evaluation runs for
`boq_coords/` and the LLM route — **not pipeline stages**. Don't read from
them as if they were current, and don't wire them into the join.

`Quotation_Data/_backup_<date>/` holds snapshots taken before a stage was
re-run in place.


## Style

Match the surrounding code. The existing scripts use a module docstring
stating purpose/inputs/outputs/design, a `CONFIGURATION` constants block,
`# ====` section banners, and a `main()` that returns an exit code. Every
stage uses the stdlib `csv` module except the `knowledge_bank/` scripts,
which use **pandas** — the project's only non-stdlib data dependency.
Keep it that way unless there's a reason not to.
