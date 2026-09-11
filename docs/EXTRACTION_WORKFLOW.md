# Quotation PDF Extraction Workflow

## 1. Data Source

The source documents are quotation PDFs downloaded from Odoo.

The PDFs are stored in quotation/RFQ-specific folders.


## 2. PDF Discovery

The extraction script recursively searches the Downloads directory.

Example:

Downloads/
    ├── Folder A/
    │   ├── file1.pdf
    │   └── file2.pdf
    │
    └── Folder B/
        └── file3.pdf


## 3. PDF Classification

Each PDF is initially processed using direct text extraction.

If sufficient text is detected:

    PDF → TEXT_BASED


If insufficient text is detected:

    PDF → IMAGE_BASED


## 4. Text-Based PDF

PyMuPDF extracts the embedded digital text.

Advantages:

- Fast
- No OCR errors
- Preserves digital text
- Suitable for machine-readable PDFs


## 5. Image-Based PDF

The PDF page is rendered as an image.

Tesseract OCR processes the image.

Pipeline:

PDF
↓
Page rendering
↓
Image
↓
Tesseract
↓
Text


## 6. Text Output

Each PDF generates a corresponding TXT file.

The original folder structure is preserved.


## 7. Metadata

A CSV file records:

- Original PDF
- Output TXT
- PDF classification
- Extraction method
- Number of pages
- Number of extracted characters
- Processing status


## 8. Validation

Before processing the entire dataset:

1. Process a small sample.
2. Check text-based PDFs.
3. Check OCR PDFs.
4. Verify filenames.
5. Check page counts.
6. Inspect extracted text.
7. Confirm no original PDFs were modified.


## 9. Handoff to Preprocessing

The output of this stage - original PDFs, extracted TXT documents, and the
CSV metadata/index - becomes the input to the preprocessing pipeline
described below (stages 10-14). This stage's own output is never modified
by anything downstream.


## 10. Text Cleaning (preprocess_quotation_text.py)

Input: `Quotation_Preprocessed/text/` — Output: `Quotation_Data/02_clean_text/`

Unicode NFKC normalization, control-character removal, line-ending/tab
normalization, page-separator standardization, conservative OCR-garbage
removal, whitespace collapsing, empty-page removal. Deliberately
conservative: numbers, symbols, prices, units, and model numbers are never
touched, since they matter for downstream analysis.


## 11. Repeated Boilerplate Removal (remove_repeated _messagev2.py)

Input: `Quotation_Data/02_clean_text/` — Output: `Quotation_Data/03_preprocessed_text_2/`

Strips the company's own repeated letterhead/footer/CIN-number text so it
doesn't pollute structured extraction. Configured as a list of known
message variants (not one fixed string) with tolerant matching for common
PDF-extraction quirks: hyphen vs. en dash, OCR misreads in the CIN number,
dashes glued to neighbouring text, scrambled/fragmented letterhead layouts.
This stage writes cleaned TXT files only (no CSV output) - see
`CHANGELOG.md` for the fix history.


## 12. Validation / Profiling (validate_quotations.py)

Input: `Quotation_Data/02_clean_text/` — Output: `Quotation_Data/04_validation/`

A read-only QA pass over the cleaned corpus: per-file stats, an MD5-based
exact-duplicate report, and conservative OCR-quality warnings. This is a
profiling tool, not a filter - nothing is removed or blocked automatically;
it exists so problem documents can be reviewed before structured
extraction.


## 13. Structured Extraction (quotation_parser_v1.py)

Input: `Quotation_Data/03_preprocessed_text_2/` — Output: `Quotation_Data/03_structured_current/`

Regex/heuristic parsing (no LLM calls) of:

- **Quotation-level fields**: customer, subject, quotation number/date,
  currency, subtotal/tax/discount/grand total.
- **BOQ line items**: item number, description, product/make/model/
  version (from explicit labels only), quantity, unit, unit price, total
  price - reconstructed from multi-line, variably-formatted table rows.

Every extracted field carries a confidence rating and full source
traceability (`source_file`, `source_path`, `raw_row_text`). See
`docs/DATA_DICTIONARY.md` for exact schemas.

This step has been through several rounds of fixes based on real
mislabeled output rather than being designed once and left alone - see
`CHANGELOG.md` for the specific bugs found and fixed (and the broader
heuristics that were tried, measured, and reverted).


## 14. Odoo Customer Matching & Enrichment (odoo_match_customer/match_customers.py)

Input: `quotations.csv` + `Quotation_Data/05_odoo_export/` (`res_partners.csv`,
`sale_orders.csv`, `customer_industry_proxy.csv`) — Output: `Quotation_Data/06_customer_matching/`

Matching runs in three stages, in order: a manual override table
(`customer_name_overrides.csv`), an **exact RFQ-number lookup**, then
**local fuzzy name-matching** (no Odoo API calls) as the fallback. The
RFQ lookup exact-matches `quotations.csv`'s `quotation_number` against
`sale_orders.csv`'s `x_studio_internal_rfq_assignment_number` - when a
quotation's number resolves to exactly one customer's order(s), the
match is unambiguous (`RFQ_MATCH`) and carries that specific order's own
fields (`matched_order_*`) alongside the usual enrichment; if the number
is shared across orders for different customers, it's flagged
`RFQ_AMBIGUOUS` rather than guessed. Only quotations with no usable RFQ
number, or no match for it, fall through to fuzzy matching, producing a
match score and status (`customer_enriched.csv`), plus a review list for
anything below HIGH confidence (`customer_review.csv`). Matched rows -
however they were resolved - are then enriched with industry (joined
from `customer_industry_proxy.csv`), contact details (from
`res_partners.csv`), and order history - order count, total PO value,
regions, latest order date, quote status (aggregated from
`sale_orders.csv`). A separate `customer_knowledge_bank.csv` collapses
this down to one row per distinct matched customer (a browsable customer
directory), rather than one row per quotation. See `CHANGELOG.md` v1.7.0
and `docs/DATA_DICTIONARY.md` for the full column list.


## 15. Item-Level Knowledge Bank (knowledge_bank/build_knowledge_bank.py)

Input: `quotation_items.csv` + `06_customer_matching/customer_enriched.csv`
+ `05_odoo_export/sale_orders.csv` — Output: `Quotation_Data/07_knowledge_bank/`

Joins the three finished datasets into one flat analytical table at
line-item grain: what was quoted (item, make/model, quantity, price), to
whom (customer, industry, region), and when. The join key is `source_path`
(verified unique across all 3,876 documents, with 100% of item rows
resolving to one); the order link is `matched_order_id`.

Adds three things the source data doesn't have:

- **Conservative normalization** (`make_normalized`, `model_normalized`,
  `unit_normalized`) - case/whitespace/punctuation only, with raw values
  always preserved. Equivalent-but-differently-spelled manufacturers are
  deliberately NOT merged yet.
- **Derived prices** (`unit_price_final`, `total_price_final`) filled from
  the complementary value and quantity where one side is missing, with
  `price_basis` recording provenance so a derived figure is never mistaken
  for a quoted one.
- **A resolved date** (`quotation_date_final`, ISO) preferring Odoo's
  structured `date_order` over the regex-extracted document date, with
  `date_source` and `date_ambiguous` recording which was used and whether
  a day-first assumption was needed.

Odoo orders that no parsed document resolved to are included as
`data_source = ODOO_ORDER_ONLY` rows so customer and industry coverage
isn't limited to quotes that happened to have a usable attachment.
`knowledge_bank_review.csv` flags rows needing a human look. See
`CHANGELOG.md` v1.8.0 and `docs/DATA_DICTIONARY.md` for the full schema.


## 16. Final Dataset (current state)

1. Original quotation PDFs (`Downloads/`, untouched)
2. Extracted TXT documents (`Quotation_Preprocessed/text/`, untouched)
3. Cleaned, boilerplate-stripped TXT documents (`Quotation_Data/03_preprocessed_text_2/`)
4. Structured `quotations.csv` and `quotation_items.csv` (`Quotation_Data/03_structured_current/`)
5. Live Odoo export (`Quotation_Data/05_odoo_export/`) and customer-level
   knowledge bank (`Quotation_Data/06_customer_matching/`, including
   `customer_knowledge_bank.csv`)
6. Item-level knowledge bank (`Quotation_Data/07_knowledge_bank/`) - the
   flat, one-row-per-line-item analytical table

Not yet built: the analytics layer described in the project doc (per-item /
per-customer / per-industry price trends, repeated modules and product
bundles, standardization opportunities), and fuzzy consolidation of
equivalent makes/models. The dataset both of those need now exists.