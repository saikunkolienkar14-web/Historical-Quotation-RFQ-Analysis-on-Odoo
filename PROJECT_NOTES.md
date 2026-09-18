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

2. **Validate `boq_coords/` at full-corpus scale and, if it holds up,
   promote it into the knowledge-bank join** in place of
   `quotation_parser_v1.py` — this would resolve the negative-price issue
   above at the source rather than by filtering it out downstream. Needs:
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
