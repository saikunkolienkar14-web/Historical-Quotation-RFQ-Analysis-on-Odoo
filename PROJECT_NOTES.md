# Project Notes

Status, known issues, and planned work for the Odoo quotation extraction
pipeline. For installation and run instructions see
[`README.md`](README.md); for the schema of any output CSV see
[`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md).


## Objective

Convert quotation PDFs downloaded from Odoo into structured, analysable
data — combining parsed document content with live Odoo customer,
industry, and order facts — to support price-trend analysis, product
standardization, and later NLP/ML work.


## Current status

Structured extraction is **complete for the full corpus (3,876
documents)**, after several rounds of targeted fixes: boilerplate
leakage into item content, Indian-currency `/-` prices not being
recognized, customer-name extraction (Kind Attention / honorific / bare
label lines picked up instead of the real company name), and
quotation-number extraction (label coverage, casing/separator bugs, and
the `Ref: A-B` dash format — non-blank `quotation_number` coverage is
now 3,676 of 3,876).

The Odoo data blocker is resolved. A live, read-only XML-RPC export
exists (`odoo_export/`), customer matching has been rebuilt and run
against real data, a customer→industry proxy is computed, and matched
customers are enriched with industry, contact details, and order
history. Matching is **RFQ-number-first** — exact-matching the
quotation's own number against Odoo's
`x_studio_internal_rfq_assignment_number` — with fuzzy name-matching
retained as the fallback. `match_customers.py` has now been **re-run
against the current (v1.7.1+) `quotations.csv`** — see "Matching
figures" below for the up-to-date numbers; the stale-figures issue
called out in earlier versions of this doc is resolved.

The **item-level knowledge bank** exists
(`07_knowledge_bank/knowledge_bank_items.csv`, 40,883 rows, rebuilt on
the refreshed match above): one row per parsed line item joined to its
customer/industry/order, plus one row per Odoo order no document
resolved to, with canonical make/model values and unit classes,
provenance-tagged derived prices, and a resolved date dimension.

The **quotation-level knowledge bank** also exists
(`07_knowledge_bank/knowledge_bank_quotations.csv`, 5,075 rows,
`knowledge_bank/build_quotation_bank.py`): the item-level bank rolled up
to one row per quotation, keyed on a derived `quotation_key` rather than
the raw `quotation_number` field (see "Known issues" below for why),
carrying per-quotation item counts, summed quoted value with its basis,
and the distinct makes/models quoted.

The **analytics layer has not been started.**

**`boq_coords/` is now the priority extraction method (2026-09-25, user
direction)** — see "Odoo field-mapping fix, and boq_coords now the
priority extractor" below for what changed and why.

### Second extraction engine: `boq_coords/`

A coordinate-aware extractor (`boq_coords/`, see
[`docs/COORDS_EXTRACTOR.md`](docs/COORDS_EXTRACTOR.md)) was built
alongside v1 to fix v1's known negative-price bug class at the source:
reading each PDF's own word coordinates instead of reconstructing columns
from flattened text. It is validated against a layout-stratified,
hand-labelled golden set (`scripts/sample_golden.py` +
`tests/golden.csv`, not committed — see the README's data note) and a
corpus-wide self-consistency suite (`scripts/score.py`). Current
corpus-scale self-consistency: 0% negative prices, ~97% arithmetic
consistency on rows with all three of qty/unit price/total price, 100%
raw-text traceability. Not yet run over the full corpus or wired into the
*main* knowledge-bank join — see Future work below.

It now has its own **parallel, evaluation-only** customer-match +
knowledge-bank path (`boq_coords/match_customers_coords.py` →
`knowledge_bank/build_knowledge_bank_coords.py`, writing to
`06_customer_matching_coords/` / `07_knowledge_bank_coords/`), so its
item extraction can be scored the same way v1's is without touching v1's
output. It reuses v1's already-extracted `customer` /
`quotation_number` per document (`quotations.csv`, joined on
`source_path`) rather than re-implementing that extraction, and reuses
`odoo_match_customer.match_customers.match_quotation()` unchanged
(extracted from that script's `main()` loop so both paths share one
matching implementation). Scaled smoke-tested three times: 50 documents
(2026-09-16, 49 unique PDFs), 200 documents (2026-09-18, 198 unique,
pre-price-bug-fix), then 300 documents (2026-09-18, 298 unique, POST the
price-bug fixes below — `python -m boq_coords --limit 300` took **7.38
minutes / 442.7s (≈1.49s/doc)**, slower than the ~0.4s/doc measured
before the fixes' added per-page content classification; worth watching
if this compounds at full-corpus scale). 281 `RULED` / 17 `NO_BOQ_TABLE`.
Figures held up, scaling roughly linearly with corpus size:

| | 50-doc (pre-fix) | 200-doc (pre-fix) | 300-doc (post-fix) |
|---|---|---|---|
| Documents / items | 49 / 641 | 198 / 2,226 | 298 / 3,099 |
| All docs resolved a `quotations.csv` counterpart | yes (49/49) | yes (198/198) | yes (298/298) |
| RFQ exact / name-exact / review / low-conf / no-match / missing / ambiguous | 22/9/10/5/1/2/0 | 80/39/28/29/18/4/0 | 131/51/39/47/22/7/1 |
| Items joined to a document | 641/641 | 2,226/2,226 | 3,099/3,099 |
| `price_quality`: TRUSTED / NON_NUMERIC / FLAGGED / NO_PRICE | 136/447/58/0 | 534/1,402/290/0 | 884/1,835/380/0 |
| `arithmetic_check`: OK / MISMATCH (checkable rows) | 83/0 | 458/8 | 666/12 |
| `PRICE_OUT_OF_RANGE` (rows / docs / % of docs with a table) | — | 110 / 17 / 9.2% | 132 / 22 / 7.9% |
| `PRICE_CELL_SPANS_MULTIPLE_ROWS` (rows / docs) | — | 31 / 6 | 57 / 12 |
| `CONTINUATION_BANDS_REINFERRED` (rows / docs) | n/a (fix landed later) | n/a | 708 / 50 |
| Distinct makes/models: raw → canonical | 26→22 / 24→23 | 54→46 / 73→68 | 75→62 / 231→225 |

Confirmed at 300-doc scale, post-fix: **all 57/57**
`PRICE_CELL_SPANS_MULTIPLE_ROWS` rows have both numeric price fields
blank (matches the 200-doc post-fix result of 31/31 exactly). Self-
consistency (`scripts/score.py --self-consistency`) on the 300-doc
extraction: 0% negative price, 98.12% arithmetic-ok (of 638 checkable),
**100.00% raw-text traceability** (was 99.98%, 4,038/4,039, before a
scorer fix below). The `arithmetic_check` mismatches (8→12) and
`(source_path, item_no)` repeats (a document with multiple BOQ tables
restarts numbering per table, not a join bug — 267 pairs/574 rows at
200-doc scale) are both rare-event / structural characteristics that
scale with corpus size, not new defects.

**The one non-verbatim row was a scorer gap, not a data defect
(investigated 2026-09-18).** `scripts/score.py`'s self-consistency
verbatim check didn't know about `total_price_source` - a `"derived"`
total (quantity × unit_price, used only when the source states no total
of its own) was never itself printed in the source document, so it can
never legitimately appear verbatim, exactly the exemption
`validate.py`'s own Rule 1 already makes. The flagged row (`PTFE TUBE`,
item 1.2: `unit_price` stated as `"Rs 45,000"`, no total stated,
`total_price=4,500,000` correctly derived as `100 × 45,000`) had
`validation_error=''`/`confidence='HIGH'` all along - the pipeline itself
never mistrusted this row, only the separate scorer did. Fixed by adding
the same exemption to `score.py`'s verbatim check; 3 new tests
(`tests/test_score.py`). Not something to watch before a full-corpus
run - it was a false alarm.

`build_knowledge_bank_coords.py` also writes a slim, analyst-facing
`knowledge_bank_items_coords_slim.csv` (40 columns vs. the full file's
64) alongside the full audit file — boq_coords-internal QA columns
(`parent_item_no`, `item_level`, `price_status`, `total_price_source`,
`validation_error`) and raw/derived duplicate pairs are dropped, but
`data_source` / `price_basis` / `date_source` / `date_ambiguous` /
`item_confidence` are kept so the slim file stays safe to analyze on its
own.

**Price plausibility check, coords side only (2026-09-16).**
Re-parsing v1's `unit_price_raw`/`total_price_raw` with
`boq_coords/money.py`'s stricter, anchored grammar surfaced a real
defect much bigger than the known `IMPLAUSIBLE_NEGATIVE_PRICE` issue:
of 18,775 `total_price_raw` cells with any text, 60% (11,183) aren't
actually a price at all under the strict grammar — `'1 No.'`, a street
address, `'Ring Heater 230 VAC Length 180 MM'`, `'@30,000 PER MAN DAY'`
had leaked into the price column. Where the strict parser does find a
number it agrees with v1's own parse 97% of the time, so this is a
presence/absence defect, not a magnitude one. Per explicit decision,
**this is not being fixed or flagged in `quotation_parser_v1.py` or
`build_knowledge_bank.py`** — that would mean re-parsing all 3,876
documents, out of scope here (see Known issues / Future work). Instead,
`build_knowledge_bank_coords.py` gained `price_quality`
(`TRUSTED`/`FLAGGED`/`NON_NUMERIC`/`NO_PRICE`) and `arithmetic_check`
(`OK`/`MISMATCH`), both a pure rollup of signals boq_coords' own
extraction already captures (`money.parse_price` + `validate.py`) — no
new parsing. `FLAGGED` always matches the existing `PRICE_*`
`validation_error` codes exactly, `NO_PRICE` stayed at 0 across all
three smoke tests (boq_coords' own `PRICE_ABSENT` rule already catches
every truly-priceless row before it gets here) — see the table above.

### Odoo field-mapping fix, and boq_coords now the priority extractor (2026-09-25)

**Policy decision: `boq_coords/` is now the priority extraction method
going forward**, per explicit user direction — not just the "if it holds
up, promote it" framing Future work #2 below still carries from before
this date. New verification/analysis work should default to the
`*_coords` outputs (`07_knowledge_bank_coords/`) over
`07_knowledge_bank/` (v1) unless there's a specific reason to look at v1.
v1 is not being removed or stopped — `boq_coords` still reuses v1's
`customer`/`quotation_number` extraction (`quotations.csv`) rather than
re-implementing it (see "Second extraction engine" above) — but it is no
longer the default lens for reviewing output.

**Odoo field-mapping gap fixed.** Of the 28 Excel-field → Odoo-technical-field
pairs required for the knowledge bank, 17 were never in
`export_customers.py`'s `SALE_ORDER_FIELDS` at all (`create_date`,
`x_studio_present_status_of_quote_1`, `x_studio_price_in_inr`,
`x_studio_total_potential_estimate_1`, `x_studio_po_value_in_inr`,
`x_studio_winning_chance`, `x_studio_sbu_type_1`,
`x_studio_tentative_finalization_month`, `x_studio_finalization_year`,
`x_studio_spares_type`, `x_studio_service_type`, `x_studio_type_of_quote`,
`x_studio_average_cycle_time`, `x_studio_po_currency`,
`x_studio_latest_price_quoted`, `x_studio_main_reason_of_losing_order`,
`x_studio_reason_for_loss`), and two more (`x_studio_end_user`,
`x_studio_control_no`) were fetched into `sale_orders.csv` but silently
dropped at the `build_knowledge_bank.py` merge step. Fixed both layers,
in both the v1 and coords knowledge-bank builders:

- `odoo_export/export_customers.py` — added all 17 missing fields to
  `SALE_ORDER_FIELDS`. `x_studio_po_currency` is many2one (`res.currency`)
  — added to `MANY2ONE_SALE_ORDER_FIELDS`, splits into `_id`/`_name` like
  `partner_id` already does. `x_studio_spares_type` /
  `x_studio_reason_for_loss` are many2many (not selection, despite how
  the mapping table read) — Odoo returns these as bare id lists, so a new
  `MANY2MANY_SALE_ORDER_FIELDS` dict + `odoo_api.py`'s new
  `get_records_by_ids()` resolves them against `x_spare.type` /
  `x_lost_order_analysis` (both Studio models, no `name` field — used
  `display_name`, which Odoo guarantees on every model, instead of
  guessing at `x_name`). All other new fields are plain
  `selection`/`char`/`monetary`/`float`/`datetime` — confirmed via
  `apicheck.py sale.order` and a `fields_get(attributes=['selection'])`
  check before writing any code, not assumed. One cosmetic finding from
  that check: `x_studio_service_type`'s stored key `AMC/CAMC` displays as
  `AMC/CMC` in the Odoo UI (a typo in the schema itself) — kept the
  existing project convention of passing selection values through as-is
  (every other selection field's key already equals its label on this
  DB), so this one field shows the raw key.
- `knowledge_bank/build_knowledge_bank.py` — new shared
  `apply_order_studio_fields(row, order)` writes 21 `matched_order_*` /
  `matched_end_user_*` columns straight from `sale_orders.csv`, called
  from both `build_attachment_row()` and `build_order_only_row()`.
  Deliberately reads the order dict directly rather than routing through
  `customer_enriched.csv`, so `odoo_match_customer/match_customers.py`'s
  own matching logic needed no changes.
- `knowledge_bank/build_knowledge_bank_coords.py` — same 21 columns,
  same helper reused by import (`from build_knowledge_bank import
  apply_order_studio_fields`) rather than duplicated, called from
  `build_coords_item_row()`.

**Two unrelated bugs found and fixed while verifying this.** Neither is
part of the field-mapping change; both were blocking a correct rebuild:

1. **Duplicate, stale `.env`.** `odoo_export/.env` (a second file inside
   that folder, undocumented) silently took priority over the
   project-root `.env` the user had updated, because `python-dotenv`'s
   `load_dotenv()` finds the nearest `.env` walking up from the script's
   own working directory. It held a different DB name/password than the
   root one, breaking XML-RPC auth with a confusing 404 on an unrelated
   redirected hostname. Synced to match root `.env`. **Both `.env` files
   need to be kept in sync by hand going forward** — this is a landmine
   for the next session that updates Odoo credentials and only edits the
   root file.
2. **Stale `06_customer_matching`/`06_customer_matching_coords` vs. a
   regenerated `03_structured_current`.** Rebuilding the v1 knowledge
   bank initially produced 0% document matches (`Items with no document:
   44017`, all of them) — `quotation_items.csv`'s `source_path` had
   drifted to a different folder-path convention
   (`Quotation_Preprocessed\text\...`) than the `customer_enriched.csv`
   it was being joined against (`Quotation_Data\03_preprocessed_text_2\...`),
   because `03_structured_current` had been regenerated since
   `match_customers.py` was last run. Not caused by this fix, not new —
   found by diffing `source_path` sets between the two files. Fixed by
   re-running `odoo_match_customer/match_customers.py` (and, for the
   coords side, `boq_coords/match_customers_coords.py`), both ordinary,
   documented, read-only-against-their-inputs pipeline steps.

**Verified end to end**, backing up every folder before overwriting in
place (`Quotation_Data/_backup_2026-09-25_odoo_field_mapping_fix/`):

- v1: full corpus rebuild (4,883 orders / 4,538 documents / 44,017 items)
  — all 21 new columns populated, 0 items unmatched to a document, all
  previously-merged columns unchanged in content.
- coords: full corpus rebuild (4,883 orders / 3,586 documents / 21,067
  items) — same 21 columns added and verified populated, 0 items
  unmatched.
- A targeted 46-document / 717-item re-merge of the
  `03f_structured_coords_smoke50_sectionfix_2026-09-24` extraction
  (newer than the default `03f_structured_coords/`, not yet folded back
  into the full coords run — see below) confirmed the fix on the exact
  batch the user had already hand-reviewed, without re-running
  extraction or repointing `build_knowledge_bank_coords.py`'s hardcoded
  input path (per this doc's own rule that ad-hoc `03x_` folders aren't
  wired into the join): 709/717 rows matched a customer, 390/717 matched
  a specific order, and the new fields populate exactly where an order is
  matched (390/717 for currency/status/RFQ-reference/SBU-type/type-of-quote,
  0/717 for `matched_order_spares_type` — genuinely empty on those 46
  orders in Odoo, not a bug).

**Open item, not yet acted on**: the default `03f_structured_coords/`
(full-corpus coords output, 3,586 docs) predates the `sectionfix`
extraction-logic experiment (46/50-doc sample, newer) — the layout-shape
and other fixes tested there haven't been re-run at full-corpus scale
yet. Given boq_coords is now the priority extractor, folding that forward
and re-running the full coords corpus is the natural next step (separate
from anything in this entry) — see Future work #2.


### Completed

- Odoo quotation PDF extraction (PyMuPDF + Tesseract OCR)
- Text cleaning and normalization
- Repeated company boilerplate removal
- Text validation / profiling
- Structured BOQ and quotation-field extraction (regex/heuristic, local)
- Live, read-only Odoo export (sale orders + partners)
- Odoo customer matching — exact RFQ-number primary, fuzzy name fallback
- Customer → industry proxy
- Customer-level enrichment + `customer_knowledge_bank.csv`
- Item-level knowledge bank with normalization, derived prices, and a
  resolved date dimension
- Quotation-level rollup (`build_quotation_bank.py`) with a derived,
  collision-free `quotation_key`
- Reconcile & normalize (requirement 5): canonical make/model values and
  unit classes (`knowledge_bank/canonicalize.py`), plus a reviewed
  alias-table workflow (`suggest_aliases.py`) — see Design notes
- Product family classification (`knowledge_bank/product_family.py`,
  starter rule tables — needs domain review, see Future work)


## Pipeline scripts

| Script | Input | Output | Purpose |
|---|---|---|---|
| `quotation_pdf_preprocessor.py` | `Downloads/` | `Quotation_Preprocessed/text/` | PDF → text (direct or OCR) |
| `preprocess_quotation_text.py` | `Quotation_Preprocessed/text/` | `Quotation_Data/02_clean_text/` | text cleaning/normalization |
| `remove_repeated _messagev2.py` | `02_clean_text/` | `03_preprocessed_text_2/` | strip repeated company boilerplate |
| `validate_quotations.py` | `02_clean_text/` | `04_validation/` | profiling/QA report (read-only) |
| `quotation_parser_v1.py` | `03_preprocessed_text_2/` | `03_structured_current/` | structured BOQ/quotation extraction |
| `odoo_export/export_customers.py` | live Odoo API | `05_odoo_export/` | pulls `sale.order` + referenced `res.partner` records |
| `odoo_export/customer_industry_proxy.py` | `05_odoo_export/sale_orders.csv` | `05_odoo_export/customer_industry_proxy.csv` | mode industry per customer |
| `odoo_match_customer/match_customers.py` | `quotations.csv` + `res_partners.csv` + `sale_orders.csv` + `customer_industry_proxy.csv` | `06_customer_matching/` | customer matching (exact RFQ-number first, fuzzy name fallback) + enrichment |
| `knowledge_bank/build_knowledge_bank.py` | `quotation_items.csv` + `customer_enriched.csv` + `sale_orders.csv` | `07_knowledge_bank/` | item-level knowledge bank |
| `knowledge_bank/build_quotation_bank.py` | `knowledge_bank_items.csv` + `knowledge_bank_review.csv` | `07_knowledge_bank/knowledge_bank_quotations.csv` | quotation-level rollup |

Every stage uses the stdlib `csv` module except
`knowledge_bank/build_knowledge_bank.py`, which uses **pandas** — the
project's only non-stdlib data dependency.

### Design notes

**Why `quotation_parser_v1.py` and not v2.** A `quotation_parser_v2.py`
variant was tried — it added a wider BOQ-header detection window and
PRODUCT/MAKE/MODEL/VERSION extraction — but measured worse overall and
was removed. Its safe, tested addition (labeled-field extraction) was
ported into v1 directly. **v1 is the parser of record.**

**Why boilerplate removal isn't one fixed string.** The company's
letterhead appears in many layouts across the corpus (different offices,
different PDF export quirks), so `remove_repeated _messagev2.py` carries
~20 configured variants plus tolerant matching for hyphen vs. en-dash,
OCR digit/letter misreads in the CIN number, dashes glued to neighbouring
text, and scrambled/fragmented layouts.

**Why industry is a proxy.** `x_studio_type_of_industry` lives on
`sale.order` (per order), not on `res.partner` (per customer), and one
customer can have orders across different industries. Each customer is
assigned their most common industry across all their orders, ties broken
by the most recent order date.

**Why makes/models are canonicalized by rules + reviewed aliases, not
fuzzy matching.** Requirement 5's merge half was already the
knowledge-bank join; the gap was consolidation. Plain fuzzy scoring
can't tell `OXYMAT 61` from `OXYMAT 64` (near-identical strings,
different products), so `canonicalize.py` applies only deterministic
rules (split alternates, strip country/legal-form words,
separator-insensitive grouping) plus a human-curated alias table.
Fuzzy scoring lives in `suggest_aliases.py`, which only proposes rows
for review — the same review → override loop as customer matching. Raw,
cosmetic (`*_normalized`) and canonical values sit side by side with a
`*_canonical_basis` column. Units were already canonical (9 values);
`unit_class` was added so unit prices are only compared within
COUNT/BUNDLE/LENGTH/etc.

**"Others" industry detail.** `x_studio_type_of_industry` has an
"Others" option backed by a companion free-text field,
`x_studio_specify_others`, only populated on those orders. It's carried
through as its own column everywhere the industry field already flows —
`sale_orders.csv` → `customer_industry_proxy.csv`
(`industry_specify_others`) → `customer_enriched.csv` /
`customer_enriched_coords.csv` (`matched_industry_specify_others`,
`matched_order_industry_specify_others`) → both knowledge banks —
without altering the industry value itself (still literally "Others").
662 of 4,721 sale orders (2026-09-16 export) have a non-blank value.

**Live Odoo source switched to `employee-copy.odoo.com`.** Previously
`test-sai.odoo.com` (4,580 orders / 1,163 partners); the new export has
4,721 orders / 1,175 partners — a superset, consistent with its name
(a copy environment with ~141 more orders). Matching figures below are
scaled up proportionally but otherwise consistent (RFQ matches still
2,384). `odoo_export/.env` holds the URL/credentials actually used by
`export_customers.py` (loaded relative to that script's own folder, NOT
the project-root `.env`, which python-dotenv's `load_dotenv()` never
reaches here) — keep both in sync if the source changes again.


## Known issues

- **Negative prices from upstream prose-digit-stripping.** 894 rows have
  a negative `unit_price_final` and 1,777 a negative `total_price_final`,
  all traced to `quotation_parser_v1.py`'s `parse_number()` turning
  descriptive text into a number (`"CAPACITY) QTY-2"` → `-2`). Same class
  of bug as v1.3.2, which was attempted and reverted. Flagged as
  `IMPLAUSIBLE_NEGATIVE_PRICE` in `knowledge_bank_review.csv` — **exclude
  these rows from price analysis until fixed at source.** `boq_coords/`
  (see above) fixes this class of bug at the extraction algorithm level
  and measures 0% negative prices corpus-wide — see
  [`docs/COORDS_EXTRACTOR.md`](docs/COORDS_EXTRACTOR.md#known-limitations)
  for what it does *not* yet catch.

- **v1's price columns hide a bigger, unflagged version of the same
  defect class.** Re-parsing `total_price_raw` with `boq_coords/money.py`'s
  strict grammar (2026-09-16) shows 60% of cells with any text aren't a
  price at all (quantity/spec/address text in the price column) — not
  just negative, just wrong. Not currently flagged anywhere in
  `knowledge_bank_review.csv`, and deliberately left that way per
  explicit decision not to touch `quotation_parser_v1.py` or
  `build_knowledge_bank.py` for this — see the boq_coords section above.
  `knowledge_bank_items_coords.csv`'s new `price_quality` column has no
  v1 equivalent for this reason.

- **Matching figures (current, post-refresh, employee-copy.odoo.com
  export).** RFQ matching resolves 2,384 of 3,876 quotations (61.5%)
  unambiguously — unchanged from the `test-sai.odoo.com` figures below,
  since the ~141 extra orders in this copy environment didn't touch any
  RFQ number already in use. 4 remain flagged `RFQ_AMBIGUOUS` (same
  number on different customers' orders — deliberately left unassigned).
  The remaining ~1,488 fell through to fuzzy matching: EXACT 398, HIGH 9,
  REVIEW 368, LOW 332, NO_MATCH 222, MISSING 159. `RFQ_MATCH` rows can be
  trusted as identity; REVIEW/LOW/NO_MATCH rows cannot, without a human
  look. Knock-on effect in the item-level bank: `ODOO_ORDER_ONLY` rows
  now 2,396 (was 2,255 against `test-sai`), orders linked to a document
  unchanged at 2,325, total knowledge-bank rows 40,883 (was 40,742).

- **Parser edge cases remain.** See `parser_review.csv` after any run: a
  meaningful share of BOQ line items still have no detected price or only
  one price value, and some documents have no detectable BOQ table
  header. Two rounds of targeted fixes measurably improved this — see
  `CHANGELOG.md` v1.3.0/v1.3.1.

- **Make/model consolidation needs its alias tables reviewed.** Rules
  alone (no alias table) take distinct makes from 290 to 229 and models
  from 343 to 306 — e.g. SIEMENS spellings (`SIEMENS AG, GERMANY`, `SIEMENS AG
  GERMANY`, `SIEMENS, GERMANY`, …) now share `make_canonical = SIEMENS`
  on 1,508 rows. The remainder needs judgement:
  `suggest_aliases.py` currently proposes 29 make and 12 model aliases
  (e.g. `MICHELL INSTRUMENTS → MICHELL`, `MAXUM ED II → MAXUM EDITION
  II`) plus 39 truncated values (`VALMET(SIEMENS`) that need a manual
  call. None are applied until copied into `make_aliases.csv` /
  `model_aliases.csv`. Some make values are prose that leaked from the
  description column (`EQV INTEL I3 PROCESSOR 4 GB RAM…`) — a parser
  issue, not something aliasing should paper over.

- **Dates need care in time-series work.** 9,931 knowledge-bank rows are
  flagged `date_ambiguous` (day and month both ≤ 12, day-first assumed),
  and 1,721 have no resolvable date at all.

- **Contact details are as complete as Odoo's own data.**
  `matched_email` / `matched_phone` / `matched_city` are blank for many
  matched customers because the underlying `res.partner` record has no
  value there — confirmed, not a join bug.

- **Industry coverage.** 1,033 of the 1,163 Odoo customers have at least
  one order to derive a proxy from; only 3 of those had no industry value
  on any order. Of the 3,756 rows matched to an Odoo customer, 3,606 have
  a non-blank `matched_industry`.

- **`quotation_number` is not safe as a join key.** Measured against
  `quotations.csv`: ~95% populated but not unique (105 distinct values
  are shared across 237 documents), and roughly a fifth of non-blank
  values are junk. Root cause is `quotation_parser_v1.py`'s bare `"ref"`
  label matching lines like `"Ref: Email"` / `"Ref: Verbal"` just as
  happily as a real quotation number, plus its dash-splitting rule
  (`value.rsplit("-", 1)[-1]`, line ~2549) turning legitimate sub-quote
  numbers like `"2526W029R2-1"` / `"2526W029R2-2"` into bare `"1"` /
  `"2"`. `build_quotation_bank.py` works around this defensively with a
  derived `quotation_key` (see `docs/DATA_DICTIONARY.md`) rather than
  keying on the raw field. **Fixing this at the parser level would
  require a full re-parse of all 3,876 documents** — recommended as
  follow-up work; it would recover real quotation numbers for the ~37
  affected junk keys and the `R2-1`/`R2-2` pairs.


## Future work

1. **Analytics layer** — per-item / per-customer / per-industry price
   trends, repeated module and product-bundle detection, standardization
   opportunities. The dataset exists and its join quality is measured, so
   this is the next phase. Any analysis must respect three columns:
   `data_source` (filter to `ATTACHMENT_ITEM` for item-level work),
   `price_basis` (derived vs. reported), and `date_source` /
   `date_ambiguous`.

2. **Promote `boq_coords/` as the priority extractor (user direction,
   2026-09-25 — see "Odoo field-mapping fix" above), in place of
   `quotation_parser_v1.py`.** This is no longer conditional on "if it
   holds up" — it already resolves v1's negative-price bug class at the
   source rather than filtering it out downstream, and is now the default
   lens for reviewing extraction output (`07_knowledge_bank_coords/` over
   `07_knowledge_bank/`). Still not wired into the *main* join
   (`06_customer_matching/` / `07_knowledge_bank/` stay v1's), and v1's
   own `customer`/`quotation_number` extraction is still reused by
   `match_customers_coords.py` rather than duplicated — full promotion to
   replace v1 as the join's own input is future work, not done. Needs:
   a full-corpus self-consistency run (currently only sampled), the
   golden set's stage-2 sampling round (35 more hand-labelled documents,
   `scripts/sample_golden.py`), and a decision on the LLM-fallback step
   for validation-failing rows (`validate.py`'s six rules already flag
   which rows would need it). Smoke-tested at 50 docs (2026-09-12), then
   200 docs (2026-09-18, see the table above) — no new failure mode
   appeared at 4x scale.

   **Both previously-known price bugs are now fixed, not just flagged**
   (2026-09-18 — item-number handling was explicitly left out of scope
   for this round). Full detail in
   `docs/COORDS_EXTRACTOR.md#known-limitations`; summary:
   - **Continuation-page column shift.** `columns.bands_capture_price()`
     now gates `rows._unheaded_continuation_rows`: the parent table's
     reused bands are kept unless they demonstrably don't fit the
     continuation page's own words, and only then does
     `columns.infer_bands_from_words()` recover real bands by clustering
     words on x-gap significance and classifying each cluster by content
     (money-parsing → price; `parse_quantity`-parsing → quantity, which
     already handles an embedded unit like `"1 No."`) rather than
     assuming a fixed column count. Measured (200-doc sample,
     before → after): `PRICE_OUT_OF_RANGE` rows 110 → 103, documents
     affected 17 → 11 (9.2% → 6.0%); the new
     `CONTINUATION_BANDS_REINFERRED` flag fired on 670 rows across 45
     documents (22.7%) with real quantity/unit/price recovered, not just
     fewer flags; total extracted rows rose 2,226 → 2,374. Getting this
     gate right took two iterations — an ungated version regressed a
     previously-correct document (`Q2501N005`, no layout shift at all)
     by replacing working bands with a worse inferred split; see
     `bands_capture_price`'s docstring.
   - **Merged/rowspan price cell.** `__main__.py` now withholds the
     numeric `unit_price`/`total_price` on any row
     `ruled._spanned_price_ranges` flags (raw text stays, per "raw stays
     beside derived" below) — detection (landed 2026-09-12) is
     unchanged, but the wrong-item attribution no longer survives into
     the numeric fields. No redistribution of the spanned total across
     the group's items is attempted (would fabricate a number the
     document never wrote). Measured (200-doc sample): all 31 flagged
     rows across 6 documents now have both numeric price fields blank.
   - Self-consistency held after both fixes (200-doc sample): 0%
     negative price, 100% raw-text traceability, arithmetic-ok on
     checkable rows 98.0% (up from ~97%).
   - Regression coverage: `tests/test_price_bug_fixes.py` (13 tests,
     including real-document cases against the exact fixtures named
     above — not just synthetic logic checks).
   - **Reconfirmed at 300-doc scale (2026-09-18, post-fix):** all 57/57
     `PRICE_CELL_SPANS_MULTIPLE_ROWS` rows have both numeric price fields
     blank (matches 200-doc's 31/31 exactly); `CONTINUATION_BANDS_REINFERRED`
     fired on 708 rows across 50 documents; `PRICE_OUT_OF_RANGE` 132
     rows / 22 docs (7.9% of documents with a table, down from 9.2%
     pre-fix); self-consistency 0% negative, 98.12% arithmetic-ok, 99.98%
     raw-text traceable (one new non-verbatim row, not yet investigated).
     See the table above.

   **Remaining before a full-corpus run:** `ITEM_NO_MALFORMED` is the
   single most common flag at 200-doc scale (509 of 725 flagged rows in
   the pre-fix run) — the row's own sequential index was substituted for
   a non-clean item number (`banded.is_clean_item_no`). Not a new bug,
   just newly visible at this sample size, and explicitly out of scope
   for this round per user direction; not yet assessed for whether it
   clusters in a few documents or is spread evenly.

   **Opening-heading sub-items mis-split by `_split_self_contained_subitems`
   — confirmed real-corpus bug, unresolved (investigated 2026-09-22).**
   User-reported example: `Q24X10030`'s "GAS CHROMATOGRAPH" item has four
   unnumbered sub-rows (Sample Probe, Sample Transport Line, Sample
   Handling System, Provision for Calibration Gas Bottle), each with its
   own `qty`/price ("1 No" / "Quoted") printed on the sub-item's *heading*
   line, with descriptive bullets following below it.
   `_split_self_contained_subitems` (`boq_coords/banded.py:204`) assumes
   the opposite shape exclusively — a self-contained priced line is
   always the *closing* line of a bundled description (the case it was
   built for: `Q2501N005`'s "PORTA CABIN ... 1 SET 25,64,400", price
   printed after the description). Splitting "after" the priced line for
   an opening-heading sub-item glues the heading onto the *previous*
   sub-item and starts the next one at its bullets instead — confirmed by
   tracing actual PDF word coordinates for this document (not just the
   CSV output), silently (`confidence=HIGH`, no `validation_error`). Also
   traced: `path_taken=RULED` for this document is misleading — the
   `RULED` label only reflects that `pymupdf`'s table-finder detected
   column geometry; `ruled._ruling_line_ys()` found **zero** real drawn
   ruling lines on this page (this vendor's borders aren't vector line
   segments `get_drawings()` picks up), so row segmentation actually fell
   through to the same anchor/gap heuristic the "unruled" path uses
   (`_derive_boundaries_from_desc_gaps`).

   An ad-hoc scan for this pattern (a row's `description_full` ending in
   a short, capitalized, unpunctuated line — a heading bled onto the
   wrong row) across a 59-document sample found it in **39 documents
   (66%)**, correlated with the `RULED` path (56 of 59 sampled documents),
   confirming this is systemic, not a one-off. This is an informal
   detector for investigation only, not one of `validate.py`'s rules.

   **Fix attempted and reverted, same session.** Tried classifying each
   self-contained line as "opening" (split *before* it) vs. "closing"
   (split *after* it, the original behavior) by the word count of its own
   description text (`<=8` words → opening). Confirmed on real data: fixed
   4 of 5 `Q24X10030` sub-items (headers correctly kept with their own
   bullets) and reduced the 59-doc sample's suspect-row count from 168 to
   139. **Broke `tests/test_boq_coords_integration.py::TestBundledSubItems`**
   (the exact regression test for the PORTA CABIN/DATALOGGER/DUST MONITOR
   bug this function was built to fix): `Q2501N005`'s "TUBE FITTING 1 LOT"
   is a short, complete, standalone closing-style line item, but the
   word-count heuristic misclassified it as an opening header and merged
   a neighboring item's price into it — reproducing the exact
   multi-number price concatenation bug `_split_self_contained_subitems`
   exists to prevent. Word count and leading punctuation alone cannot
   reliably distinguish a short closing single-line item from a short
   opening heading; reverted rather than trade a confirmed regression for
   a partial fix. `boq_coords/banded.py` is unchanged from before this
   investigation.

   **Second fix attempted (font-weight signal) and also reverted, same
   session.** Directly inspected both documents' own PDF font spans
   (`page.get_text("dict")`): confirmed every heading line in both is bold
   (`Arial-BoldMT` / `Calibri-Bold`, span `flags & 16`) and every
   closing/bullet line, including `Q2501N005`'s `"TUBE FITTING 1 LOT"`, is
   regular weight — a much cleaner signal than word count. Implemented:
   added `Word.bold` (`boq_coords/geometry.py`, populated by a new
   `bold_span_boxes()`/`mark_bold()` pair cross-referencing
   `page.get_text("dict")` span bboxes against each word's centre point,
   wired into both word-construction sites, `ruled.rows_from_region` and
   `rows._unheaded_continuation_rows`), then reused the same
   opening/closing split logic from the first attempt but keyed off
   `_is_opening_style`'s bold ratio on the line's own description-band
   words instead of word count. Confirmed on real data: same `Q24X10030`
   improvement as the word-count version (4 of 5 sub-items correctly
   split), and reduced the `TestBundledSubItems` regression from 2 failing
   assertions to 1 — but did not eliminate it. Root cause of the remaining
   failure, traced directly against `Q2501N005`'s font spans on page 5: a
   feature bullet mid-description, `"WITH REMOTE CALIBRATION"`, is *also*
   bold (used for in-body emphasis, not just headings, in this vendor's
   layout) and sits close enough in y-position to `cluster_lines()` onto
   the same physical line as a price/qty pair, tripping the same
   misclassification the bold signal was meant to avoid. Also identified
   the actual structural fix that would make this whole code path
   unnecessary for this specific case: the two items in question are
   numbered `"3A"`/`"3B"` on the page (real, human-legible Sr. Nos.), but
   `_valid_item_no`'s regex is digits-only and rejects the letter suffix,
   so they never become real `item_anchors` and fall through to
   `_split_self_contained_subitems` at all — this is the same, already
   out-of-scope `ITEM_NO_MALFORMED` issue two paragraphs above, not a new
   one. Reverted `geometry.py`/`ruled.py`/`rows.py`/`banded.py` back to
   their pre-investigation state (118/118 tests pass) rather than ship a
   second confirmed regression.

   **Third fix — loosened `ITEM_NUMBER_RX` to accept a trailing letter,
   kept (2026-09-22).** `ITEM_NUMBER_RX` (`banded.py:19`) changed from
   `r"^\(?\d{1,3}(?:\.\d+)*[.)]?$"` to
   `r"^\(?\d{1,3}(?:\.\d+)*[A-Za-z]?[.)]?$"` (accepts `"1A"`, `"1.1a"`,
   ...; `_valid_item_no`'s numeric-range check already only reads the
   leading digit group, so it needed no change). **Correction to the
   `"3A"`/`"3B"` premise above**: traced further and found it was wrong —
   those tokens sit at x≈45-57, entirely to the LEFT of this document's
   own `description` band (x0=71.4; there is no `item_no` band in this
   table's column geometry at all), so they're clipped out of word
   extraction before any regex ever sees them. `_valid_item_no` was never
   the blocker for `Q2501N005`; this was a misdiagnosis, corrected here
   rather than left standing.

   The regex change is still a **real, verified fix for a different,
   confirmed case**: `Q24X10030`'s own revisions (`R1`, `R2`) print
   genuine lettered Sr. Nos for alternate options —
   `"1.1a Sample Probe (Fixed Type)"` / `"1.1b Sample Probe (Fixed
   Type)"` / `"1.2a Sample Transport Line"` / etc. (confirmed directly
   against the PDF's own word coordinates) — that the old regex silently
   couldn't recognize as anchors at all. `Q24G10074`'s `"1A"` ("Optional
   Price for Item O2 Analyser") is a second, independent real case in the
   59-doc sample. 118/118 tests still pass; a scan of every newly-matched
   letter-suffixed `item_no` across the sample (17 rows, 3 documents) found
   no false positives — every one is a real printed Sr. No.

   **But this does not fix the row-content-boundary bug two fixes above
   solved for other rows.** Measured on the 59-doc sample: rows-written
   811 → 815, informal trailing-heading-bleed count 168 → **171** (flat,
   marginally worse, not better). Root cause, confirmed directly against
   `Q24X10030R1`'s own coordinates: the anchor is now correctly recognized
   (`item_no="1.1a"` at the right y-position), but the CONTENT assigned to
   that row still starts one line late — `item_no="1.1a"`'s own
   `description` begins with `"• Temperature (Op/Design): 30 to 45°C..."`
   instead of `"Sample Probe (Fixed Type)"`, the line the anchor is
   actually printed on. This is `_derive_boundaries_from_desc_gaps`
   (`banded.py:287`) misplacing where an anchor's row starts relative to
   the description column's own line gaps — a different, still-open
   mechanism from the opening/closing self-contained-line ambiguity the
   first two attempts targeted. **Net assessment: keep the regex change**
   (it's independently correct — real Sr. Nos. should be recognized as
   real Sr. Nos. — and doesn't regress anything), but it is not, by
   itself, a fix for the user-reported `Q24X10030` symptom; the
   anchor-to-content misalignment in `_derive_boundaries_from_desc_gaps`
   is the remaining unresolved piece.

   **Full-corpus extraction run completed 2026-09-21** (3,586 documents,
   21,067 rows). Self-consistency held at full scale, matching the
   300-doc sample: 0% negative price, 99.15% arithmetic-ok (of 7,543
   checkable), 100.00% raw-text traceability. `RULED` 3,323 /
   `NO_BOQ_TABLE` 263.

   **Discovered and fixed during the full-corpus run: `Downloads/` (v1's
   input) and `Quotation PDFs/raw/` (boq_coords' input) had diverged into
   two different, only-partially-overlapping source trees** — 3,825 vs.
   3,551 PDFs, only 2,890 in common. 661 PDFs existed only in
   `Quotation PDFs/raw/`; since `match_customers_coords.py` borrows
   `customer`/`quotation_number` from v1's `quotations.csv` rather than
   re-extracting them, every one of those 661 documents (95% dated 2026)
   came back `customer_match_status=MISSING` — a document-level MISSING
   rate of 22.5% of the full corpus, well above v1's own ~4% baseline.
   Root-caused by directly diffing the two folders and confirming 0 of
   the 657 affected coords documents existed anywhere under `Downloads/`
   — a v1 re-run alone, without addressing the source divergence, would
   have fixed nothing.

   Fixed by: (1) copying the 662 raw-only PDFs into `Downloads/`
   (preserving their `Quotation PDFs/raw/<folder>/` structure; originals
   in both locations untouched, nothing deleted), taking `Downloads/`
   to 4,538 PDFs; (2) running `quotation_pdf_preprocessor.py` over the
   merged set (4,538/4,538 succeeded, 36 image-based/OCR); (3) running
   `quotation_parser_v1.py` **pointed directly at
   `Quotation_Preprocessed/text/`** rather than its usual
   `03_preprocessed_text_2/` input, since stages 2-3
   (`preprocess_quotation_text.py`, `"remove_repeated _messagev2.py"`)
   are missing from this checkout (see the note below) — customer name
   and quotation number are both extracted from only the first ~80 lines
   of a document, before where letterhead/footer boilerplate repeats, so
   skipping the cleaning stages doesn't meaningfully affect the one thing
   this catch-up run needed (`quotations.csv`'s `customer` /
   `quotation_number` columns); BOQ item quality in v1's *own* output for
   the newly-added documents may be lower than the rest of the corpus as
   a result, but that doesn't reach `boq_coords`, which extracts items
   independently. `INPUT_FOLDER` was reverted to
   `Quotation_Data/03_preprocessed_text_2` immediately after this one run.
   `quotation_parser_v1.py`'s prior output was backed up to
   `Quotation_Data/_backup_2026-09-21_03_structured_current_prev/` first.

   **`_derive_boundaries_from_desc_gaps` anchor-to-content misalignment —
   fixed (2026-09-22).** Continuation of the item above: traced directly
   against `Q24X10030R1`'s own word coordinates (`banded.py:239`). The
   function already computed the CORRECT per-anchor boundary for
   `item_no="1.1a"` (yc=420.31, the line right before "Sample Probe
   (Fixed Type)") — the bug was not in that per-anchor computation, it
   was in what happened *after*: two OTHER, unrelated anchor pairs later
   in the same region (`"1.2a"`/`"1.2b"` and `"1.3a"`/`"1.4a"`) collided
   onto the same gap-derived boundary (ordinary line spacing between
   them, no gap for the heuristic to key off), and `segment_rows`'
   collision handling (`len(boundaries) < len(set(item_anchors))`)
   reacted by discarding *every* gap-derived boundary in the region and
   replacing all of them with raw midpoints between anchors' own
   y-positions — including "1.1a"'s already-correct one. The 1.1a
   midpoint (between anchor "1" at yc=180.50 and anchor "1.1a" at
   yc=434.23) landed at yc=307.36, inside item 1's own unrelated "Process
   Conditions at sample take-off LZA001" preamble, sweeping its "•
   Temperature (Op/Design)..." bullet (yc=309.85) into row 1.1a — the
   reported symptom.

   **Fix**: resolve a collision locally, per colliding pair, instead of
   globally for the whole region. `_derive_boundaries_from_desc_gaps` now
   computes one gap-derived boundary per anchor as before, then walks
   them in y-order; only when an anchor's boundary is `<=` the previous
   anchor's already-assigned boundary does it get replaced, with the
   midpoint of just that pair's own anchor y-positions (the original
   centred-item-number rationale, preserved). Every other anchor's
   gap-derived boundary is left untouched. `segment_rows`'
   region-wide-fallback branch was removed as redundant — the function it
   called now does this itself. 118/118 tests pass (`TestBundledSubItems`
   included — that test's document has no colliding pairs at all, so it
   was never exercising this fallback path).

   Verified directly against `Q24X10030R1`: `item_no="1.1a"` now starts
   `"For Customised Technical Details of this GC refer Annex-1\nSample
   Probe (Fixed Type)\n..."` — the anchor's own heading line is now
   correctly included (previously started one line late, on the
   Temperature bullet from a different item). 4 of the 5 original
   mis-split sub-items on this document now start on their own heading
   line, up from 0. The still-imperfect two (`"1.1a"` carries one stray
   preceding annex-reference line; `"1.2b"`/`"1.4a"` each pick up one
   stray bullet line from their colliding sibling, since a raw midpoint
   between two anchor y-positions can't know where a wrapped sentence's
   own line break falls) are the same class of approximation the
   midpoint fallback always accepted for the centred-item-number case —
   but now correctly confined to the two lines immediately adjacent to
   the collision, never smearing ~100pt into an unrelated, distant
   section of the document as the global version did.

   Not yet done: a full-corpus (or large-sample) re-run of the informal
   trailing-heading-bleed scan (168 → 171 documents at the 59-doc sample,
   per the regex-fix entry above) to quantify the corpus-wide effect of
   this fix the same way the earlier attempts were measured.

   **Base (non-revision) `Q24X10030` checked against the pasted plain-text
   extraction (2026-09-22) — found two further, distinct bugs, both
   fixed.** The base document (`Q24X10030 EM Singapore Jurong.pdf`, no
   `R1`/`R2` suffix, no lettered Sr. Nos at all) hit a different code path
   than `Q24X10030R1`'s fix above, and was still badly mis-extracting:
   `item_no="2"`'s row started on `"Note- Calibration cylinder..."` (item
   1's own leftover closing note) instead of its own `"GAS
   CHROMATOGRAPH"` heading, and none of the unnumbered sub-items ("Sample
   Probe (Fixed Type)", "Sample Transport Line", "Sample Handling
   System") ever appeared as a row's own content — their qty/price tokens
   concatenated into garbage like `qty="Assuming 50m 1 No"`.

   **Bug 1 (top-level anchor boundary bleed) - fixed.** Same underlying
   weakness as the R1 fix above (an anchor's own line often has no
   detectable gap before it), but occurring in the PRIMARY per-anchor
   lookup itself, not the collision path - `_derive_boundaries_from_desc_gaps`
   picked the nearest preceding gap-candidate for item "2"'s anchor, which
   was "Note-..." (item 1's own trailing content), not item 2's own
   heading line. Distinguishing a genuinely unclaimed trailing line of the
   previous item from the OTHER known real case this gap-search protects
   (a vertically centred item number on the SECOND line of its own
   two-line, hyphen-wrapped heading - `TestSQ2509N216`, `"SERVICE CHARGES
   PER MAN-"` / `"1 DAYS [...]"`) needed a structural signal, not just
   "is there a big gap": a complete sentence ending in terminal
   punctuation (`.`, `:`, `)`, `!`, `?`) on the line immediately before an
   anchor's own co-located heading line can never be a wrapped
   continuation INTO that heading (wrapped continuations characteristically
   end the prior line unfinished, often literally mid-word with a hyphen,
   as `TestSQ2509N216`'s case does) - so it's always unclaimed content of
   the item above, safe to exclude. Added as an anchor-precision override
   inside `_derive_boundaries_from_desc_gaps` (`banded.py`), gated on this
   punctuation check so `TestSQ2509N216` is untouched (verified: its
   `"MAN-"` doesn't end in terminal punctuation, no override fires).

   Item "3"'s own anchor sits on a page where `ruled._ruling_line_ys()`
   only finds the page's own outer top/bottom border (2 points) - not
   real per-row rulings - so `segment_rows` takes the ruled-boundary
   branch (`ruling_ys and len(ruling_ys) >= 2`) and never reaches
   `_derive_boundaries_from_desc_gaps` at all for that page; the same bug
   still showed up there via a different mechanism (the whole page becomes
   one row before self-contained-line splitting ever runs). Fixed with a
   second, path-independent application of the same signal: a new final
   pass, `_reattach_misattributed_leading_lines`, walks the FINISHED row
   list (regardless of which boundary strategy produced it) and moves any
   line(s) glued in front of a row's own item-number anchor back to the
   previous row, gated on the identical terminal-punctuation check. Rows
   with no item_no anchor (the bundled-subitem case) are untouched.

   **Bug 2 (opening vs. closing self-contained-line ambiguity) - fixed,
   a new approach after two prior reverted attempts.** PROJECT_NOTES
   above documents two failed attempts at this (word-count heuristic,
   then font-weight/bold heuristic), both reverted for regressing
   `Q2501N005`'s `TestBundledSubItems` (a genuinely closing-style
   document: "TUBE FITTING 1 LOT" and similar). The new signal is
   structural instead of typographic: `_split_self_contained_subitems`
   now splits BEFORE a self-contained line (instead of the default AFTER)
   only when that line's own description is non-empty, does NOT itself
   start with a bullet character (`BULLET_CHARS`, `fields.py` - rules out
   a bulleted spec line that merely happens to carry a price mid-paragraph,
   confirmed real case: `Q2501N005`'s `"• WITH HEATED FLOW THROUGH
   CELL..."`), and IS immediately followed by a bullet-marked line (the
   elaboration bullets only make sense following a heading, never
   preceding one). Checked directly against every self-contained line
   traced in both real regression-test documents: fires only for the four
   confirmed `Q24X10030` headings and is a confirmed no-op everywhere in
   `Q2501N005` (its self-contained lines are either price-only/empty
   description, or non-bulleted text not followed by a bullet) - 118/118
   tests pass, `TestBundledSubItems` included.

   Verified directly against the base `Q24X10030`: `item_no="2"` and
   `item_no="3"` now correctly start on their own `"GAS CHROMATOGRAPH"`
   heading line (previously started on the previous item's leftover
   closing note); `"Sample Probe (Fixed Type)"` and `"Sample Handling
   System"` now correctly appear as a row's own leading content (previously
   never appeared as any row's first line at all).

   **Known remaining, narrower, separate issue - NOT fixed here.**
   `"Sample Transport Line"` still doesn't become its own split point,
   because its own quantity is phrased `"Assuming 50m"` / `"Quoted for
   50m"` (the number is on a following bullet line, not fused with the
   qty text on its own line) - `resolve_quantity_and_unit` (`money.py`)
   doesn't recognise this phrasing as a valid qty+unit, so
   `_is_self_contained_item_line` never flags that line as self-contained
   and it silently flows into whichever neighboring row claims it. This
   is a `money.py`-level quantity-parsing gap, not a row-segmentation bug
   - a distinct, narrower follow-up, out of scope for this round.

   **Cross-page row continuation - fixed (2026-09-22).** A DIFFERENT,
   previously undocumented and untested bug, found while checking whether
   the fixes above also covered multi-page items: `rows.py`'s
   `extract_document_tables` stitches pages by flatly concatenating each
   page's already-finished row list (`combined_rows.extend(...)`) with no
   merge pass across the stitch boundary itself. A row whose content
   genuinely continues from the previous page (its item cut mid-way by
   pagination) lands as the FIRST row extracted from the new page - but
   was never folded back into the item it continues, because it was never
   in the same `segment_rows()` call as that row. Confirmed real-corpus
   case, directly traced against actual page text: base `Q24X10030`'s
   `"Sample Probe (Fixed Type)"` prints its own heading AND price
   ("1 No"/"Quoted") on page 5; its own third bullet ("• with full port
   gate valve, with provision to Rod Out.") is pushed onto page 6 by
   pagination alone, landing after that page's letterhead/footer
   boilerplate. Before the fix, that bullet became the leading content of
   an unrelated orphaned row on page 6, which then also absorbed the
   following, genuinely separate "Sample Transport Line" item into itself.

   Whole-row `_merge_bundled_lots` (already used per-page inside
   `segment_rows`, and re-run once across the fully-stitched `combined_rows`
   as a coarser catch-all) could NOT catch this on its own: by the time
   the new page's own row is fully formed, it had already absorbed
   "Sample Transport Line"'s own price within that same page, so the row
   as a whole no longer looked like a bundled lot (no item_no AND no
   price) - only its LEADING line was the actual continuation. Fixed with
   a new function, `banded.peel_leading_continuation_lines`, called at
   BOTH of `rows.py`'s stitch points (headered-region stitching and
   unheaded-continuation stitching), while each page's own row boundaries
   are still known: a legitimate new row never opens directly on a bare
   bullet with no heading of its own above it, so any leading run of
   bullet-marked lines (`BULLET_CHARS`) on the first row of a newly
   stitched page can only be a continuation of the previous page's last
   row - move it there before the rest of the page's rows are appended.
   118/118 tests pass; verified directly against `Q24X10030`: "Sample
   Probe (Fixed Type)" now correctly ends with its own third bullet, and
   "Sample Transport Line" now correctly starts its own row instead of
   absorbing that stray bullet.

   Re-running `match_customers_coords.py` + `build_knowledge_bank_coords.py`
   against the refreshed `quotations.csv` (4,538 rows, up from 3,876)
   closed the gap completely:

   | | Before fix | After fix |
   |---|---|---|
   | coords docs with no `quotations.csv` match | 657 | **0** |
   | Document-level MISSING | 807 (22.5%) | **166 (4.6%)** |
   | Item-level MISSING | 4,178 (19.8%) | **983 (4.7%)** |
   | RFQ_MATCH (items) | 10,253 | **12,736** |
   | `date_source=NONE` (items) | 3,766 | **454** |

   MISSING now sits close to v1's own ~4% baseline, as expected once the
   two source trees were reconciled. **Note for the next full run of the
   main (non-coords) pipeline**: `03_structured_current/quotations.csv`
   and `quotation_items.csv` now reflect the temporary
   `Quotation_Preprocessed/text/`-input run (4,538 docs) rather than the
   documented `03_preprocessed_text_2/` path — re-running
   `odoo_match_customer/match_customers.py` → `build_knowledge_bank.py`
   would pick this up too, which is probably desirable (same corpus gap
   affects the main join) but hasn't been done as part of this round.

   **Abbreviation-period false sentence-end - fixed (2026-09-23).** A
   THIRD, distinct bug in the same terminal-punctuation anchor override
   introduced 2026-09-22 (see above): the override treats any line ending
   in `. : ) ! ?` as a finished sentence and refuses to walk the row
   boundary back past it. That's wrong when the trailing `.` closes an
   abbreviation, not a sentence. User-reported and confirmed real-corpus
   on base `Q24X10030`'s Section-I "Summary of Prices" table: every item
   heading ends `"... as per attached datasheets doc."` ("doc." = short
   for "document", sentence continues on the next physical line - "No.
   E0780601-... and technical mentioned in MR"). The override saw the
   trailing period, assumed the sentence was finished, and refused to
   reclaim that heading line - so it stayed attached to the PREVIOUS
   item's row, and every item from #2 onward started one line late,
   missing its own heading and instead ending with the NEXT item's
   heading tail. Systematic down the whole table (not an isolated
   collision) because every heading in this table ends the same way.
   (The user's own first guess - that a "Tag No." column was the cause -
   was a red herring: `BOQ_HEADER_ALIASES` has no mapping for it, so its
   values fall harmlessly into `"other"`.)

   Fixed with a shared `_ends_sentence()` helper (`banded.py`) used by
   both the boundary-derivation path and `_reattach_misattributed_leading_lines`:
   a trailing `.` is now treated as non-terminal when the word right
   before it is a short abbreviation seen in these documents
   (`NON_TERMINAL_ABBREVIATIONS` in `vocab.py`: `no, doc, dwg, drg, fig,
   ref, std, spec, rev, approx, qty, pt`) - other terminal punctuation
   (`: ) ! ?`) is untouched. Verified directly against base `Q24X10030`,
   R1 and R2: items 1-9 in the Section-I table now each keep their own
   heading and stop cleanly before the next item's, with zero regression
   on the already-fixed "GAS CHROMATOGRAPH" section or the R1/R2 numbered
   sub-item splits. 118/118 tests pass (confirms `_ends_sentence` is a
   no-op on `Q2501N005`/`TestBundledSubItems` and
   `SQ2509N216`/`TestSQ2509N216`, the two documents that broke two
   earlier fix attempts in this area). 50-doc smoke test: 0 errors.

   **Unnumbered sub-items with only a placeholder price - fixed
   (2026-09-23).** User-reported, two real-corpus symptoms on base
   `Q24X10030` traced back to the same cause. (1) "Sample Probe (Fixed
   Type)" and "Sample Transport Line" (each its own component under a
   GAS CHROMATOGRAPH item, no item-number of its own) were landing in
   ONE merged row instead of two. (2) Item 12 ("Special Tools & Tackles")
   was absorbing six unrelated, separately-priced summary rows that
   follow it in the source table (`"Amount on FCA Basis"`, `"...
   Documentation Charges..."`, `"...Inspection & Testing Charges..."`,
   `"VAT/ Taxes & Duties..."`, `"Packing, Preservation &
   Transportation..."`, `"Total on DDP Site basis"` - each with its own
   "Quoted" value in the image's Total column) into item 12's single row.

   Root cause: `_is_self_contained_item_line` (`banded.py`) required BOTH
   a valid price AND a valid quantity+unit reading on the same physical
   line before treating it as its own splittable sub-item. Direct trace
   confirmed every one of these lines DOES carry its own price - `parse_price`
   recognizes "Quoted"/"Quoted for" as the QUOTED placeholder on each
   line's own price band - but none of them has a quantity+unit match:
   the six charge rows are flat lump-sum charges with no Qty/UOM column
   at all (nothing to require), and "Sample Transport Line"'s quantity
   band literally reads "Assuming" (from "Assuming 50m", the
   already-documented `money.py` quantity-grammar gap above) so
   `resolve_quantity_and_unit` returns no value. The quantity+unit
   requirement was conservative specifically against a stray NUMBER
   landing alone in a price band (see that function's docstring) - it was
   never meant to gate a genuine PLACEHOLDER price ("Quoted"/"Included"),
   which is a much stronger, unambiguous signal that a line is its own
   priced item.

   Fixed by accepting a placeholder-only price (no numeric value, i.e.
   `parse_price(...).is_placeholder`) as sufficient on its own, via a new
   `_price_is_placeholder()` helper - a bare NUMERIC price still requires
   the quantity+unit match, unchanged. Verified directly against base
   `Q24X10030`: item 12's row now splits into "Special Tools & Tackles"
   plus its six previously-swallowed charge rows, and each GAS
   CHROMATOGRAPH tag's "Sample Probe (Fixed Type)" / "Sample Transport
   Line" / "Sample Handling System" now split into three separate rows
   (same across R1/R2). 118/118 tests pass (no regression on
   `TestBundledSubItems`/`TestSQ2509N216`). 50-doc smoke test: 0 errors,
   676 rows written (up from 638 pre-fix, as expected from correctly
   splitting more rows).

   **Leading label word lost to the "other" bucket - fixed (2026-09-23).**
   Follow-up to the fix directly above: each of the six charge rows was
   still missing its own leading label word(s) ("Total", "Documentation",
   "Inspection", "VAT/ Taxes", "Packing,", "Total on") once correctly
   separated into their own rows. Root cause: `_band_for_word`
   (`banded.py`) buckets any word that doesn't overlap a known column
   band by >40% into the catch-all `"other"` field, which the row emitter
   never reads back into `description`. Direct trace of this table's band
   geometry confirmed these label words start at x=69.5 (bold font,
   wider/left-shifted), a full ~46pt left of this table's normal
   description band start (x=115.1) - entirely or mostly outside every
   band, so they fell to `"other"` and were silently dropped.

   First attempt (unconditionally folding any word left of the
   description band's edge into description) was too broad and directly
   regressed a DIFFERENT part of the SAME table: verified by trace that
   items 1-4 have their own genuine "Tag No" column ("AT-2X1".."AT-2X4")
   sitting in that identical x-range gap, which got wrongly swept into
   the description text too ("Feed Gas Analyzer as per attached AT-2X1
   datasheets..."). Caught by re-tracing items 1-4 specifically after the
   first attempt, before running the test suite - not by the test suite
   itself (no test covers this table's Tag No column).

   Fixed narrower: a word left of the description band's edge is folded
   into description only when it reads as an ordinary label word (letters
   and light punctuation, no digits) - a tag code like "AT-2X1" always
   carries a digit, a label word never does, so this one cheap check
   separates the two real, opposite-direction cases confirmed on this
   same document. Verified directly: all six charge rows now carry their
   complete label ("Documentation Charges as mentioned in MR document",
   "Inspection & Testing Charges as mentioned in MR", "VAT/ Taxes &
   Duties (If any)", "Packing, Preservation & Transportation as mentioned
   in", "Total on DDP Site basis"), while items 1-4's Tag No data stays
   out of their description exactly as before. 118/118 tests pass. 50-doc
   smoke test: 0 errors, same 676 rows (a description-text fix, not a
   row-count one).

   **"Provision for Calibration Gas Bottle connection." absorbed into the
   PRECEDING or FOLLOWING item - fixed (2026-09-23).** User-reported via
   manual review of the Q24X10030 output, the one remaining defect after
   the fixes above: this self-contained sub-item (its own "1 No"/"Quoted"
   on its own heading line, per the source PDF's table layout) kept
   getting glued onto a neighboring item instead of forming its own row.
   Root cause, in two parts, both inside `_split_self_contained_subitems`:

   1. It's an "opening-style" heading (own price, no bullets of its own
      under it - instead a plain "Note-..." line) but its ONLY existing
      opening signal was "next line is bulleted", which doesn't fire here.
      The line before it isn't bulleted either - it's the wrapped TAIL of
      the previous sub-item's last bullet ("...shall be mounted" /
      "inside SS304 enclosure, 1.5mm thick.", no bullet prefix of its own
      on the second line). Fixed by also checking one line further back:
      lines[i-1], or lines[i-2] when lines[i-1] is a single non-bulleted
      wrap line. Deliberately NOT an unbounded backward walk - that was
      tried first and regressed Q2501N005's "TUBE FITTING 1 LOT" (a
      genuine CLOSING-style item with unrelated bullets much earlier in
      the same pending block) - only a 2-line window is trusted.
   2. Once "Provision..." correctly opens its own block, it has nothing
      of its own to close on (no bullets), so it only closes when the
      NEXT self-contained line is reached - and for tag 2 and tag 3 of
      this document, that next line is the FOLLOWING tag's own "GAS
      CHROMATOGRAPH" (or "OXYGEN ANALYZER") heading, which defaulted to
      CLOSING style and silently absorbed the whole unclosed "Provision"
      block into itself, gluing two different analyzer tags' data
      together. Fixed: a self-contained line that also carries its own
      valid item-number anchor always forces its own fresh open - but
      ONLY when it isn't already sitting at the very start of the pending
      block (`i > start`); an anchored self-contained line that's ALREADY
      first (e.g. Section-I's "Special Tools & Tackles", also anchored
      but with nothing before it needing walling off) must keep its
      default closing behavior, or it never closes at all and swallows
      whatever self-contained line comes after it instead - a regression
      caught immediately by the same manual review before being narrowed
      to `i > start`. A third variant, "OXYGEN ANALYZER" itself (an
      anchored heading with NO price of its own - the price sits on a
      LATER line, "Analyzer shelter & System integration") needed a
      separate fix: anchor-only lines (valid item_no, not themselves
      self-contained) are now folded in as forced split points too, not
      just the price-bearing self-contained ones.

   Verified directly against base `Q24X10030`, R1 and R2: all three/four
   GAS CHROMATOGRAPH/OXYGEN ANALYZER tags now each carry their own
   "Provision for Calibration Gas Bottle connection." as a separate row,
   with no cross-tag bleed and no regression on item 12's charges-section
   split (fixed earlier this session). 118/118 tests pass throughout
   (three intermediate attempts each caught and fixed a real regression
   before landing here - Q2501N005's TUBE FITTING, then Section-I's
   Special Tools & Tackles). 50-doc smoke test: 0 errors, 686 rows (up
   from 676, matching the newly-split rows).

   **`Q2501N005`-family row segmentation — three fixes implemented,
   measured, and REVERTED (2026-09-23). Superseded the same day by the
   layout-dispatch entry directly below, which landed; kept for the
   measurements.** User-reported against `Q2501N005`: 12 garbled rows,
   no item numbers at all, the first item's entire heading block missing,
   and six separate prices concatenated into one cell. All three fixes
   below were implemented in one session, verified to largely fix that
   document (24 rows, correct item numbers `1A`/`1B`/`2A`..., correct
   quantities, exactly one price per row), and then reverted in full,
   because final verification showed they regressed base `Q24X10030` —
   the document fixed and user-confirmed correct earlier the same day
   (item 2 went back to starting `"003 and technical mentioned in MR N2
   Analyzer..."`, and the GAS CHROMATOGRAPH table re-fragmented). Six
   further narrowing attempts each fixed one document and broke the
   other. `boq_coords/banded.py` and `boq_coords/vocab.py` are unchanged
   from their post-`Q24X10030` state; 118/118 tests pass.

   **Why the two documents can't share one boundary rule — the actual
   finding.** They have opposite table geometry, both real and both
   common:
   - `Q24X10030`: the item number and its price sit on the SAME line as
     the item's own heading, spec text follows below. Correct
     segmentation opens a row essentially AT the anchor line.
   - `Q2501N005`: the item number AND price are both vertically centred
     in a tall merged cell, with the item's heading several lines ABOVE
     the anchor and spec text continuing below it. Correct segmentation
     must reach backwards, far, from the anchor.

   Any single gap/anchor rule tuned for one shape mis-segments the
   other, which is why incremental heuristics keep trading one for the
   other (this is now the fourth documented revert in this area — see
   the word-count and font-weight attempts above). The next attempt
   should CLASSIFY the table's layout shape first (anchor-on-price-line
   vs. anchor-centred-in-cell — measurable from the y-offset between an
   anchor and the nearest heading/price line, and from whether anchors
   land mid-paragraph) and dispatch to a matching boundary strategy,
   rather than tuning one shared rule. It needs a labelled sample drawn
   from BOTH families before any code, not one regression test per
   family after the fact.

   Three independently-verified findings worth keeping:

   1. **Glued `SR.NO.` header token defeats `item_no` column detection —
      confirmed, large, and a two-line change.** `normalize_header_cell`
      (`vocab.py`) strips periods rather than replacing them with a
      space, so a header cell printed `"SR.NO."` (no space, very common
      in this corpus) normalizes to `"srno"`, which matches no `item_no`
      alias; `classify_header_word` then returns nothing and the table
      gets no `item_no` band at all, so no anchors, so segmentation falls
      through to the weakest boundary strategy. Measured: **707 documents
      in the scanned corpus** contain the glued-token pattern; on a
      random 80-document manifest-matched sample of them, documents with
      an `item_no` column detected went **27/80 → 74/80** with
      period→space normalization (in both `normalize_header_cell` and
      `classify_header_word`'s own alias normalization). The change
      itself is correct and small — but it CANNOT ship alone, because
      turning anchors on for 700+ documents is exactly what exposes the
      segmentation weakness in finding 2/3. Scan script kept in the
      session scratchpad (`scope_srno_scan.py`); regenerate rather than
      trust the path.

   2. **Silent whole-heading data loss in the no-anchor fallback.** When
      a region has fewer than two `item_anchors`, `segment_rows` falls
      through to `boundaries = sorted(set(money_lines_yc))` — row
      boundaries placed at the y-centres of PRICE lines. But in these
      layouts a price line is semantically the row's CLOSER, not its
      OPENER, so everything above the FIRST price line — the first item's
      complete heading, make, model and spec block — is outside every
      band and is dropped from the output entirely, with no flag and no
      `validation_error`. Confirmed by direct measurement on
      `Q2501N005`: first content line at y≈226, first derived boundary at
      y≈376; ~150pt of real item content discarded. This is a data-loss
      bug independent of the geometry question above and worth fixing on
      its own terms (at minimum, extend the first boundary up to the
      region's own content top, or flag the region).

   3. **`Q2501N005`'s pages defeat both of the other two strategies too,
      measured.** Its pages 4-10 contain exactly 2 ruling lines — the
      page's own outer border, not per-row rulings — which is enough to
      satisfy `segment_rows`' `ruling_ys and len(ruling_ys) >= 2` branch
      and make the whole page one row (same failure shape already
      documented for `Q24X10030`'s item 3 above; the `>= 2` threshold is
      the common cause and should probably become a "are these rulings
      INSIDE the region and more numerous than the region's own
      anchors/prices" test rather than a count). And its paragraph gaps
      are only ~1.35× the median line spacing, comfortably below
      `_derive_boundaries_from_desc_gaps`' `1.8 *` threshold, so the gap
      heuristic finds no paragraph starts either. Lowering that threshold
      globally was tried and is what regressed `Q24X10030` hardest.

   **Two row layouts, detected and dispatched - landed (2026-09-23).**
   Follow-up to the reverted entry above, built the way it recommended:
   classify the layout first, then segment, instead of one shared rule.
   `boq_coords/banded.py` now has `LAYOUT_TOP` (default; exactly the
   pre-existing code path, untouched) and `LAYOUT_CENTRED`:

   - `detect_layout()` runs once, on the page carrying the table's own
     header (`ruled.rows_from_region`), stored on `TableRegion.layout`;
     unheaded continuation pages inherit it (`rows._unheaded_continuation_rows`),
     since a continuation page may open mid-item. CENTRED needs all of:
     >= 2 description lines above the first anchor; most anchors carry
     their own price on or next to their line (`_anchors_carry_own_price`
     - a vendor that top-aligns the number and centres only the price
     fails this, `Q2409G010R5`); anchors don't mostly open a paragraph
     followed by body text (`_anchors_look_top_aligned` - a top-anchored
     table with the price on the heading line fails this,
     `Q25N10067R1`'s SECTION 2); and, with >= 2 anchors, the centred fit
     below has mean error <= 1.5 line pitches. The last three checks are
     re-applied per continuation page, which falls back to TOP when they
     fail (documents mix conventions).
   - `_derive_boundaries_centred()`: a small dynamic program choosing one
     block per anchor so each anchor sits nearest its block's vertical
     centre, with a capped bonus for cutting at a wider-than-usual gap and
     a cost per line left in front of the first block (without that cost
     it "cheats" with tiny blocks round each anchor). Unnumbered price
     lines more than a pitch from any anchor - and not the first price
     below an anchor that has none of its own - are block centres too
     (`Q2606H02`'s unnumbered PORTA CABIN). Border-only rulings (the page
     frame, as on `Q2501N005`'s continuation pages) are ignored in this
     path. Neither TOP repair pass (`_split_self_contained_subitems`,
     `_reattach_misattributed_leading_lines`) runs on CENTRED rows - both
     would cut the heading off.
   - Also landed: the no-anchor fallback's first boundary is clamped to the
     region top (finding 2 above - the dropped heading block), and the
     glued-`SR.NO.` header fix (finding 1), extended after a 400-document
     header-cell scan caught two side effects: aliases now match with the
     period read both as a space and as nothing (plain "SNO" had stopped
     matching), and "no of" is a `quantity` alias ("No.of Qty" had become
     a second item_no column, `SQ2407W049`).

   Verified: `Q2501N005` 22 -> 24 rows, every item `1A`..`8C.` its own row
   opening on its own heading, 1A/1B equal to the user's hand-checked
   ground truth (`tests/test_boq_coords_integration.py::TestCentredLayout`).
   `Q24X10030` base/R1/R2 byte-identical to the user-verified output,
   pinned by a new full-row snapshot test (`tests/test_boq_snapshots.py`,
   snapshot written by `scripts/snapshot_rows.py` into gitignored
   `tests/snapshots/`). 126/126 tests. Before/after (baseline = old header
   normalization + TOP everywhere), each run in its own dated
   `Quotation_Data/03f_structured_coords_layout_*_2026-09-23/` folder:
   80-doc glued-`SR.NO.` sample 536 -> 591 rows, smoke-50 686 -> 696;
   0 errors, 0% negative price, 100% arithmetic-ok on both (checkable rows
   86 -> 94 / 70 -> 78). Spot-checked changes are real items previously
   swallowed or shifted (rate-quoted heat-trace tube / service rows now
   their own LOW-confidence rows; `Q2606E06` and `Q2501C001R2` rows that
   concatenated 3-4 prices now one price each). Known remaining:
   `Q2512E16` gains one LOW bullet-fragment row - its anchor is centred
   high in its block, fits neither rule, stays TOP; exposed, not caused,
   by the header fix.

   **Missing pipeline scripts, still unresolved.**
   `preprocess_quotation_text.py` and `"remove_repeated _messagev2.py"`
   (documented stages 2-3) do not exist anywhere in this checkout, even
   though their output folders (`02_clean_text/`, `03_preprocessed_text_2/`)
   do — confirmed a second time during this round. The pipeline cannot be
   reproduced end-to-end from a fresh clone until these are restored from
   wherever they last ran, or rewritten. Not blocking day-to-day work
   (the catch-up run above worked around it for one narrow purpose), but
   flagged here so it doesn't get lost.

3. **Product-name standardization — landed 2026-09-18 as a starter,
   needs domain review.** `product_family` / `product_family_basis`
   (`knowledge_bank/product_family.py`) check `model_canonical` against
   `model_family_rules.csv` first (precise, prefix match), then
   `description` against `description_family_rules.csv` (broader
   coverage, first-match-wins phrase rules), blank on no match. This is
   a genuinely different problem from make/model consolidation — there
   was no existing family signal to build from, so the rule tables were
   authored from general product-line knowledge, not confirmed against
   anything in this repo, and **need your domain review before being
   trusted** (same caution `alias_suggestions.csv` needed). Measured on
   the main knowledge bank (38,487 `ATTACHMENT_ITEM` rows) with the
   16-rule/6-rule starter tables: **19.5% coverage** (7,510 rows: 6,523
   via description rules, 987 via model rules) — inherently partial,
   since only 5.5% of rows have a recognized `model_canonical` and
   68.3% have any `description` at all. Top families: `SAMPLING SYSTEM`
   (1,827), `SERVICES` (1,747), `GAS ANALYZER` (1,333),
   `GAS CHROMATOGRAPH` (547), `OXYGEN ANALYZER` (345). On the coords
   knowledge bank (300-doc sample, 3,099 rows): 49.6% coverage (higher
   density of analyzer/sampling-system items in that sample).
   `knowledge_bank/suggest_product_families.py` ranks still-uncategorized
   `model_canonical` values and description phrases by row count for
   extending the tables — no algorithmic way to propose a family *name*
   the way `suggest_aliases.py` could propose a make/model merge, so
   this only ranks candidates, never proposes the label itself.

4. **NLP / machine learning preparation** — not yet scoped.
