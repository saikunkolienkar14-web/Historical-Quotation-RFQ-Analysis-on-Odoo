"""
CSV writer for the coords extractor. 17-column schema: the original 17
production columns plus `currency` (plan decision: non-INR rows such as SAR
or USD offers get a currency tag instead of being silently mixed into INR
totals or blanked), minus `product`/`version` (dropped - never populated in
this corpus, see fields.py); `description` is split into `product_name`
(short heading, see fields.extract_heading) and `description_full` (the
complete, unmodified spec text) so neither the heading nor the full detail
is lost.

`total_price_source` ("stated" | "derived") and `price_status` ("NUMERIC" |
"QUOTED_SEPARATELY" | "INCLUDED" | "MISSING") were added by the price-field
fix (money.py: parse_price/combine_price_status, __main__.py's total-price
precedence). unit_price and total_price are always kept as two independent
fields - total_price is never silently replaced by quantity x unit_price
when the source states its own total.
"""
from __future__ import annotations

import csv
from pathlib import Path

# utf-8-sig, not utf-8: Excel on Windows decodes a BOM-less CSV using the
# system ANSI codepage (cp1252), which renders the UTF-8 bytes for "₹" as
# "â‚¹" and "•" as "â€¢" even though the file itself is perfectly valid
# UTF-8. The BOM is what makes Excel detect UTF-8. Matches the convention
# every other CSV writer in this project already uses (quotation_parser_v1,
# knowledge_bank, odoo_export, odoo_match_customer, ...).
CSV_ENCODING = "utf-8-sig"

FIELDNAMES = [
    "source_file", "source_path", "quotation_number",
    # item_no is TEXT, exactly as printed in the source ("1", "1.1", "4a")
    # - never a number, since "1.1" is a two-level marker, not the value
    # 1.1. parent_item_no/item_level (fields.derive_item_hierarchy) carry
    # that hierarchy explicitly so consumers don't have to re-parse it.
    # NOTE for consumers: CSV carries no types, so read this column as a
    # string (e.g. pandas read_csv(dtype=str)) or it will be coerced back
    # into a float and "1.10" will collapse onto "1.1".
    "item_no", "parent_item_no", "item_level",
    "product_name", "description_full", "make", "model",
    "quantity", "unit", "unit_price_raw", "unit_price",
    "total_price_raw", "total_price", "total_price_source",
    "price_status", "currency",
    "raw_row_text", "confidence", "validation_error",
]

REVIEW_FIELDNAMES = FIELDNAMES + ["path_taken"]


def write_items_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding=CSV_ENCODING) as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_review_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding=CSV_ENCODING) as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_documents_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = ["source_file", "source_path", "n_pages", "n_regions", "path_taken", "n_rows", "confidence"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding=CSV_ENCODING) as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
