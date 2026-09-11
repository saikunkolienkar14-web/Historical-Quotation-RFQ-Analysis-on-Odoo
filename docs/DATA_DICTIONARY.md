# Data Dictionary

## quotation_documents.csv

| Field | Description | Example |
|---|---|---|
| Quotation_Folder | Folder associated with the quotation | 25AKAI001 |
| Filename | Original PDF filename | quotation.pdf |
| Original_Path | Original PDF location | Downloads/... |
| Text_Path | Extracted TXT location | Quotation_Preprocessed/text/... |
| PDF_Type | PDF classification | TEXT_BASED |
| Extraction_Method | Extraction technique | DIRECT_TEXT |
| Pages | Number of PDF pages | 12 |
| Characters | Number of extracted characters | 18452 |
| Status | Processing result | SUCCESS |
| Text | Extracted document text | ... |

## PDF_Type

TEXT_BASED:
PDF contained machine-readable text.

IMAGE_BASED:
PDF contained insufficient machine-readable text and OCR was used.

## Extraction_Method

DIRECT_TEXT:
Text extracted directly using PyMuPDF.

OCR:
Text extracted using Tesseract OCR.


## preprocessing_report.csv (validate_quotations.py)

One row per cleaned TXT file (`Quotation_Data/02_clean_text/`).

| Field | Description | Example |
|---|---|---|
| Filename | TXT filename | quotation.txt |
| Relative_Path | Path relative to the input folder | 25AKAI001/quotation.txt |
| Status | `OK`, `WARNING`, `EMPTY`, or `READ_ERROR` | OK |
| Characters | Character count (stripped) | 18452 |
| Words | Word count | 2891 |
| Lines | Non-empty line count | 412 |
| Estimated_Pages | Page count from page-marker patterns | 8 |
| MD5 | Hash of normalized text, used for duplicate detection | a1b2c3... |
| Symbol_Ratio | Fraction of non-alphanumeric characters (0-1) | 0.04 |
| Alpha_Ratio | Fraction of alphabetic characters (0-1) | 0.71 |
| Warnings | `;`-joined warning codes, e.g. `VERY_SHORT`, `OCR_WARNING:HIGH_SYMBOL_RATIO` | |

## duplicates.csv (validate_quotations.py)

One row per file that is part of an exact-duplicate group (same MD5).

| Field | Description |
|---|---|
| MD5 | Shared hash for the duplicate group |
| Filename | TXT filename |
| Relative_Path | Path relative to the input folder |
| Duplicate_Count | Number of files sharing this hash |


## quotations.csv (quotation_parser_v1.py)

One row per source document (`Quotation_Data/03_structured_current/`).

| Field | Description |
|---|---|
| source_file | TXT filename |
| source_path | Full path to the source TXT (the reliable join key - `source_file` alone collides for ~2% of the corpus) |
| quotation_number | Extracted quotation/proposal/offer number |
| quotation_date | Extracted date (first match near a "date"/"dated" label) |
| customer | Extracted recipient/customer name |
| subject | Extracted subject/title line |
| currency | Detected currency (INR/USD/EUR/GBP) from symbols/codes in the text |
| subtotal, tax, discount, grand_total | Extracted financial summary values |
| boq_detected | `YES`/`NO` - whether a BOQ table header was found |
| boq_headers | The detected header cell text, `|`-joined, for review |
| document_confidence | `HIGH`/`MEDIUM`/`LOW`, from a points system (customer + subject + quotation number + table detected + items found) |
| customer_confidence, subject_confidence, quotation_number_confidence, quotation_date_confidence | Per-field confidence for that extraction |
| warnings | `;`-joined list of parser warning codes for this document (see Troubleshooting) |

## quotation_items.csv (quotation_parser_v1.py)

One row per BOQ line item (`Quotation_Data/03_structured_current/`).

| Field | Description |
|---|---|
| source_file, source_path, quotation_number | Traceability back to the source document (join on `source_path`) |
| item_no | Extracted serial/item number (as written, e.g. "1", "1.1"). **Validated (v1.9.0)**: blanked when above 100 or date-shaped, since those are PO/material codes or dates that landed in this slot. Blank on 7,328 rows for that reason - the row's other data is still valid |
| description | Multi-line item description, cleaned of quantity/unit/price/labeled-field lines |
| product, make, model, version | Pulled from explicit labels only ("Make: Siemens") - blank if the document doesn't label them, never guessed. `product` is empty on every row in practice |
| quantity | Numeric quantity, or blank. **Validated (v1.9.0)**: a quantity with a recognized `unit` is kept at any magnitude (`1600 METER` of cable is real); a *unit-less* value above 100 is blanked as a misplaced material code. Rejected quantities are blanked, never moved into a price field |
| unit | Unit of measure, **canonicalized (v1.9.0)** to one of nine values: `NOS`, `SET`, `LOT`, `METER`, `DAY`, `EACH`, `PCS`, `KG`, `MM`. Unrecognized spellings are blanked and flagged |
| unit_price_raw, unit_price | Raw text and parsed numeric unit price (numeric blank if the raw value is non-numeric, e.g. "QUOTED") |
| total_price_raw, total_price | Same, for total price |
| raw_row_text | The full, unprocessed multi-line block this row was parsed from - useful for verifying a mislabeled row |
| confidence | `HIGH`/`MEDIUM`/`LOW`, from how many of description/quantity/unit/prices were found |

## parser_review.csv (quotation_parser_v1.py)

One row per field/row flagged for human review.

| Field | Description |
|---|---|
| source_file | TXT filename |
| field | What's flagged: `customer`, `subject`, `boq`, `document`, or `item_<item_no>` |
| value | The (possibly empty) extracted value, for context |
| confidence | Confidence at the time of flagging |
| reason | Human-readable reason, or a warning code (see Troubleshooting for the code list) |

## customer_enriched.csv / customer_review.csv / customer_knowledge_bank.csv (odoo_match_customer/match_customers.py)

`customer_enriched.csv` (`Quotation_Data/06_customer_matching/`) is
`quotations.csv` with the following columns appended - one row per
quotation:

| Field | Description |
|---|---|
| matched_customer_id, matched_customer_name, matched_customer_reference | The matched Odoo `res.partner` record's id / name / `ref` |
| matched_email, matched_phone, matched_city, matched_state, matched_country | Contact details from `res_partners.csv` - often blank where the underlying Odoo record itself has no value, not a join failure |
| matched_industry | The matched customer's most common industry, from `customer_industry_proxy.csv` (see that CSV below) - blank if the customer has no order history to derive one from |
| matched_industry_confidence | e.g. `"7 of 8 orders"` - how many of the customer's orders agreed on `matched_industry` |
| total_orders, total_po_value, regions, latest_order_date, customer_type, adage_customer, end_user, quote_status_summary | Order-history aggregation from `sale_orders.csv`, grouped by matched customer id (`total_po_value` summed, `regions`/`quote_status_summary` distinct-value lists, single-valued fields taken from the most recent order) |
| customer_match_score | 0.0-1.0 fuzzy similarity score (`1.0` for an RFQ-number match) |
| customer_match_status | `RFQ_MATCH`/`RFQ_AMBIGUOUS`/`EXACT`/`HIGH`/`REVIEW`/`LOW`/`NO_MATCH`/`MISSING`/`OVERRIDE` - see below |
| matched_order_id, matched_rfq_number, matched_order_industry, matched_order_customer_type, matched_order_region, matched_order_po_number, matched_order_po_value, matched_order_quote_status, matched_order_date | Only populated on an `RFQ_MATCH` row - the **specific matched `sale.order`'s own** fields (not the customer-level aggregation above), resolved by exact-matching `quotation_number` against `sale_orders.csv`'s `x_studio_internal_rfq_assignment_number` column. Blank for every other match status. |

**Matching order**: manual override (`customer_name_overrides.csv`) is
checked first, then an exact RFQ-number lookup against
`sale_orders.csv`, then fuzzy name-matching as the fallback when no RFQ
number was extracted or no match was found for it.

- `RFQ_MATCH`: `quotation_number` matched exactly one customer's order(s)
  in `sale_orders.csv` via `x_studio_internal_rfq_assignment_number`.
- `RFQ_AMBIGUOUS`: the same RFQ number appears on orders for two or more
  *different* customers - no customer is assigned (not guessed); written
  to `customer_review.csv`.
- `EXACT`/`HIGH`/`REVIEW`/`LOW`/`NO_MATCH`/`MISSING`/`OVERRIDE`: the
  original fuzzy-matching outcomes (unchanged), reached only when RFQ
  matching didn't resolve the row.

`customer_review.csv` lists every quotation whose match wasn't
`RFQ_MATCH`/`EXACT`/`HIGH`, for manual review (this includes
`RFQ_AMBIGUOUS` rows).

`customer_knowledge_bank.csv` is a **customer-level** directory, not a
quotation log: one row per distinct `matched_customer_id` (deduplicated
from `customer_enriched.csv`), carrying every field above except the
match score/status, plus `quotation_count` (how many quotations matched
to this customer).


## knowledge_bank_items.csv (knowledge_bank/build_knowledge_bank.py)

The item-level knowledge bank (`Quotation_Data/07_knowledge_bank/`) - the
flat analytical table joining what was quoted to whom, and when. **41,673
rows.**

Two kinds of row, distinguished by `data_source`:

| data_source | Rows | What it is |
|---|---|---|
| `ATTACHMENT_ITEM` | 38,487 | One parsed BOQ line item, joined to its document's customer match and (where the document matched an Odoo order) that order's fields |
| `ODOO_ORDER_ONLY` | 3,186 | One Odoo sale order that no parsed document resolved to. All item-level fields are blank by design - these rows exist so customer/industry coverage reflects the whole business, not just quotes with a parseable attachment |

| Field | Description |
|---|---|
| data_source | `ATTACHMENT_ITEM` or `ODOO_ORDER_ONLY` (see above) - **filter on this before any item-level analysis** |
| source_file, source_path, quotation_number, item_no | Lineage back to the source document/line (join on `source_path`; blank on Odoo-only rows except `quotation_number`) |
| description, make, model, version, quantity, unit | Item fields exactly as parsed - unmodified passthrough from `quotation_items.csv` |
| unit_price, total_price, unit_price_raw, total_price_raw | Prices exactly as parsed (numeric and raw text forms) |
| item_confidence | `HIGH`/`MEDIUM`/`LOW` from the parser. **52% are LOW** - segment or weight by this rather than treating all rows equally |
| make_normalized, model_normalized | Uppercased, whitespace-collapsed, edge-punctuation-stripped forms for grouping. **Cosmetic differences only** - genuinely different spellings of one manufacturer (`SIEMENS AG, GERMANY` vs `SIEMENS`) are intentionally NOT merged yet |
| unit_normalized | Canonical unit (`Nos`/`No`/`nos` → `NOS`, `Mtrs` → `METER`). An unrecognized unit is uppercased, not dropped |
| unit_price_final, total_price_final | The price to analyze - reported where available, otherwise derived from the other value and `quantity` |
| price_basis | How the two fields above were obtained: `REPORTED` / `DERIVED_FROM_UNIT` / `DERIVED_FROM_TOTAL` / `NONE`. **Always check this before treating a price as quoted** |
| matched_customer_id, matched_customer_name, matched_industry, matched_industry_confidence, customer_match_status, customer_match_score, matched_city, matched_state, matched_country, customer_type, regions | Customer fields carried from `customer_enriched.csv`. On Odoo-only rows these come straight from the order, and `customer_match_status` is `ODOO_ONLY` |
| matched_order_id, matched_rfq_number, matched_order_industry, matched_order_po_number, matched_order_po_value, matched_order_quote_status, order_state, firm_or_budgetary | The specific Odoo order's own fields, where one was matched |
| quotation_date_raw, order_date_raw | The two candidate dates, unmodified |
| quotation_date_final | Resolved date, ISO `YYYY-MM-DD`. Verified to parse as a real date within 2022-2026 for 100% of non-blank values |
| date_source | Which date won: `ODOO_ORDER` (structured, preferred) / `PARSED_DOC` (regex-extracted from the PDF) / `NONE` |
| date_ambiguous | `YES` when day and month were both ≤12 and day-first was assumed (Indian convention). **9,931 rows** - treat those dates as approximate |
| quotation_year, quotation_month | `YYYY` and `YYYY-MM`, derived from `quotation_date_final`, for time bucketing |
| currency, document_confidence | Document-level fields from the parser |

**`product` is deliberately absent**: it is populated on 0 of the 38,487
rows in `quotation_items.csv`, so it would be a permanently empty column.
`version` is carried despite having only 2 non-blank values.

## knowledge_bank_review.csv (knowledge_bank/build_knowledge_bank.py)

Rows needing a human look - 29,955 of 41,673. One row per flagged record;
`reason` is a `;`-joined list, so a row can carry several.

| Reason code | Count | Meaning |
|---|---|---|
| `NO_PRICE` | 17,529 | No usable price could be reported or derived |
| `DATE_AMBIGUOUS_DAY_MONTH` | 9,931 | Day-first assumed; both readings were valid |
| `LOW_CONFIDENCE_ITEM_WITH_PRICE` | 6,820 | Has a price but the parser rated the row LOW |
| `PRICE_WITHOUT_DESCRIPTION` | 6,820 | Price present, description empty - likely a misparsed row |
| `IMPLAUSIBLE_NEGATIVE_PRICE` | 1,777 | Price below zero. Caused by the upstream parser digit-stripping prose (`"CAPACITY) QTY-2"` → `-2`), not by the join - **exclude these from price analysis** |
| `NO_MATCHED_CUSTOMER` | 1,725 | No Odoo customer resolved for the row |
| `DATE_MISSING` | 1,699 | No date on either side |
| `DATE_UNPARSEABLE` | 22 | A date value was present but not a real date (e.g. `24-25/1264`) |

`knowledge_bank_summary.txt` carries corpus-wide counts, distributions and
per-column fill rates for the same run (aggregate only - no customer
names, per project convention).