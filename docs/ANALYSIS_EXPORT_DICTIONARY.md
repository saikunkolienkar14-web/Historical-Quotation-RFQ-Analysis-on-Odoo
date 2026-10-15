# Analysis export data dictionary

Columns of `Quotation_Data/08_analysis_ready/knowledge_bank_analysis_<date>.xlsx`,
produced by `knowledge_bank/build_analysis_export.py`.

- **Source column** is the column read from
  `07_knowledge_bank_coords/knowledge_bank_items_coords_<date>.csv`
  (called "KB CSV" below). `—` means the value is computed in this script.
- **Origin** is where the data ultimately comes from:
  **PDF** (boq_coords extraction), **Manifest** (`Quotation PDFs/quotation_pdfs.csv`),
  **Odoo** (live export joined by `match_customers_coords.py`),
  **Rules** (alias / family tables in `07_knowledge_bank/`), **Derived** (this script).
- Blank cell = unknown / not applicable. Placeholder text (`Unknown`, `NA`, `N/A`, `-`,
  `--`, `NIL`, `NULL`) is turned into blank by `clean_text()`.
- This is a first version. Upstream origins marked *(inferred)* come from column names
  and the script's own dictionary, not from reading the upstream scripts. Update later.

## Sheets

| Sheet | Grain | Content |
|---|---|---|
| Items | one row per BoQ line | the analysis sheet; no order-level money |
| Quotations | one row per document | order-level Odoo money and per-document rollups |
| Audit | one row per BoQ line | `doc_id` + `line_seq` + source columns moved off Items, verbatim |
| FX_Rates | one row per currency | rate table behind the `*_inr` columns |
| Data_Dictionary | one row per column | short meanings (the in-code `DICTIONARY`) |

Join key between sheets: `doc_id`. Items and Audit join on `doc_id` + `line_seq`.

## Items sheet (and the shared columns on Quotations)

Columns marked **Q** also appear on Quotations (copied from the document's first row).

### Quotation identity and revisions

| Column | Q | Source column | How derived | Origin |
|---|---|---|---|---|
| doc_id | Q | `source_path` | `"D" + sha1(source_path)[:8]`, upper-case. Same path always gives the same id | Derived |
| line_seq | | `source_path` | Position within the document, in file order, starting at 1 (`groupby.cumcount()+1`) | Derived |
| quotation_number | Q | `quotation_number` | Copied as is. Built from the manifest `RFQ` + `Revision_Number` (`R1` appended to RFQ, or a standalone id used as printed, or RFQ alone). Not unique; can be junk | Manifest |
| quotation_base_no | Q | `quotation_number` | `parse_revision()`: regex strips a trailing `R<1-2 digits>`; `2526G007R1` → `2526G007` | Derived |
| revision_no | Q | `quotation_number` | Number after the trailing `R`; `0` when there is no suffix | Derived |
| revision_group | Q | `matched_order_id`, `quotation_base_no`, `doc_id` | First non-blank of: Odoo order id, `Q:<base no>`, `doc_id`. Documents in one group are revisions of each other | Derived |
| is_latest_revision | Q | — | Within a `revision_group`, sort by `revision_no`, `document_date`, `doc_id`; last document gets `Y`, others `N` | Derived |
| odoo_rfq_number | Q | `matched_rfq_number` | Copied | Odoo |
| odoo_order_id | Q | `matched_order_id` | Copied. Blank = no Odoo order resolved | Odoo |

### Dates

| Column | Q | Source column | How derived | Origin |
|---|---|---|---|---|
| quotation_date | Q | `quotation_date_final` | Parsed as `YYYY-MM-DD`. Odoo order creation date when matched, else the date printed on the PDF | Odoo, else PDF *(inferred)* |
| document_date | Q | `quotation_date_raw` | `parse_document_date()`: day-first numeric (`dd/mm/yy`, `.` `-` `/`) or text (`23 June 2025`). Blank if unreadable (e.g. a reference number) | PDF |
| quotation_year | Q | `quotation_date_final` | Calendar year | Derived |
| quotation_month | Q | `quotation_date_final` | `YYYY-MM` | Derived |
| fiscal_year | Q | `quotation_date_final` | Indian FY (April–March), e.g. `FY2025-26` | Derived |
| date_source | Q | `date_source` | Copied. `ODOO_ORDER` reliable, `PARSED_DOC` regex from PDF, `NONE` | KB builder |
| date_ambiguous | Q | `date_ambiguous` | Copied. `YES` = day and month both ≤ 12, day-first assumed. Treat as approximate | KB builder |

### Customer (all from the Odoo match)

| Column | Q | Source column | How derived | Origin |
|---|---|---|---|---|
| customer_id | Q | `matched_customer_id` | Copied. Group by this, not by name | Odoo |
| customer_name | Q | `matched_customer_name` | Copied, trimmed. **Confidential** | Odoo |
| customer_type | Q | `customer_type` | Upper-cased: END USER / EPC / EPMC | Odoo *(inferred)* |
| end_user_id | Q | `matched_end_user_id` | Copied | Odoo |
| end_user_name | Q | `matched_end_user_name` | Copied. **Confidential** | Odoo |
| industry | Q | `matched_order_industry`, `matched_industry` | Order industry if present, else the customer's usual industry | Odoo |
| industry_source | Q | same two | `ODOO_ORDER` if order industry present, `CUSTOMER_HISTORY` if only the customer one, else blank | Derived |
| industry_detail | Q | `matched_order_industry_specify_others`, `matched_industry_specify_others` | Upper-cased free text, kept only when `industry` is `OTHERS`. Order value preferred | Odoo |
| city | Q | `matched_city` | Copied | Odoo |
| state | Q | `matched_state` | Trailing `(IN)`-style suffix removed; `Chattisgarh` → `Chhattisgarh`; one Thai-script value → `Bangkok` | Odoo |
| country | Q | `matched_country` | Copied | Odoo |
| sales_regions | Q | `regions` | Split on comma, deduplicated, sorted, joined with `\|`. Sales-team tags, not geography | Odoo |
| customer_match_quality | Q | `customer_match_status`, `matched_customer_id` | No customer id → `UNMATCHED`; status in `REVIEW`/`LOW`/`RFQ_AMBIGUOUS` → `REVIEW`; else `MATCHED` | Derived |

### Item

| Column | Source column | How derived | Origin |
|---|---|---|---|
| item_no | `item_no` | Copied as printed. Repeats within a document | PDF |
| parent_item_no | `parent_item_no` | Copied (parsed parent of a sub-item) | PDF |
| item_level | `item_level` | Copied as integer: 0 no item number, 1 main item, 2–3 sub-item | PDF |
| line_type | `item_level`, `quantity`, `price_status`, `product_name`, `item_no` | `classify_lines()`: name starts with total / grand total / sub-total and no item number → `TOTAL`; level 0 with no quantity and `price_status = MISSING` → `HEADER`; level 0 or 1 → `LINE`; deeper → `SUB_LINE` | Derived |
| parent_line_seq | `item_no`, `parent_item_no` | For `SUB_LINE`: nearest earlier row whose `item_no` equals the parent number and has a lower level. Blank if the parent isn't printed (1A, 1B with no row 1) | Derived |
| bundle_id | — | `doc_id-<seq>` of the top-level row the line belongs to. Siblings with an unprinted shared parent share a bundle. Blank for HEADER / TOTAL | Derived |
| product_name | `product_name` | Stripped, otherwise unchanged. Not standardized yet | PDF |
| description | `description` | Stripped, otherwise unchanged. Not standardized yet | PDF |
| product_family | `product_family` | Copied. Blank for most rows (no rule matched) | Rules (`model_family_rules.csv`, `description_family_rules.csv`) |
| make_canonical | `make_canonical` | Stripped. Spelling variants merged via the alias table; alternates `\|`-joined | Rules (`make_aliases.csv`) |
| model_canonical | `model_canonical` | Stripped. Cosmetic variants merged; `OXYMAT 61` ≠ `OXYMAT 64` | Rules (`model_aliases.csv`) |
| quantity | `quantity` | Number, commas removed. Blank = not printed (never assumed 1) | PDF |
| unit_std | `unit_normalized` | Mapped by `UNIT_STD` (EACH/UNIT/PCS → NOS, MANDAY/MDY → MAN-DAY, etc.). Unmapped values are kept upper-cased | Derived |
| unit_class | `unit_std` | `UNIT_CLASS`: NOS → COUNT; SET/LOT/JOB → BUNDLE; METER → LENGTH; DAY/MAN-DAY/MONTH/YEAR → TIME; VISIT → SERVICE_VISIT. Compare unit prices only within one class | Derived |

### Pricing

| Column | Source column | How derived | Origin |
|---|---|---|---|
| price_status | `price_status` | Copied: NUMERIC / QUOTED_SEPARATELY / INCLUDED / MISSING | PDF |
| currency_orig | `currency` | Copied | PDF |
| currency_source | `currency_source` | Copied: CELL (in the price cell), DOCUMENT (markers on table pages), DEFAULT (INR assumed, unconfirmed) | PDF |
| unit_price_orig | `unit_price_final` | Number, commas removed. In `currency_orig` | PDF |
| total_price_orig | `total_price_final` | Number, commas removed. In `currency_orig` | PDF |
| fx_rate_to_inr | `currency` | INR → 1; USD 84.84; EUR 94.25; SAR 22.61 (`FX_RATES_TO_INR`); any other currency or blank → blank | Derived |
| fx_source | `currency` | `BASE_CURRENCY` (INR), `ODOO_FIXED_RATE`, `NO_RATE`, blank when no currency | Derived |
| unit_price_inr | — | `unit_price_orig × fx_rate_to_inr`, rounded to 2 decimals | Derived |
| total_price_inr | — | `total_price_orig × fx_rate_to_inr`, rounded to 2 decimals | Derived |
| price_basis | `price_basis` | Copied: REPORTED (printed), DERIVED_FROM_TOTAL, NONE | KB builder |
| price_quality | `price_quality` | Copied: TRUSTED / FLAGGED / NON_NUMERIC | KB builder (from `boq_coords/money.py` checks) |
| item_confidence | `item_confidence` | Copied: HIGH / LOW | KB builder |
| price_usable | — | `Y` when `price_quality = TRUSTED`, `price_basis` is REPORTED or DERIVED_FROM_TOTAL, a price above zero, currency present, and not a TOTAL row | Derived |
| include_in_doc_total | — | `Y` when `price_usable = Y`, `total_price_orig > 0`, and no ancestor row already counted (`include_in_total()`) | Derived |
| duplicate_line | all source columns | `Y` on the 2nd+ copy of a row identical in every source column. Rows are flagged, not removed | Derived |

### Outcome (Odoo order, repeated on every line)

| Column | Q | Source column | How derived | Origin |
|---|---|---|---|---|
| quote_status | Q | `matched_order_quote_status` | `QUOTE - YES/PENDING/REGRET` → `YES`/`PENDING`/`REGRET` | Odoo |
| quote_nature | Q | `firm_or_budgetary` | Upper-cased: FIRM / BUDGETARY / TENDER / ARC | Odoo *(inferred)* |
| outcome_status | Q | `matched_order_present_status` | Upper-cased: WON, LOST, DROP, ACTIVE, HOLD, SYSTEM CLOSED | Odoo |
| outcome_group | Q | `matched_order_present_status` | WON, LOST, DROP → DROPPED, ACTIVE/HOLD → OPEN, SYSTEM CLOSED → CLOSED_NO_OUTCOME | Derived |
| winning_chance_pct | Q | `matched_order_winning_chance` | `"75%"` → `75`; blank if not a percentage | Odoo |
| sbu | Q | `matched_order_sbu_type` | Upper-cased | Odoo |
| spares_type | Q | `matched_order_spares_type` | Copied | Odoo |
| service_type | Q | `matched_order_service_type` | Copied | Odoo |

## Quotations sheet: columns not on Items

Taken from the document's first row. Order-level money is here only, so it can be summed
over `is_latest_revision = Y` without being multiplied by line count.

| Column | Source column | How derived | Origin |
|---|---|---|---|
| type_of_quote | `matched_order_type_of_quote` | Upper-cased (Techno - Commercial / Technical) | Odoo |
| expected_finalization | `matched_order_tentative_finalization_month`, `matched_order_finalization_year` | `YYYY-MM`; blank if either is Unknown | Odoo |
| cycle_time_days | `matched_order_average_cycle_time` | `"45 days"` → `45` | Odoo |
| loss_reason | `matched_order_reason_for_loss` | Copied | Odoo |
| loss_reason_detail | `matched_order_main_reason_of_losing_order` | Copied (free text) | Odoo |
| po_number | `matched_order_po_number` | Copied | Odoo |
| order_currency | `matched_order_po_currency` | Copied | Odoo |
| order_latest_price_quoted | `matched_order_latest_price_quoted` | Copied as text with currency symbol | Odoo |
| order_price_inr | `matched_order_price_in_inr` | Number; `0` → blank | Odoo |
| order_po_value_orig | `matched_order_po_value` | Number; `0` → blank (no PO) | Odoo |
| order_po_value_inr | `matched_order_po_value_in_inr` | Number; `0` → blank | Odoo |
| order_potential_estimate_inr | `matched_order_total_potential_estimate` | Number; `0` → blank (not estimated) | Odoo |
| line_count | — | Rows on Items for this document | Derived |
| priced_line_count | — | Rows with `include_in_doc_total = Y` | Derived |
| doc_currency | — | The single `currency_orig` among included lines, else `MIXED`, blank if none | Derived |
| sum_items_orig | — | Sum of `total_price_orig` over included lines; blank when `doc_currency` is MIXED or blank | Derived |
| sum_items_inr | — | Sum of `total_price_inr` over included lines | Derived |
| reconcile_ratio_vs_odoo | — | `sum_items_inr / order_price_inr`, 4 decimals. Near 1 = document lines match Odoo's quoted total | Derived |

## Audit sheet

`doc_id`, `line_seq`, then these KB CSV columns copied unchanged:
`source_file`, `source_path` (both can contain customer names), `data_source`,
`quotation_date_raw`, `order_date_raw`, `matched_order_create_date`, `order_state`,
`matched_order_rfq_reference_number`, `matched_order_control_no`, `matched_industry`,
`matched_industry_specify_others`, `matched_order_industry`,
`matched_order_industry_specify_others`, `matched_industry_confidence`,
`customer_match_status`, `customer_match_score`, `make`, `model`, `make_normalized`,
`model_normalized`, `make_canonical_basis`, `model_canonical_basis`,
`product_family_basis`, `unit`, `unit_normalized`, `unit_class`, `unit_price`,
`total_price`, `unit_price_raw`, `total_price_raw`, `total_price_source`,
`unit_price_final`, `total_price_final`, `currency`, `document_currency`,
`arithmetic_check`, `validation_error`.

Note: the Audit sheet has its own `unit_class` copied from the KB CSV. The Items sheet
`unit_class` is recomputed from `unit_std`.

## FX_Rates sheet

| Currency | Rate to INR | Source |
|---|---|---|
| INR | 1 | base currency |
| USD | 84.84 | Odoo's fixed rate (matched_order_price_in_inr ÷ matched_order_latest_price_quoted, measured 2026-09-28) |
| EUR | 94.25 | same |
| SAR | 22.61 | same |

Odoo's rate is fixed and ignores exchange-rate movement over 2024–26.

## Rules to apply when using the workbook

- Item-level price analysis: filter `price_usable = Y`; segment by `item_confidence`.
- Document totals: sum `total_price_inr` only where `include_in_doc_total = Y`; never sum `TOTAL` rows.
- Order-level money: sum only over `is_latest_revision = Y` on the Quotations sheet.
- Treat `date_ambiguous = YES` dates as approximate; `date_source = ODOO_ORDER` is reliable.
- `currency_source = DEFAULT` means INR was assumed, not read from the document.
- Compare unit prices only within one `unit_class`.
