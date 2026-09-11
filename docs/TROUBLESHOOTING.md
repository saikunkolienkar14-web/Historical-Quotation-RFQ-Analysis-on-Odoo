# Troubleshooting

## 1. ModuleNotFoundError: No module named 'fitz'

Install PyMuPDF:

    python -m pip install PyMuPDF

The modern import is:

    import pymupdf


## 2. Tesseract Not Found

Verify the executable:

    C:\Users\Sai\AppData\Local\Tesseract-OCR\tesseract.exe

Run:

    & "C:\Users\Sai\AppData\Local\Tesseract-OCR\tesseract.exe" --version


If Tesseract is installed somewhere else, update:

    TESSERACT_PATH


## 3. PDF Not Found

Make sure `PDF_PATH` points to the actual PDF file and not the folder.

Correct:

    PDF_PATH = r"C:\...\quotation.pdf"

Incorrect:

    PDF_PATH = r"C:\...\quotation"


## 4. fitz API Deprecated Warning

Older versions/examples use:

    import fitz

Modern PyMuPDF uses:

    import pymupdf

Use:

    pymupdf.open(pdf_path)


## 5. No Text Extracted

If direct extraction returns little or no text, the PDF may be scanned/image-based.

The preprocessing pipeline should then use OCR.


## 6. OCR Produces Poor Text

Possible causes:

- Low-resolution scan
- Blurry document
- Rotated pages
- Complex tables
- Poor image quality
- Unusual fonts

Try increasing OCR resolution or improving the source PDF.


## 7. Wrong Input Folder

The main script expects the quotation PDFs under:

    Downloads/

The script searches recursively, so quotation-specific subfolders are supported.


## 8. Output Already Exists

Existing TXT files may be overwritten depending on the current script behavior.

Always keep the original PDFs unchanged.


## 9. Virtual Environment

Activate the virtual environment before running the project:

    .\venv\Scripts\activate

Then install dependencies:

    python -m pip install -r requirements.txt


## 10. Understanding quotation_parser_v1.py Warning Codes

Warning codes appear in `parser_review.csv`'s `reason` column and in each
document's `warnings` field in `quotations.csv`. They flag rows/documents
for review - they do not mean the row was dropped.

| Code | Meaning |
|---|---|
| `BOQ_HEADER_NOT_FOUND` / "BOQ header not detected" | No BOQ table header (e.g. "Description" + "Rate"/"Amount") was found in the document at all. No items were extracted for it. |
| `NO_ITEM_NUMBER_ROWS_FOUND` | A BOQ header was found, but no lines inside it looked like item numbers. |
| `ITEM_<n>_NO_PRICE_VALUES` | Item `<n>` has no numeric or recognized text (`QUOTED`, `TBD`, etc.) price value at all. |
| `ITEM_<n>_ONLY_ONE_VALUE` | Item `<n>` has exactly one price value - it's assumed to be the total; check `unit_price_raw` is genuinely blank in the source, not just undetected. |
| "Customer not detected" / "Subject not detected" | No line matched the known customer/subject labels near the top of the document. |
| "BOQ row needs review" | Any item with confidence below `HIGH` gets one of these, pointing at its description for a quick look. |
| `ITEM_<n>_INVALID_ITEM_NUMBER` | The item number was above 100 or date-shaped (a PO/material code or a date in the item-number slot), so `item_no` was blanked. The row is kept - its description and prices are usually still valid. |
| `ITEM_<n>_QUANTITY_OUT_OF_RANGE` | A quantity above 100 with **no recognized unit** - almost always a material code or price fragment, so `quantity` was blanked. A large quantity WITH a unit (`1600 METER`) is legitimate and is never flagged. Price fields are never modified by this rule. |
| `ITEM_<n>_UNKNOWN_UNIT` | The unit wasn't in the canonical vocabulary (`NOS`, `SET`, `LOT`, `METER`, `DAY`, `EACH`, `PCS`, `KG`, `MM`), so it was blanked. Rare - only 22 rows corpus-wide. If a real unit keeps showing up here, add it to `UNIT_CANONICAL` in `quotation_parser_v1.py`. |

If you're seeing a specific document mislabeled, the fastest fix is to
paste the exact surrounding text (the label wording, table header, or row
layout) rather than describing it generally - broad heuristics based on
general descriptions have repeatedly measured worse than narrow ones
targeted at the exact wording. See `CHANGELOG.md` v1.3.0 for examples of
fixes that were tried, measured, and reverted for this reason.


## 11. odoo_match_customer/match_customers.py: matched_industry / contact / order-history fields are blank

- `matched_industry` blank: the matched customer has no rows in
  `customer_industry_proxy.csv` (no order history to derive an industry
  from) - re-run `odoo_export/customer_industry_proxy.py` after a fresh
  `odoo_export/export_customers.py` pull if this seems stale.
- `matched_email` / `matched_phone` / `matched_city` / `matched_state` /
  `matched_country` blank: usually real sparsity in the underlying
  `res.partner` record in Odoo, not a join bug - confirmed against real
  data in `CHANGELOG.md` v1.6.0 (many matched customers have no
  email/phone/city on file in Odoo at all).
- `total_orders` / `total_po_value` / etc. blank: the matched customer id
  has no rows in `sale_orders.csv` - check `Quotation_Data/05_odoo_export/sale_orders.csv`
  exists and is a recent pull.

If the script won't run at all, check that all three input files exist
under `Quotation_Data/05_odoo_export/` (`res_partners.csv`,
`sale_orders.csv`, `customer_industry_proxy.csv` - the last two are
optional; their absence degrades to blank enrichment fields, not a
crash) and that `Quotation_Data/03_structured_current/quotations.csv`
exists. A `PermissionError` writing any of the three output CSVs under
`Quotation_Data/06_customer_matching/` usually means one of them is open
in Excel - close it and re-run.


## 12. odoo_match_customer/match_customers.py: RFQ_MATCH / RFQ_AMBIGUOUS statuses

- A row stays on fuzzy-matching statuses (`EXACT`/`HIGH`/`REVIEW`/`LOW`/
  `NO_MATCH`) instead of getting `RFQ_MATCH` when either: `quotation_number`
  is blank in `quotations.csv` for that document (re-run
  `quotation_parser_v1.py` - see warning codes above, and `CHANGELOG.md`
  v1.7.1 for the latest extraction fixes), or the number isn't present in
  `sale_orders.csv`'s `x_studio_internal_rfq_assignment_number` column
  (check `Quotation_Data/05_odoo_export/sale_orders.csv` is a recent
  export pull - re-run `odoo_export/export_customers.py` if stale).
- `RFQ_AMBIGUOUS`: the same RFQ number is shared by orders for two or
  more *different* customers in Odoo. This isn't a bug - `customer_review.csv`
  lists the conflicting customer names for a human decision; the row is
  intentionally left without an assigned customer rather than guessed.
- `matched_order_*` columns (industry, PO number/value, quote status,
  date) are only populated on `RFQ_MATCH` rows - they reflect that one
  specific matched order, not the customer-level aggregation
  (`total_orders`, `total_po_value`, etc., which is still populated for
  every matched row regardless of match method). See
  `docs/DATA_DICTIONARY.md` for the full field list.