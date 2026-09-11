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
retained as the fallback.

The **item-level knowledge bank** exists
(`07_knowledge_bank/knowledge_bank_items.csv`, 41,673 rows): one row per
parsed line item joined to its customer/industry/order, plus one row per
Odoo order no document resolved to, with conservative make/model/unit
normalization, provenance-tagged derived prices, and a resolved date
dimension.

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
knowledge-bank join — see Future work below.

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

- **Matching figures are stale.** RFQ matching resolved 1,440 of 3,876
  quotations (37%) unambiguously, with 4 flagged `RFQ_AMBIGUOUS` (the
  same number on different customers' orders — deliberately left
  unassigned). The remaining 2,432 fell through to fuzzy matching: EXACT
  524, HIGH 11, REVIEW 578, LOW 683, NO_MATCH 511, MISSING 125. These
  figures **predate the v1.7.1 quotation-number fix**, which raised
  non-blank coverage from 2,568 to 3,676; `match_customers.py` has not
  been re-run since, so RFQ-match coverage should be materially higher on
  the next run. `RFQ_MATCH` rows can be trusted as identity;
  REVIEW/LOW/NO_MATCH rows cannot, without a human look.

- **Parser edge cases remain.** See `parser_review.csv` after any run: a
  meaningful share of BOQ line items still have no detected price or only
  one price value, and some documents have no detectable BOQ table
  header. Two rounds of targeted fixes measurably improved this — see
  `CHANGELOG.md` v1.3.0/v1.3.1.

- **Equivalent makes are not consolidated.** `SIEMENS AG, GERMANY` (887
  rows) and `SIEMENS` (490) remain separate under the deliberately
  conservative normalization in v1.8.0.

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
   which rows would need it).

3. **Fuzzy make/model consolidation** — deferred deliberately; real
   duplicate clusters are now visible in the knowledge bank to calibrate
   a threshold against.

4. **NLP / machine learning preparation** — not yet scoped.
