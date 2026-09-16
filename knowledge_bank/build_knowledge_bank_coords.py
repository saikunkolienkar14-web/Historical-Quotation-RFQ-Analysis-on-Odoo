"""
Knowledge Bank Builder - boq_coords variant
============================================

Purpose:
    Same join build_knowledge_bank.py performs, but sourced from
    boq_coords' own coordinate-aware item extraction instead of
    quotation_parser_v1.py's, so the two extractors' knowledge banks can
    be compared side by side. Reuses build_knowledge_bank.py's pure
    normalization/price/date helpers by import - only the input item
    file, the input customer-match file, and the output columns differ.

Input:
    Quotation_Data/03f_structured_coords/quotation_items.csv
    Quotation_Data/06_customer_matching_coords/customer_enriched_coords.csv
    Quotation_Data/05_odoo_export/sale_orders.csv

Output:
    Quotation_Data/07_knowledge_bank_coords/knowledge_bank_items_coords.csv
    Quotation_Data/07_knowledge_bank_coords/knowledge_bank_review_coords.csv
    Quotation_Data/07_knowledge_bank_coords/knowledge_bank_summary_coords.txt

Design:
    Only ATTACHMENT_ITEM-equivalent rows are produced here (labelled
    COORDS_ITEM) - no ODOO_ORDER_ONLY rows. Those are identical
    regardless of which item extractor produced the attachment rows
    (they represent Odoo orders no document resolved to at all), so
    duplicating them into a second knowledge bank would only repeat
    build_knowledge_bank.py's own bookkeeping without telling us
    anything new about boq_coords.

    Column mapping from boq_coords' 19-column item schema (see
    boq_coords/emit.py): description_full -> description, with
    product_name kept as its own extra column rather than merged in;
    parent_item_no/item_level/total_price_source/price_status/
    validation_error carried through as additional lineage/quality
    columns; boq_coords' per-item `currency` kept distinct from the
    document-level `document_currency` borrowed from quotations.csv
    (avoids two different meanings colliding under one column name).
    boq_coords has no `version` field (never populated in this corpus,
    same reason quotation_parser_v1.py's own `product` column is
    dropped there) so no version column exists here either.

Important:
    Progress/summary output is aggregate (counts) only - never a
    customer name. Redirect this script's own stdout to a file when
    running it.

This script does NOT modify any of its input files.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(PROJECT_ROOT))

from build_knowledge_bank import (  # noqa: E402
    format_number,
    normalize_text_value,
    normalize_unit,
    resolve_date,
    resolve_prices,
)


# ============================================================
# CONFIGURATION
# ============================================================

ROOT = PROJECT_ROOT.parent

COORDS_ITEMS_CSV = (
    ROOT
    / "Quotation_Data"
    / "03f_structured_coords"
    / "quotation_items.csv"
)

CUSTOMER_ENRICHED_COORDS_CSV = (
    ROOT
    / "Quotation_Data"
    / "06_customer_matching_coords"
    / "customer_enriched_coords.csv"
)

SALE_ORDERS_CSV = (
    ROOT
    / "Quotation_Data"
    / "05_odoo_export"
    / "sale_orders.csv"
)

OUTPUT_FOLDER = (
    ROOT
    / "Quotation_Data"
    / "07_knowledge_bank_coords"
)

ITEMS_CSV = OUTPUT_FOLDER / "knowledge_bank_items_coords.csv"
REVIEW_CSV = OUTPUT_FOLDER / "knowledge_bank_review_coords.csv"
SUMMARY_TXT = OUTPUT_FOLDER / "knowledge_bank_summary_coords.txt"

CSV_ENCODING = "utf-8-sig"


# ============================================================
# OUTPUT SCHEMA
# ============================================================

OUTPUT_COLUMNS = [

    # Lineage
    "data_source",
    "source_file",
    "source_path",
    "quotation_number",
    "item_no",
    "parent_item_no",
    "item_level",

    # Item (raw, as parsed)
    "product_name",
    "description",
    "make",
    "model",
    "quantity",
    "unit",
    "unit_price",
    "total_price",
    "unit_price_raw",
    "total_price_raw",
    "total_price_source",
    "price_status",
    "currency",
    "item_confidence",
    "validation_error",

    # Normalized (additive - raw always kept above)
    "make_normalized",
    "model_normalized",
    "unit_normalized",

    # Derived price
    "unit_price_final",
    "total_price_final",
    "price_basis",

    # Customer
    "matched_customer_id",
    "matched_customer_name",
    "matched_industry",
    "matched_industry_confidence",
    "customer_match_status",
    "customer_match_score",
    "matched_city",
    "matched_state",
    "matched_country",
    "customer_type",
    "regions",

    # Order
    "matched_order_id",
    "matched_rfq_number",
    "matched_order_industry",
    "matched_order_po_number",
    "matched_order_po_value",
    "matched_order_quote_status",
    "order_state",
    "firm_or_budgetary",

    # Time
    "quotation_date_raw",
    "order_date_raw",
    "quotation_date_final",
    "date_source",
    "date_ambiguous",
    "quotation_year",
    "quotation_month",

    # Document
    "document_currency",
]


REVIEW_COLUMNS = [
    "data_source",
    "source_file",
    "source_path",
    "quotation_number",
    "item_no",
    "reason",
]


# ============================================================
# LOADING
# ============================================================

def read_csv(path: Path) -> pd.DataFrame:

    return pd.read_csv(
        path,
        dtype=str,
        keep_default_na=False,
        encoding=CSV_ENCODING,
    )


def check_inputs() -> bool:

    missing = [
        path
        for path in (
            COORDS_ITEMS_CSV,
            CUSTOMER_ENRICHED_COORDS_CSV,
            SALE_ORDERS_CSV,
        )
        if not path.exists()
    ]

    if not missing:
        return True

    print("\nERROR: required input file(s) not found:")

    for path in missing:
        print(f"  {path}")

    return False


# ============================================================
# ROW BUILDER
# ============================================================

def blank_row() -> dict:

    return {
        column: ""
        for column in OUTPUT_COLUMNS
    }


def build_coords_item_row(item, document, order) -> dict:
    """
    One boq_coords item row, joined to its document's customer match and
    (where the document matched a specific Odoo order) that order's own
    fields. Mirrors build_knowledge_bank.py's build_attachment_row().

    `item`     - a row from 03f_structured_coords/quotation_items.csv
    `document` - the matching customer_enriched_coords.csv row (dict, may be {})
    `order`    - the matching sale_orders.csv row (dict, may be {})
    """

    row = blank_row()

    row["data_source"] = "COORDS_ITEM"

    # ---- Lineage ------------------------------------------------------

    row["source_file"] = item.get("source_file", "")
    row["source_path"] = item.get("source_path", "")
    row["quotation_number"] = item.get("quotation_number", "")
    row["item_no"] = item.get("item_no", "")
    row["parent_item_no"] = item.get("parent_item_no", "")
    row["item_level"] = item.get("item_level", "")

    # ---- Item (raw) ---------------------------------------------------

    row["product_name"] = item.get("product_name", "")
    row["description"] = item.get("description_full", "")
    row["make"] = item.get("make", "")
    row["model"] = item.get("model", "")
    row["quantity"] = item.get("quantity", "")
    row["unit"] = item.get("unit", "")
    row["unit_price"] = item.get("unit_price", "")
    row["total_price"] = item.get("total_price", "")
    row["unit_price_raw"] = item.get("unit_price_raw", "")
    row["total_price_raw"] = item.get("total_price_raw", "")
    row["total_price_source"] = item.get("total_price_source", "")
    row["price_status"] = item.get("price_status", "")
    row["currency"] = item.get("currency", "")
    row["item_confidence"] = item.get("confidence", "")
    row["validation_error"] = item.get("validation_error", "")

    # ---- Normalized ---------------------------------------------------

    row["make_normalized"] = normalize_text_value(item.get("make", ""))
    row["model_normalized"] = normalize_text_value(item.get("model", ""))
    row["unit_normalized"] = normalize_unit(item.get("unit", ""))

    # ---- Derived price ------------------------------------------------

    unit_final, total_final, basis = resolve_prices(
        item.get("unit_price", ""),
        item.get("total_price", ""),
        item.get("quantity", ""),
    )

    row["unit_price_final"] = format_number(unit_final)
    row["total_price_final"] = format_number(total_final)
    row["price_basis"] = basis

    # ---- Customer -----------------------------------------------------

    for field in (
        "matched_customer_id",
        "matched_customer_name",
        "matched_industry",
        "matched_industry_confidence",
        "customer_match_status",
        "customer_match_score",
        "matched_city",
        "matched_state",
        "matched_country",
        "customer_type",
        "regions",
    ):
        row[field] = document.get(field, "")

    # ---- Order --------------------------------------------------------

    for field in (
        "matched_order_id",
        "matched_rfq_number",
        "matched_order_industry",
        "matched_order_po_number",
        "matched_order_po_value",
        "matched_order_quote_status",
    ):
        row[field] = document.get(field, "")

    row["order_state"] = order.get("state", "")
    row["firm_or_budgetary"] = order.get("x_studio_firm_or_budgetary", "")

    # ---- Time ---------------------------------------------------------

    document_date_raw = document.get("quotation_date", "")

    order_date_raw = (
        order.get("date_order", "")
        or document.get("matched_order_date", "")
    )

    row["quotation_date_raw"] = document_date_raw
    row["order_date_raw"] = order_date_raw

    iso_date, date_source, ambiguous = resolve_date(
        order_date_raw,
        document_date_raw,
    )

    row["quotation_date_final"] = iso_date
    row["date_source"] = date_source
    row["date_ambiguous"] = "YES" if ambiguous else ""

    if iso_date:
        row["quotation_year"] = iso_date[:4]
        row["quotation_month"] = iso_date[:7]

    # ---- Document -----------------------------------------------------

    row["document_currency"] = document.get("currency", "")

    return row


# ============================================================
# REVIEW FLAGGING
# ============================================================

def to_number(value):

    if value is None:
        return None

    text = str(value).strip()

    if not text or text.lower() in ("nan", "none"):
        return None

    text = text.replace(",", "")

    try:
        return float(text)

    except ValueError:
        return None


def is_negative(value) -> bool:

    number = to_number(value)

    return number is not None and number < 0


def review_reasons(row: dict) -> list[str]:
    """
    Reasons this row deserves a human look. Empty list = nothing to flag.
    Mirrors build_knowledge_bank.py's review_reasons() for the
    ATTACHMENT_ITEM case (this script has no ODOO_ORDER_ONLY rows).
    """

    reasons = []

    if not row["quotation_date_final"]:

        if row["quotation_date_raw"] or row["order_date_raw"]:
            reasons.append("DATE_UNPARSEABLE")

        else:
            reasons.append("DATE_MISSING")

    if row["date_ambiguous"] == "YES":
        reasons.append("DATE_AMBIGUOUS_DAY_MONTH")

    if not row["matched_customer_id"]:
        reasons.append("NO_MATCHED_CUSTOMER")

    has_price = bool(
        row["unit_price_final"]
        or row["total_price_final"]
    )

    if has_price and row["item_confidence"] == "LOW":
        reasons.append("LOW_CONFIDENCE_ITEM_WITH_PRICE")

    if has_price and not row["description"]:
        reasons.append("PRICE_WITHOUT_DESCRIPTION")

    if not has_price:
        reasons.append("NO_PRICE")

    if is_negative(row["unit_price_final"]) or is_negative(
        row["total_price_final"]
    ):
        reasons.append("IMPLAUSIBLE_NEGATIVE_PRICE")

    if row["validation_error"]:
        reasons.append("BOQ_COORDS_VALIDATION_ERROR")

    return reasons


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("KNOWLEDGE BANK BUILDER (boq_coords)")
    print("=" * 70)

    print("\nInput:")
    print(f"  {COORDS_ITEMS_CSV}")
    print(f"  {CUSTOMER_ENRICHED_COORDS_CSV}")
    print(f"  {SALE_ORDERS_CSV}")

    print("\nOutput:")
    print(f"  {OUTPUT_FOLDER}")

    if not check_inputs():
        return 1

    # --------------------------------------------------------
    # LOAD
    # --------------------------------------------------------

    items = read_csv(COORDS_ITEMS_CSV)
    documents = read_csv(CUSTOMER_ENRICHED_COORDS_CSV)
    orders = read_csv(SALE_ORDERS_CSV)

    print(f"\nCoords line items    : {len(items)}")
    print(f"Coords documents     : {len(documents)}")
    print(f"Odoo sale orders     : {len(orders)}")

    documents_by_path = {
        row["source_path"]: row
        for row in documents.to_dict("records")
    }

    orders_by_id = {
        row["id"]: row
        for row in orders.to_dict("records")
    }

    print(f"Documents indexed    : {len(documents_by_path)}")
    print(f"Orders indexed       : {len(orders_by_id)}")

    # --------------------------------------------------------
    # BUILD ROWS
    # --------------------------------------------------------

    output_rows = []
    unmatched_documents = 0

    for item in items.to_dict("records"):

        source_path = item.get("source_path", "")

        document = documents_by_path.get(source_path)

        if document is None:

            document = {}
            unmatched_documents += 1

        order_id = document.get("matched_order_id", "")

        order = orders_by_id.get(order_id, {})

        output_rows.append(
            build_coords_item_row(item, document, order)
        )

    # --------------------------------------------------------
    # REVIEW ROWS
    # --------------------------------------------------------

    review_rows = []

    for row in output_rows:

        reasons = review_reasons(row)

        if not reasons:
            continue

        review_rows.append({
            "data_source": row["data_source"],
            "source_file": row["source_file"],
            "source_path": row["source_path"],
            "quotation_number": row["quotation_number"],
            "item_no": row["item_no"],
            "reason": "; ".join(reasons),
        })

    # --------------------------------------------------------
    # WRITE
    # --------------------------------------------------------

    OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

    output_frame = pd.DataFrame(output_rows, columns=OUTPUT_COLUMNS)

    output_frame.to_csv(ITEMS_CSV, index=False, encoding=CSV_ENCODING)

    review_frame = pd.DataFrame(review_rows, columns=REVIEW_COLUMNS)

    review_frame.to_csv(REVIEW_CSV, index=False, encoding=CSV_ENCODING)

    # --------------------------------------------------------
    # FILL RATES / DISTRIBUTIONS
    # --------------------------------------------------------

    fill_lines = []

    for column in OUTPUT_COLUMNS:

        non_blank = int(
            (output_frame[column].astype(str) != "").sum()
        )

        percentage = (
            (non_blank / len(output_frame) * 100)
            if len(output_frame)
            else 0.0
        )

        fill_lines.append(
            f"  {column:<30} {non_blank:>7}  {percentage:5.1f}%"
        )

    def distribution(column):

        counts = (
            output_frame[column]
            .replace("", "(blank)")
            .value_counts()
        )

        return [
            f"  {str(value):<30} {int(count):>7}"
            for value, count in counts.items()
        ]

    # --------------------------------------------------------
    # SUMMARY FILE
    # --------------------------------------------------------

    with open(SUMMARY_TXT, "w", encoding="utf-8") as file:

        file.write("KNOWLEDGE BANK SUMMARY (boq_coords)\n")
        file.write("=" * 70 + "\n\n")

        file.write(f"Total rows              : {len(output_frame)}\n")
        file.write(f"Source line items       : {len(items)}\n")
        file.write(f"Source documents        : {len(documents)}\n")
        file.write(f"Source sale orders      : {len(orders)}\n")
        file.write(
            f"Items with no document  : {unmatched_documents}\n\n"
        )

        file.write(f"Rows flagged for review : {len(review_rows)}\n\n")

        file.write("DATE SOURCE\n")
        file.write("\n".join(distribution("date_source")) + "\n\n")

        file.write("PRICE BASIS\n")
        file.write("\n".join(distribution("price_basis")) + "\n\n")

        file.write("CUSTOMER MATCH STATUS\n")
        file.write(
            "\n".join(distribution("customer_match_status")) + "\n\n"
        )

        file.write("ITEM CONFIDENCE\n")
        file.write("\n".join(distribution("item_confidence")) + "\n\n")

        file.write("FILL RATES\n")
        file.write("\n".join(fill_lines) + "\n\n")

        file.write("OUTPUTS\n")
        file.write(f"{ITEMS_CSV}\n")
        file.write(f"{REVIEW_CSV}\n")

    # --------------------------------------------------------
    # FINAL CONSOLE (counts only - never a customer name)
    # --------------------------------------------------------

    print("\n")
    print("=" * 70)
    print("KNOWLEDGE BANK COMPLETE (boq_coords)")
    print("=" * 70)

    print(f"Total rows             : {len(output_frame)}")
    print(f"Items with no document : {unmatched_documents}")
    print(f"Flagged for review     : {len(review_rows)}")

    print("\nDate source:")

    for line in distribution("date_source"):
        print(line)

    print("\nPrice basis:")

    for line in distribution("price_basis"):
        print(line)

    print("\nFiles created:")
    print(f"  {ITEMS_CSV}")
    print(f"  {REVIEW_CSV}")
    print(f"  {SUMMARY_TXT}")

    print("\nInput files were not modified.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
