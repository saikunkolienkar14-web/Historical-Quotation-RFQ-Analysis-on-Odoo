# Changelog

## Version 2.0.0 - Coordinate-Aware Extractor (`boq_coords/`)

### Problem

`quotation_parser_v1.py`'s known negative-price bug (v1.8.0/v1.9.0 above)
is structural, not a regex gap: `quotation_pdf_preprocessor.py` flattens
each page to linear text before v1 ever sees it, so the column geometry
needed to tell a price cell from a prose sentence is already gone.
`parse_number()`'s `re.sub(r"[^0-9,.\-]", "", value)` then strips
anything but digits/commas/hyphens out of *whatever text lands there*,
turning `"CAPACITY) QTY-2"` into `-2` and `"Size-6x4 with real time
data"` into `-64`. No amount of regex tuning on the flattened text can
fix this - fixing it means reading coordinates.

### Added

- `boq_coords/` - a new package, built beside v1 (v1 not modified or
  deleted; still the parser of record for the knowledge-bank pipeline).
  Reads `page.get_text("words")` (word bounding boxes) and
  `page.find_tables()` (ruled-table geometry) directly from each PDF.
  Full architecture in
  [`docs/COORDS_EXTRACTOR.md`](docs/COORDS_EXTRACTOR.md).
  - `money.py`: anchored money/quantity grammar (`^...$`, never
    `re.search` over prose, no minus sign in the grammar at all) with
    plausibility bounds v1 never applied to price fields.
  - `locate.py` / `columns.py`: BOQ region detection and header-to-column
    mapping, including a fallback for borderless (no ruling-line) priced
    tables that `find_tables()` can't see at all.
  - `banded.py`: row segmentation from ruling lines, item-number anchors,
    or money-line positions, with a structural gate - a word only stays
    in a price band if the whole line's band content parses as money or a
    recognized placeholder, otherwise it reverts to description. This is
    the actual fix for the bug class above: prose never gets the chance
    to reach a price field, independent of the number parser.
  - `validate.py`: a six-rule validator (traceability, arithmetic, price
    form, quantity form, price bounds, item_no shape) - a failing row is
    flagged `confidence=LOW` with the failed rule name(s), never dropped.
- `tests/test_money.py` (21 stdlib-`unittest` tests, no corpus needed) and
  `tests/test_boq_coords_integration.py` (real-document regression tests,
  gated to skip cleanly when the gitignored source corpus isn't present).
- `scripts/sample_golden.py`: layout-stratified sampling for building a
  hand-labelled golden test set (classifies every PDF by ruled/unruled,
  column count, and price-column layout, then samples proportionally).
- `scripts/score.py`: two independent scoring modes - `--self-consistency`
  (negative-price rate, arithmetic sanity, raw-text traceability; needs no
  golden set, runs over the whole corpus) and `--golden` (per-field
  exact/wrong/missing rate against hand-labelled rows).
- `scripts/check_fewshot.py`: asserts every literal value in a few-shot
  prompt example's output is present verbatim in that example's own
  input - guards against the exact defect fixed in `fewshot_prompt.txt`
  below (an example that invents a product/price its own input never
  states).

### Fixed

- Rebuilt `llm_fewshot/fewshot_prompt.txt`'s three broken price examples -
  each showed the model inventing a product name and price with no
  connection to its own input (one taught `45,00,000/-` in -> a
  completely different product and `9892000` out), directly contradicting
  the prompt's own "never invent values" rule.
- Header misclassification: a fixed field-priority order let item_no's
  bare `"item"` alias match before description's own more specific `"item
  description"` alias, silently rejecting real BOQ tables. Fixed with
  longest-alias-wins matching across all fields simultaneously.
- Row-boundary collision: item numbers vertically centred in a tall
  multi-line description cell (rather than top-aligned) could collapse
  two distinct items into one row. Fixed with a boundary-collision
  fallback using the midpoints between anchors.
- A duplicate-header edge case (two columns in the same real document
  both literally printed `"TOTAL PRICE (INR)"`) built two `ColumnBand`s
  under one field name, which made the row-classifier concatenate both
  columns' money text into one string, fail to parse it, and silently
  drop a real price into description. Fixed by resolving same-named
  duplicate price bands by column order.
- A bare `"Total"` header alias (meant for `total_price`) was
  misclassifying a total-*quantity* column as a price on a table with no
  `unit_price` column at all. Now only accepted as `total_price` when a
  `unit_price` column is also present in the same header window.
- `quotation_number` collapsing to a bare, non-unique manifest revision
  code (`"R0"`, shared by 9 unrelated documents in one sampled batch) -
  now always derived from the manifest's RFQ + revision-number columns
  together.
- `item_no` garbled by unsplit sub-items concatenating into one field
  (`"2 .1 .2 .3"`) - now takes only the row's own first line, with an
  output-side fallback (flagged, not silently substituted) to the row's
  sequential index for anything still malformed.
- Company letterhead/footer boilerplate bleeding into the last row's
  description on some page layouts; a `QUANTITY_MISSING_UNIT`
  false-positive on rows where quantity and unit live in two genuinely
  separate table columns; a duplicate source document (two different
  PDFs logged under the same manifest revision number) double-counting
  the same line items.
- Removed `product`/`version` from the 18-column output schema - measured
  0/592 populated across a sampled batch, since this corpus only ever
  labels fields `"Make:"`/`"Model:"` inline, never `"Product:"`/`"Version:"`.

### Verification

Golden-set (hand-labelled directly from rendered PDF pages, never copied
from any parser's own output) and self-consistency scores after the
fixes above, on a 300-document corpus sample: **0% negative prices**
(v1: 6.18% on this metric's original full-corpus baseline), **~97%
arithmetic consistency** on rows with quantity + both prices present,
**100% raw-text traceability**. Not yet run at full-corpus scale or wired
into the knowledge-bank join - see `PROJECT_NOTES.md` future work.


## Version 1.0.0 - PDF Text Extraction

### Added

- Recursive quotation PDF discovery
- Direct PDF text extraction using PyMuPDF
- Image-based PDF detection
- OCR extraction using Tesseract
- TXT output generation
- CSV metadata generation
- Page markers in extracted text
- Extraction status tracking
- Limited PDF testing


### Tested

- Single quotation PDF
- Ten quotation PDFs


### Current Status

PDF-to-text extraction is working as expected.


## Version 1.1.0 - Text Cleaning & Normalization

### Added

- `preprocess_quotation_text.py`: Unicode (NFKC) normalization, control-
  character removal, line-ending/tab normalization, conservative OCR-
  garbage stripping, whitespace collapsing, empty-page removal.
- `validate_quotations.py`: per-file profiling report (chars/words/lines/
  pages), MD5-based exact-duplicate detection, conservative OCR-quality
  warnings. Read-only QA pass, not a filter.


## Version 1.2.0 - Repeated Boilerplate Removal

### Added

- `remove_repeated _messagev2.py`: strips the company's own repeated
  letterhead/footer text from every document before structured parsing.

### Fixed (iterative, based on real leftover text reported in the corpus)

- En dash vs. hyphen mismatch causing whole footer blocks to silently
  fail to match (the config used a plain hyphen; the source PDFs used an
  en dash).
- Company-name wording variants ("Private" vs. "Pvt." vs. "Pvt" with no
  period) and several reordered/fused-line footer layouts.
- CIN number matching made fully format-generic (tolerates digit
  misreads, "PTC"/"PT C"/"PT" splitting, "CIN" misread as "GIN", ":" vs.
  ";") instead of one literal string, after discovering the same company
  has multiple valid CIN numbers (different legal entities) and several
  OCR-corrupted variants of each.
- Dashes glued directly to neighbouring text with no surrounding space,
  and an asterisk used in place of a hyphen in one badly-scanned template.
- A second legal entity's footer (a subsidiary, with its own CIN) and a
  scrambled/fragmented Mumbai-office letterhead layout.

### Rejected (measured net negative, reverted)

- A broad "next SECTION heading" stop marker - `SECTION n` and `NOTE`
  turned out to be common words in ordinary technical-writing content
  ("as per Section 4.2 of IEC...") as well as document structure, so this
  false-triggered too often. See v1.4.0 below for the fix that actually
  worked for this class of problem, applied to the structured parser
  instead of the boilerplate remover.


## Version 1.3.0 - Structured BOQ / Quotation Extraction

### Added

- `quotation_parser_v1.py`: regex/heuristic extraction (no LLM calls) of
  quotation-level fields (customer, subject, quotation number/date,
  currency, subtotal/tax/discount/grand total) and BOQ line items (item
  no., description, quantity, unit, unit price, total price), each with
  a confidence rating and full source traceability. Outputs
  `quotations.csv`, `quotation_items.csv`, `parser_review.csv`,
  `parser_summary.txt`.
- A `quotation_parser_v2.py` variant was tried alongside v1 (wider BOQ-
  header detection window, PRODUCT/MAKE/MODEL/VERSION extraction) but
  measured worse overall on real output and was removed. v1 is the
  parser of record.

### Fixed

- **Split quantity/unit misread as a new item.** A spec value like
  "150 Meters" broken across two lines by PDF extraction ("150" then
  "Meters") was being read as a new BOQ item's serial number, chopping
  the real item's row in two and misaligning every field after it
  (unit price ending up in the total-price slot, etc.). Fixed by
  excluding a bare-number line from item-start detection whenever the
  very next line is a standalone unit word.
- **PRODUCT/MAKE/MODEL/VERSION extraction** ported from the v2
  prototype: pulls labeled values ("Make: Siemens", "Model:" on its own
  line with the value following, or multiple labels on one line like
  "Make: ENVEA, Model: AF22E") out of a row before quantity/price
  detection runs, so a labeled line can't be misread as one of those.
- **"Note:" numbered lists misread as new BOQ items with no price.**
  Numbered note points ("1. All signal, power... is in customer scope.")
  were being read as new item numbers, creating spurious priceless rows.
  Two earlier fixes that stopped the whole BOQ-region scan at a "Note:"
  heading were tried and reverted after measurement showed they clipped
  real trailing price data (these notes can be embedded inside a single
  item's own content, with that item's real price still to come). The
  fix that actually worked: track the note's own sequential numbering
  (1, 2, 3, ...) and skip only those specific lines from item-start
  detection - nothing is truncated, so no price data can be lost by this
  mechanism. Verified on the full 3876-file corpus: zero items lost,
  several dozen spurious/mis-clipped items recovered or removed net.

### Full-corpus results (3876 documents, after the fixes above)

- 3876/3876 processed, 0 failures.
- BOQ table detected in 3181 documents (82%).
- 38,487 BOQ line items extracted; 6,447 HIGH / 11,947 MEDIUM / 20,093
  LOW confidence.
- Document confidence: 2,610 HIGH / 494 MEDIUM / 772 LOW.

### Known remaining gaps (as of this version - see v1.3.1 below for the
next round of fixes)

- ~36% of items have no detected price value at all.
- ~26% of items have only one price value (ambiguous unit vs. total on
  single-price-column tables).
- ~18% of documents have no detected BOQ table header at all (`v1` still
  uses the original narrow 1-3 line header-detection window; the wider,
  re-anchored version was only built for the abandoned v2).
- Being addressed incrementally as specific failing documents are
  reported, rather than with broad heuristics - broad fixes have
  repeatedly measured worse than targeted ones in this corpus.


## Version 1.3.1 - Boilerplate Leakage & Indian-Currency Price Fix

### Fixed

- **Repeated Note/Scope/bullet/SECTION-heading boilerplate leaking into
  BOQ item content.** Real documents showed the same Note/Scope text
  blocks, standalone bullet lines, and `SECTION n NOTE/SCOPE/TECHNICAL
  LITERATURE` headings repeating inside item descriptions. Fixed with a
  non-truncating, single-line-removal pass inside `parse_boq_rows()`
  only (`is_noise_line()` + `find_note_list_line_indices()`) - individual
  noise lines are dropped from an item's own content, but the BOQ-region
  scan boundary itself is never touched, avoiding the truncation
  regressions seen in earlier fixes (see v1.3.0 above).
- **Indian-currency prices like "16,00,000/-" not recognized as prices
  at all.** `parse_number()`'s digit-filter regex kept the trailing `-`
  from the "no paise" marker, producing an unparseable string. Fixed by
  stripping a trailing `/-` before the digit filter runs.

### Full-corpus re-run results (3876 documents, after these fixes)

- 3876/3876 processed, 0 failed, 3181 BOQ detected (unchanged), 38,487
  items (unchanged).
- Price fill: both unit+total price filled 14,829 (was 14,562); total-
  only 9,964 (was 9,990); neither price filled 13,694 (was 13,935);
  unit-only-mismatch 0 (was 0, unchanged).
- Net effect: measurable, non-regressive improvement in price-field
  completeness with zero item-count change.
- **Correction (see v1.3.2 below)**: these specific numbers could not be
  reproduced later in the same session, even after verifying the code
  had been restored to byte-for-byte this state (no leftover fragments
  from any later experiment). The cause wasn't identified - most likely
  either a transcription error when this entry was first written, or a
  mid-session run that appeared to fail (`PermissionError` from a locked
  output file) silently leaving a *different* run's stale output in
  place when a "before" figure was captured. Treat the numbers in this
  section as unverified; v1.3.2 below has the actual reproducible
  figures for this same code.


## Version 1.3.2 - Price-Line Detection Fixes (reverted; corrected baseline)

### Problem reported

Real documents showed several price-recognition gaps in `parse_boq_rows()`:
placeholder values with a trailing basis ("QUOTED /Manday", "@QUOTED PER
MANDAY") were silently dropped instead of recognized; standalone currency
labels ("Rs", "Rs.") on their own line leaked into item descriptions
instead of being filtered; and, most seriously, a prose sentence with an
embedded digit (e.g. "Charges for data uploading is included for 2
years.") was being digit-stripped by `parse_number()` into a bogus
numeric value that - because price assignment reads the LAST two
candidates in a row - could silently override a real `QUOTED`/`QUOTED`
pair that appeared earlier in the same item.

### Fixed

- **Placeholder suffix matching**: `PLACEHOLDER_PRICE_PATTERN` now
  matches a placeholder word ("quoted", "na", "tbd", ...) followed by a
  short pricing-basis suffix ("/Manday", "per Manday"), not just an exact
  whole-line match - but still anchored so open-ended trailing prose
  ("na - see attached document") is rejected (see regression below).
- **Standalone currency-label lines** ("Rs", "Rs.", "INR", "₹", "$", "€",
  "£" alone) added to `is_noise_line()` so they're dropped instead of
  leaking into item descriptions.
- **`parse_number()`**: now strips a leading "Rs."/"INR" currency word
  before the digit filter runs (previously left a stray "." that combined
  with the amount's own decimal point to break `float()`, e.g. "Rs.
  61,407.00" silently failed to parse at all); the trailing Indian "no
  paise" marker `/-` is now stripped wherever it's followed by
  whitespace, an opening parenthesis, or end-of-string - not only at the
  literal end of the string, since a spelled-out-amount annotation often
  follows right after it ("16,00,000/- (Rupees ... Only)").
- **Trailing prose-derived candidate trim**: after building an item's
  price candidates the normal way (unconditional `parse_number()` on
  every line, unchanged), candidates are trimmed from the END of the
  list only when that candidate's own source line reads as a sentence
  (`has_prose_words()`, 3+ real words, parenthetical text ignored). This
  targets exactly the reported override bug without touching normal
  numeric detection.

### Regressions found and reverted during this fix (two attempts)

- **Attempt 1**: gated numeric-candidate detection up front with a
  strict "must look like a clean number" shape regex before ever calling
  `parse_number()`. Measured on the full corpus: both-prices-filled
  dropped from 14,829 to 4,220 - real extracted price lines routinely
  carry PDF/OCR spacing artifacts inside the digits ("16, 00,000"), which
  the strict shape regex rejected but `parse_number()`'s own
  strip-everything-but-digits approach already tolerated fine. Reverted.
- **Attempt 2**: broadened the placeholder-word match to a free prefix
  (any trailing text allowed) and gated numeric detection with a blanket
  "reject if more than 1 real word" rule up front. Still measured badly
  (both-filled ~5,880 vs. the 14,829 baseline) for two compounding
  reasons: short words like "na"/"tbd" are common real-sentence openers
  elsewhere in a document and got wrongly captured as placeholder price
  candidates; and a blanket word-count gate on every numeric line rejects
  genuinely valid price lines that carry real words too - spelled-out
  Indian amounts ("Rs. 1,50,000/- (Rupees One Lakh Fifty Thousand
  Only)") and rate-basis suffixes ("45,000 per Manday"). Reverted in
  favor of the narrower placeholder-suffix grammar and tail-only trim
  described above.

### Verification status: reverted

11 synthetic test cases (built from the reported real-document examples,
not corpus `.txt` files) all passed, including the two regression
triggers above (spelled-out amount, per-Manday rate). However, a third
full-corpus run still measured a further regression relative to the
figure this session had been treating as the baseline, so all of the
above (`PLACEHOLDER_PRICE_PATTERN`, the `is_noise_line()` currency-label
filter, the `parse_number()` Rs./INR-prefix strip and `/-` lookahead
change, `has_prose_words()` and the trailing-candidate trim) was reverted
back out of `quotation_parser_v1.py` and `parse_boq_rows()`. None of this
version's fixes are in the codebase.

### Corrected reproducible baseline (post-revert, full 3,876-file corpus)

Re-running the reverted code turned up a second problem: its output did
not match the "14,829 both-filled" figure this session had been citing
as the v1.3.1 baseline, even though the code was verified identical (see
the correction note on v1.3.1 above). The actual, reproducible output of
this code, confirmed by re-running it, is:

- 3876/3876 processed, 0 failed, 3181 BOQ detected, 38,487 items.
- Price fill: both unit+total filled **9,417**; total-only **9,358**;
  unit-only (mismatch) **2,186**; neither **17,526**.

This is now the reference baseline for any future price-detection work -
not the earlier "14,829" figure, which could not be reproduced and
should be considered incorrect. The non-zero unit-only-mismatch count
(previously believed to always be 0) is part of this corrected baseline,
not a new regression.

### Lesson for next attempt

The three regressions in this version all stemmed from changing how
price candidates are DETECTED (shape rules, prose-word gating) without
enough real corpus diversity in mind - OCR spacing noise, spelled-out
Indian amounts, and rate-basis suffixes are all real patterns that broad
detection changes kept breaking. Any future attempt at the originally
reported bug (a trailing prose sentence overriding a real price) should
stay maximally narrow - ideally verified against a handful of real
`parser_review.csv` rows the user has looked at themselves - rather than
a rule applied to every price line in the corpus.


## Version 1.4.0 - Odoo Live Export & Customer Matching (complete)

### Added

- `odoo_export/` - a live, read-only Odoo XML-RPC connector
  (`config.py`, `odoo_api.py`, `apicheck.py`, `export_customers.py`,
  `.env`/`.env.example`), mirroring a proven reference project's file
  layout. No write/create/unlink calls exist anywhere in `OdooAPI`.
  Pulls `sale.order` (17 fields incl. `x_studio_type_of_industry`,
  region, PO value, dates) and every referenced `res.partner`, writing
  `sale_orders.csv` and `res_partners.csv`. This unblocks the
  industry-field gap noted in earlier versions - the previous Odoo
  export in this repo was Contacts-only with no industry data.
- `odoo_match_customer/match_customers.py`: rebuilt (not just patched -
  the fuzzy-matching algorithm itself was kept, the I/O layer rewritten)
  to point at the live export and the current parser output, with paths
  anchored via `Path(__file__).resolve().parent.parent` to avoid
  working-directory-dependent breakage (the same relative-path bug hit
  `odoo_export/config.py` first and was fixed there the same way).

### Real-data run results

- Odoo export: 4,580 sale orders fetched, 1,163 unique customers
  referenced and resolved.
- Customer matching: 3,876 quotations vs. 1,163 Odoo customers - EXACT
  865, HIGH 23, REVIEW 763, LOW 884, NO_MATCH 1222, MISSING (no
  customer parsed) 119.

### Fixed bugs (previously blocking, now resolved)

- `ODOO_CUSTOMERS_CSV` now points at the real `res_partners.csv` file
  (was pointing at a folder).
- `QUOTATIONS_CSV` now points at `03_structured_current/quotations.csv`
  (was pointing at a stale pre-fix output folder).
- Odoo column-name candidate lists now resolve correctly against the
  live export's real headers (`id`, `name`, `ref`) - no changes were
  actually needed to the candidate lists themselves once pointed at the
  real file; the industry column intentionally still resolves to
  nothing here, since industry lives on `sale.order`, not `res.partner`
  (see v1.5.0).

This closes out the "richer Odoo export" blocker noted throughout
earlier versions of this file.


## Version 1.5.0 - Customer -> Industry Proxy (complete)

### Added

- `odoo_export/customer_industry_proxy.py`: since
  `x_studio_type_of_industry` lives on `sale.order` (per order) and not
  on `res.partner` (per customer), and one customer can have multiple
  orders across different industries, this computes the mode (most
  common) industry per customer across all their orders, with ties
  broken by the most recently dated order. Output columns expose the
  proxy's confidence downstream (`order_count`, `industry_order_count`,
  e.g. "3 of 5 orders agree").

### Run results

- 4,580 orders read, 1,033 customers with at least one order, 0 orders
  skipped for missing `partner_id`, only 3 customers had no industry
  value on any of their orders. 1,033 proxy rows written.

This is an approximation for aggregate industry-wise analysis, not a
per-document fact - documented as such, consistent with the project
doc's "knowledge bank" approach.


## Version 1.5.1 - Customer-Name Extraction Fixes (quotation_parser_v1.py)

### Problem reported

Real documents showed the `customer` field in `quotations.csv` picking up
the wrong line in three recurring shapes, all a variant of the same root
cause: `extract_after_label()`'s lookahead blindly took the first
non-blank line following a matched label ("Customer", "To,", ...), even
when that line wasn't itself a real value.

- A "Kind Attention: <Name>" line (often with an honorific, e.g. "Kind
  Attention: Krutika Joshi Wankhede") sitting directly below the label was
  captured as the customer instead of the real company name below it.
- A bare "Customer" (or other label word) line directly below "To,"
  was captured verbatim instead of the company name below it.
- A bare "Kind Attention," line with no name attached, same issue.
- A contact person's name introduced by an honorific with no "Kind
  Attention" wording at all (e.g. "Mr. Sher Chand Kamboj"), both as its
  own line and inline on the label's own line ("Customer: Mr. Sher Chand
  Kamboj").
- A bare "Name" / "Name:" header line, same shape as the bare "Customer"
  case.

### Fixed

- Added `is_skippable_value_line()`: true for a blank line, a "Kind
  Attention"/"Attn" header (`KIND_ATTENTION_PATTERN`, with or without a
  name), an honorific-introduced contact name (`HONORIFIC_PATTERN`:
  Mr./Mrs./Ms./Miss/Dr./Shri/Smt.), a bare label word from the same
  label list being searched (e.g. "Customer"), or a bare "Name"/"Name:"
  line (`EXTRA_SKIP_LABELS`).
- Added `find_next_valid_line()`: scans forward (widened lookahead window,
  4 -> 6 lines) skipping anything `is_skippable_value_line()` flags, and
  is now used by both lookahead cases in `extract_after_label()` - the
  "label alone on its own line" case, and (newly) the "label: value on
  the same line" case, so a same-line honorific capture also chains
  forward to the real value.
- Widened the date-extraction regex from `[/-]` to `[/.-]` so
  dot-separated dates ("dated 26.03.2025") are recognized alongside the
  existing dash/slash formats ("Date: 18-02-2025").

### Verification

Unit-tested all reported patterns directly against `extract_customer()`
and `extract_quotation_date()`, plus a full 3876-file corpus re-run after
each change (0 failures throughout). Final full-corpus check: zero
remaining `customer` values starting with an honorific (Mr./Mrs./Ms./Dr./
Shri/Smt.) and zero remaining bare-"Name" `customer` values.


## Version 1.6.0 - Customer Enrichment: Industry, Contact & Order History (complete)

### Problem

`match_customers.py`'s `matched_industry` output column was always
blank: it looked for an `industry` column on `res_partners.csv`, but
that CSV never had one (industry lives on `sale_orders.csv`, per order,
surfaced via the `odoo_export/customer_industry_proxy.py` script added
in v1.5.0). The enriched output also carried no contact details or order
history, even though both were already available in the existing Odoo
export.

### Added

- `match_customers.py` now loads `customer_industry_proxy.csv`
  (`load_industry_proxy()`) and joins it on `matched_customer_id`,
  fixing the always-blank `matched_industry` bug and adding
  `matched_industry_confidence` (e.g. "7 of 8 orders").
- `match_customers.py` now loads `sale_orders.csv`
  (`load_sale_order_summary()`) and aggregates per customer:
  `total_orders`, `total_po_value`, `regions`, `latest_order_date`,
  `customer_type`, `adage_customer`, `end_user`, `quote_status_summary`
  ("most recent order wins" for single-valued fields, matching the
  tie-break convention `customer_industry_proxy.py` already uses).
- `matched_email`, `matched_phone`, `matched_city`, `matched_state`,
  `matched_country` added to the enriched output, pulled from
  `res_partners.csv` (already loaded for matching, previously not
  carried into the output).
- New output file `customer_knowledge_bank.csv`: one row per distinct
  **matched** Odoo customer (not per quotation), consolidating all of
  the above plus `quotation_count` (how many quotations matched to that
  customer) - a browsable customer directory rather than a quotation
  log.
- Both missing-file cases (`customer_industry_proxy.csv` or
  `sale_orders.csv` not present) degrade to blank fields, not a crash -
  same pattern as the existing `load_overrides()`.

### Real-data run results (after v1.5.1's customer-extraction fixes)

- 3,876 quotations vs. 1,163 Odoo customers: EXACT 974, HIGH 23, REVIEW
  986, LOW 1,042, NO_MATCH 732, MISSING (no customer parsed) 119. (Higher
  EXACT / lower MISSING than v1.4.0's numbers, consistent with the
  customer-text quality fixes in v1.5.1.)
- 3,756 rows matched an Odoo customer; 3,606 of those now have a
  non-blank `matched_industry` (was 0 before this version).
- `customer_knowledge_bank.csv`: 660 rows, one per distinct
  `matched_customer_id` - confirmed to match the distinct-id count in
  `customer_enriched.csv` exactly.
- `matched_email` / `matched_phone` / `matched_city` are blank for many
  customers - confirmed this is real sparsity in the underlying
  `res.partner` records in Odoo, not a join bug.

This closes the "richer Odoo export" / industry-blocker thread that ran
through v1.4.0 and v1.5.0, and is the customer-level portion of the
"Knowledge Bank" plan below.


## Version 1.7.0 - RFQ-Number Matching (primary), Fuzzy Matching Kept as Fallback

### Problem

Fuzzy name-matching (v1.4.0-v1.6.0) is inherently uncertain - REVIEW/LOW/
NO_MATCH rows need a human look before the matched customer identity can
be trusted. Every quotation document carries its own internal quote
number (`Our Quotation No: 2601W012`, `ADAGE QUOTE REF: Q25S10087`), and
Odoo's `sale.order` carries the same number on the technical field
`x_studio_internal_rfq_assignment_number` - an exact match on this field
gives a hard, unambiguous link to the customer and every other fact on
that specific order, with no fuzziness at all.

### Added

- `odoo_export/export_customers.py`: added
  `x_studio_internal_rfq_assignment_number` to `SALE_ORDER_FIELDS` -
  confirmed present on the live database (not in the "skipped fields"
  list) and populated on 4,329 of 4,580 sale orders (95%); only 6
  RFQ numbers collide across different customers system-wide.
- `quotation_parser_v1.py`'s `extract_quotation_number()`: rewritten to
  reuse `extract_after_label()` (the same helper `extract_customer()`
  uses) instead of its old bespoke single-line-only regex, adding
  lookahead support for the label-alone-on-its-own-line case
  (`ADAGE QUOTE REF:` followed by the number on the next line).
  `QUOTATION_NUMBER_LABELS` extended with `"our quotation no"`,
  `"our quotation no."`, `"our quotation number"`, `"our quote no"`,
  `"our quote no."`, `"adage quote ref"`, `"adage quote reference"`.
  Fixed two bugs found while verifying this: the same-line match was
  running against the lowercased/punctuation-stripped `normalized`
  string instead of the original `line`, mangling casing (
  `"2425W034R2"` came back as `"2425w034r2 dated 27.06.2024"`); and the
  label/value separator was too strict to match a literal period with
  no colon/dash ("Our Quotation No. SQ2411W076R2"), widened from
  `\s*[:\-]?\s*` to `[\s.:\-]*`. Also strips a "Dated" suffix some
  source PDFs glue directly onto the number with no space
  (`"2425W090R1Dated: 24.03.2025"`).
- `odoo_match_customer/match_customers.py`: new RFQ-number match path,
  checked **before** fuzzy matching (fuzzy matching is kept as the
  fallback, not removed - if a quotation has no extractable RFQ number,
  or its number isn't found in `sale_orders.csv`, it falls through to
  the existing fuzzy-matching logic unchanged).
  - `load_rfq_index()` reads `sale_orders.csv`, keyed on the normalized
    (uppercase, whitespace-stripped) RFQ number, so a number appearing
    on orders for two *different* customers is detected as a collision
    rather than silently resolved to one of them.
  - Exactly one customer for the number -> `RFQ_MATCH`, score `1.0`,
    `matched_customer_*` fields resolved from that order's
    `partner_id`, plus new `matched_order_*` columns (`matched_order_id`,
    `matched_rfq_number`, `matched_order_industry`,
    `matched_order_customer_type`, `matched_order_region`,
    `matched_order_po_number`, `matched_order_po_value`,
    `matched_order_quote_status`, `matched_order_date`) carrying that
    **specific order's own** fields - more precise than the existing
    customer-level industry-proxy/order-history aggregation, which is
    still joined on top via `build_enrichment_extra()` regardless of
    which method resolved the customer.
  - Two or more different customers share the number -> `RFQ_AMBIGUOUS`,
    written to `customer_review.csv` with the conflicting customer
    names, no customer assigned (not guessed).
  - No RFQ number extracted, or number not found in `sale_orders.csv` ->
    falls through to fuzzy matching exactly as before.

### Real-data run results

- RFQ numbers indexed from `sale_orders.csv`: 4,329.
- Of 3,876 quotations: **1,440 RFQ_MATCH** (37%), **4 RFQ_AMBIGUOUS**,
  2,432 fell through to fuzzy matching (EXACT 524, HIGH 11, REVIEW 578,
  LOW 683, NO_MATCH 511, MISSING 125).
- Spot-checked several `RFQ_MATCH` rows directly against
  `sale_orders.csv`: correct customer name, and `matched_order_*` fields
  consistent with that specific order's own industry/PO value/quote
  status/date.
- `customer_knowledge_bank.csv` still builds correctly (766 rows) with
  RFQ-matched and fuzzy-matched rows feeding the same
  `matched_customer_id`-keyed dedup.

These numbers predate the v1.7.1 `Ref:`/dash-split quotation-number fix
below, which raised `quotation_number` coverage further - re-running
`match_customers.py` after that fix is expected to move some of the
current fuzzy-matched/NO_MATCH/MISSING rows into RFQ_MATCH, since more
quotations now have a usable extracted number.


## Version 1.7.1 - Quotation Number: "Ref: A-B" Dash Format

### Problem reported

A recurring format wasn't handled: `Ref: Q24250574-SQ2503N052`, where
only the part after the dash (`SQ2503N052`) is the actual internal
quotation number - the first segment is an unrelated reference code.

### Fixed

- Added a bare `"ref"` label to `QUOTATION_NUMBER_LABELS` so `Ref:`
  lines are picked up at all.
- `extract_quotation_number()`: after the existing single-token trim and
  "Dated"-suffix strip, added a dash-split step - if the captured token
  contains a `-`, keep only the part after the last one. Dash-free
  formats (`2425W034R2`, `Q25S10087`, ...) are unaffected.
- **Regression caught during verification and fixed in the same pass**:
  adding the bare `"ref"` label exposed a pre-existing gap in
  `extract_after_label()` - the label-match regex had no word-boundary
  check, so `"ref"` matched as a *prefix* of unrelated words too (
  `"Reference A"` was captured as `"erence A"`). Fixed by adding `\b`
  right after the label in the match pattern. This is a general
  correctness fix, not specific to `"ref"` - it also protects any other
  short label added to any of the label lists in the future.

### Verification

Unit-tested the reported format plus the `"Reference A"` regression case
and every previously-passing label format (`Our Quotation No:`,
`ADAGE QUOTE REF:`, `REF NO:`) directly against `extract_quotation_number()`.
Full 3,876-file corpus re-run: 3,876/3,876 processed, 0 failures,
3,676 non-blank `quotation_number` values (up from 2,568 pre-v1.7.0), 0
remaining dash-containing values, 0 values containing `"erence"`, 0
overlong/space-containing garbage values.


## Version 1.8.0 - Item-Level Knowledge Bank (dataset)

### Added

- `knowledge_bank/build_knowledge_bank.py` - joins the three finished
  datasets into one flat table at **line-item grain**:
  `quotation_items.csv` → `customer_enriched.csv` (on `source_path`, the
  verified-unique key) → `sale_orders.csv` (on `matched_order_id`).
  Outputs `Quotation_Data/07_knowledge_bank/knowledge_bank_items.csv`,
  `knowledge_bank_review.csv`, and `knowledge_bank_summary.txt`.
  First script in the project to use pandas (see dependency note below).
- **Two row kinds**, separated by a `data_source` column: `ATTACHMENT_ITEM`
  (38,487 parsed line items) and `ODOO_ORDER_ONLY` (3,186 Odoo orders that
  no parsed document resolved to, item fields blank). The second kind
  exists so customer/industry coverage reflects the real business rather
  than only the subset of quotes with a parseable attachment.
- **Conservative normalization**, additive - raw values always kept:
  `make_normalized`, `model_normalized` (case/whitespace/edge-punctuation
  only), `unit_normalized` (canonical unit forms - `Nos`/`No`/`nos` →
  `NOS`, `Mtrs` → `METER`), built from the same unit vocabulary as
  `UNITS_PATTERN` in `quotation_parser_v1.py`. No fuzzy consolidation of
  makes/models yet - deliberately deferred (see findings below).
- **Derived prices**: `unit_price_final` / `total_price_final`, filled from
  the other value and `quantity` where one side is missing, with
  `price_basis` (`REPORTED` / `DERIVED_FROM_UNIT` / `DERIVED_FROM_TOTAL` /
  `NONE`) recording provenance, so a computed number is never mistaken for
  a quoted one. Lifts rows carrying a usable price from 18,775 (reported
  `total_price` alone) to **20,961**.
- **Resolved date**: `quotation_date_final` (ISO), `date_source`,
  `date_ambiguous`, `quotation_year`, `quotation_month`. Odoo's structured
  `date_order` wins where a document matched a specific order; the
  regex-extracted document date is the fallback. The document-date parser
  handles all the real corpus shapes: `.`/`-`/`/` separators, `YYYY/MM/DD`,
  text months (including the corpus typo "Augest"), and 2-digit years.
  Where one component is >12 the order is decided by that; where both
  readings are valid, day-first is assumed (Indian convention, dominant
  here) and the row is flagged `date_ambiguous = YES` rather than silently
  guessed. Values outside 2015-2027 are rejected as parse failures, which
  is what catches the malformed `24-25/1264` value.

### Deliberately omitted

- `product` is not carried through - measured **0 non-blank values in
  38,487 rows**, so it would be a permanently empty column. `version` IS
  carried (2 real values).

### Fixed during verification

- **Orders double-counted as `ODOO_ORDER_ONLY`.** The set of "orders already
  represented by a document" was first derived while iterating line items,
  but a document can match an Odoo order and still parse zero line items
  (no BOQ table detected). 434 such orders looked unlinked and were written
  a second time as Odoo-only rows. Fixed by deriving the set from the
  document table instead; linked orders went 960 → 1,394 and total rows
  42,107 → 41,673.

### Real-data run results

- **41,673 rows**: 38,487 `ATTACHMENT_ITEM` + 3,186 `ODOO_ORDER_ONLY`
  (= 4,580 orders − 1,394 linked to a document). 0 items failed to resolve
  to a document.
- Fill rates verified **identical to source** for every carried item field
  (description 26,300 / make 3,952 / model 2,135 / quantity 15,618 /
  unit 11,121 / unit_price 11,603 / total_price 18,775) - the join loses
  nothing. `matched_industry` present on 92.8% of attachment rows.
- Date source: 25,917 `PARSED_DOC`, 14,035 `ODOO_ORDER`, 1,721 `NONE`.
  All 39,952 resolved dates parse as valid ISO dates within 2022-2026.
  9,931 flagged `date_ambiguous`.
- Price basis: 15,114 `REPORTED`, 4,181 `DERIVED_FROM_TOTAL`, 1,666
  `DERIVED_FROM_UNIT`, 17,526 `NONE` - reconciles exactly against the
  source's own 9,417 both-price / 2,186 unit-only / 9,358 total-only rows.
- 29,955 rows flagged in `knowledge_bank_review.csv` (a row can carry
  several reasons): NO_PRICE 17,529, DATE_AMBIGUOUS_DAY_MONTH 9,931,
  LOW_CONFIDENCE_ITEM_WITH_PRICE 6,820, PRICE_WITHOUT_DESCRIPTION 6,820,
  NO_MATCHED_CUSTOMER 1,725, DATE_MISSING 1,699,
  IMPLAUSIBLE_NEGATIVE_PRICE 1,777, DATE_UNPARSEABLE 22.

### Findings surfaced by this dataset (for the next phase, not fixed here)

- **Negative prices from upstream prose-digit-stripping.** 894 rows have a
  negative `unit_price_final`, 1,777 a negative `total_price_final` - all
  traced to `quotation_parser_v1.py`'s `parse_number()` digit-stripping
  descriptive text into a number (`"CAPACITY) QTY-2"` → `-2`,
  `"Size-6x4 with real time data"` → `-64`). This is the same class of bug
  v1.3.2 attempted and reverted. Flagged as `IMPLAUSIBLE_NEGATIVE_PRICE` so
  these rows can be excluded from price analysis until fixed at source.
- **Make consolidation is genuinely needed**: `SIEMENS AG, GERMANY` (887
  rows) and `SIEMENS` (490) are the same manufacturer but stay separate
  under conservative normalization - exactly the duplicate cluster the
  deferred fuzzy-consolidation step will need to handle, now visible with
  real counts to justify the threshold choice.
- 52% of line items remain `LOW` item confidence; analytics should segment
  or weight by `item_confidence` rather than treating all rows equally.

### Dependency change

pandas (3.0.5) and its dependencies were installed into the project venv -
the first non-stdlib data dependency in the pipeline. A `requirements.txt`
now records the project's Python dependencies, which previously existed
nowhere.


## Version 1.9.0 - Item / Quantity / Unit Validation

### Problem

The knowledge bank (v1.8.0) made field-level quality visible for the first
time, and three columns in `quotation_items.csv` carried obvious garbage:

- **`item_no`**: 6,604 rows above 100, 4,953 above 1,000 - PO/material codes
  in the item-number slot (`9021200461` repeated across rows,
  `698403961131124`). Plus dates captured as item numbers (`02.07.2025`),
  which `is_item_number()`'s `\d+(?:\.\d+)*[.)]?` pattern matches happily.
- **`quantity`**: 5,058 rows above 100, the worst being values like
  `2150000` on a row whose description reads `"Mete Rs. 650 Per Mtr"`.
- **`unit`**: 50 distinct spellings of about nine real units.

### Added - validation at row-output time

All three rules run inside `parse_boq_rows()` when the row is built,
**deliberately not inside `find_item_start_positions()`**: changing row
segmentation is the class of broad change that measured worse and was
reverted twice before (see the `STOP_SECTION_MARKERS` comment and v1.3.2).
Validating the emitted value keeps the row and everything else on it.

- `validate_item_number()` + `MAX_ITEM_NUMBER = 100`: rejects date-shaped
  values (`DATE_SHAPED_PATTERN`) and any leading integer above 100. Real
  forms (`1`, `1.`, `2)`, `1.1`, `01`) and non-numeric ones (`A1`) pass
  through untouched.
- `validate_quantity()` + `MAX_UNITLESS_QUANTITY = 100`: **unit-aware, not a
  flat cap**. A quantity carrying a recognized unit is kept at any
  magnitude - a flat cap would have destroyed 587 legitimate rows such as
  `1600 Meters` of power cable (`unit_price` 650, total 1,040,000). Only a
  large *unit-less* number is rejected. A rejected quantity is **blanked,
  never moved into a price field** - these values are material codes, not
  prices, and `unit_price` is frequently already populated on those rows.
- `canonicalize_unit()` + `UNIT_CANONICAL`: folds the 50 observed spellings
  to `NOS`, `SET`, `LOT`, `METER`, `DAY`, `EACH`, `PCS`, `KG`, `MM`.
  Unrecognized units are blanked and flagged rather than passed through.
- New warning codes flowing into `parser_review.csv` through the existing
  machinery: `ITEM_<n>_INVALID_ITEM_NUMBER` (7,328),
  `ITEM_<n>_QUANTITY_OUT_OF_RANGE` (4,471), `ITEM_<n>_UNKNOWN_UNIT` (22).

### Full-corpus results (3,876 documents)

- 3,876/3,876 processed, 0 failures. **Item rows unchanged at 38,487** -
  this pass blanks fields, it never drops rows.
- `item_no`: 0 remaining values above 100, 0 date-shaped. Non-blank
  31,159 (7,328 blanked); their descriptions and prices survive intact.
- `quantity`: non-blank 15,618 → 11,147 (4,471 blanked). **All 587 rows with
  a quantity above 100 AND a real unit preserved**, verified explicitly.
- `unit`: 50 distinct → 9 canonical; 11,121 → 11,099 non-blank (only 22
  dropped as unrecognized).
- **Prices completely untouched**, as intended: `unit_price` 11,603 and
  `total_price` 18,775 both byte-identical to the previous run.

### Knowledge bank rebuilt on the cleaned data

Re-ran `knowledge_bank/build_knowledge_bank.py`: 41,673 rows (38,487
`ATTACHMENT_ITEM` + 3,186 `ODOO_ORDER_ONLY`), every fill rate matching the
new source exactly.

One meaningful improvement fell out of the quantity fix: `price_basis`
`DERIVED_FROM_TOTAL` dropped from 4,181 to **997**, and `REPORTED` rose from
15,114 to **18,298**. Those 3,184 rows were previously deriving a unit price
by dividing a real total by a *material code* masquerading as a quantity -
i.e. producing meaningless unit prices. Total rows carrying any price is
unchanged at 20,961, so nothing was lost; the derived figures are simply no
longer fabricated from bad divisors.

### Known limitation (not fixed here)

Some garbage survives *below* the thresholds because its cause is BOQ-region
detection rather than the value itself - e.g. `item_no=50` whose description
is the table header `"SI No DESCRIPTION QTY UNIT PRICE"`. That belongs to the
deferred `description` pass.


## Knowledge Bank Build - Status

Per the approved plan (`Build the Knowledge Bank`):

- Phase 1 (customer matching, v1.4.0) - complete.
- Phase 2 (industry proxy, v1.5.0) - complete.
- Phase 3, customer-level (industry/contact/order-history enrichment
  joined onto matched customers, v1.6.0 above) - complete.
- Phase 3, item-level (the full join into one flat table, one row per
  historical line item, v1.8.0 above) - **complete**.
- Phase 4 (product/make/unit normalization) - **partially complete**:
  conservative unit/make/model normalization ships in v1.8.0; fuzzy
  consolidation of equivalent makes/models (e.g. `SIEMENS AG, GERMANY` vs
  `SIEMENS`) is deliberately deferred, now with real duplicate clusters
  visible in `knowledge_bank_items.csv` to calibrate against.
- Phase 5 (analytics/report layer - price trends per item/customer/
  industry, repeated modules and bundles, standardization opportunities) -
  not started. This is the next phase; the dataset it needs now exists.