"""
Knowledge Bank Builder
======================

Purpose:
    Join the pipeline's three finished datasets into one flat table at
    line-item grain - the "knowledge bank" the project doc describes.

    What we quoted (item, make/model, quantity, price)
      + who we quoted it to (customer, industry, region)
      + when (Odoo order date, or the date printed on the document)

Input:
    Quotation_Data/03_structured_current/quotation_items.csv
    Quotation_Data/06_customer_matching/customer_enriched.csv
    Quotation_Data/05_odoo_export/sale_orders.csv

Output:
    Quotation_Data/07_knowledge_bank/knowledge_bank_items.csv
    Quotation_Data/07_knowledge_bank/knowledge_bank_review.csv
    Quotation_Data/07_knowledge_bank/knowledge_bank_summary.txt

Design:
    Conservative. Raw values are always preserved beside any derived or
    normalized value, and every derived value records how it was derived
    (`price_basis`, `date_source`) so a computed number can never be
    mistaken for one actually quoted in a document.

    No fuzzy consolidation of makes/models happens here - that needs real
    duplicate clusters to look at first, and this table is what will
    surface them.

Important:
    Progress/summary output is aggregate (counts) only - never a customer
    name - matching the confidentiality discipline used throughout this
    project. Redirect this script's own stdout to a file when running it
    rather than letting it print to a shared terminal/chat.

This script does NOT modify any of its input files.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

from canonicalize import (
    MAKE_ALIASES_CSV,
    MODEL_ALIASES_CSV,
    apply_canonical_columns,
    load_aliases,
)
from product_family import (
    DESCRIPTION_FAMILY_CSV,
    MODEL_FAMILY_CSV,
    apply_product_family_columns,
    load_description_rules,
    load_model_rules,
)


# ============================================================
# CONFIGURATION
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

QUOTATION_ITEMS_CSV = (
    PROJECT_ROOT
    / "Quotation_Data"
    / "03_structured_current"
    / "quotation_items.csv"
)

CUSTOMER_ENRICHED_CSV = (
    PROJECT_ROOT
    / "Quotation_Data"
    / "06_customer_matching"
    / "customer_enriched.csv"
)

SALE_ORDERS_CSV = (
    PROJECT_ROOT
    / "Quotation_Data"
    / "05_odoo_export"
    / "sale_orders.csv"
)

OUTPUT_FOLDER = (
    PROJECT_ROOT
    / "Quotation_Data"
    / "07_knowledge_bank"
)

ITEMS_CSV = (
    OUTPUT_FOLDER / "knowledge_bank_items.csv"
)

REVIEW_CSV = (
    OUTPUT_FOLDER / "knowledge_bank_review.csv"
)

SUMMARY_TXT = (
    OUTPUT_FOLDER / "knowledge_bank_summary.txt"
)

CSV_ENCODING = "utf-8-sig"

# Sanity bounds for a quotation date. Anything outside this is treated as
# a parse failure rather than a real date - a "year" like 1264 (seen in
# the corpus as a mis-parsed value) must not reach the output.
MIN_YEAR = 2015
MAX_YEAR = 2027


# ============================================================
# OUTPUT SCHEMA
# ============================================================
#
# `product` is deliberately NOT carried through: it is populated on 0 of
# the 38,487 rows in quotation_items.csv, so it would be a permanently
# empty column. `version` IS carried (it has real, if rare, values).
# ------------------------------------------------------------

OUTPUT_COLUMNS = [

    # Lineage
    "data_source",
    "source_file",
    "source_path",
    "quotation_number",
    "item_no",

    # Item (raw, as parsed)
    "description",
    "make",
    "model",
    "version",
    "quantity",
    "unit",
    "unit_price",
    "total_price",
    "unit_price_raw",
    "total_price_raw",
    "item_confidence",

    # Normalized (additive - raw always kept above)
    "make_normalized",
    "model_normalized",
    "unit_normalized",

    # Canonical (canonicalize.py - rules + alias tables, basis recorded)
    "make_canonical",
    "make_canonical_basis",
    "model_canonical",
    "model_canonical_basis",
    "unit_class",

    # Product family (product_family.py - rule tables over model/description)
    "product_family",
    "product_family_basis",

    # Derived price
    "unit_price_final",
    "total_price_final",
    "price_basis",

    # Customer
    "matched_customer_id",
    "matched_customer_name",
    "matched_industry",
    "matched_industry_specify_others",
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
    "matched_order_industry_specify_others",
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
    "currency",
    "document_confidence",
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
# NORMALIZATION HELPERS
# ============================================================
#
# Conservative by design: case, whitespace and edge punctuation only.
# Mirrors normalize_spaces() / clean_field_value() in
# quotation_parser_v1.py rather than inventing a different convention.
# ------------------------------------------------------------

def normalize_spaces(text: str) -> str:
    """
    Collapse repeated whitespace (same behaviour as the parser's helper).
    """

    return re.sub(
        r"\s+",
        " ",
        str(text)
    ).strip()


def normalize_text_value(value) -> str:
    """
    Uppercase + whitespace-collapsed + edge-punctuation-stripped form of a
    free-text value, for grouping equivalent spellings of the same make or
    model. Returns "" for blanks.

    Deliberately does NOT attempt fuzzy consolidation (e.g. "SIEMENS AG"
    vs "SIEMENS") - only differences that are purely cosmetic are removed.
    """

    if value is None:
        return ""

    text = normalize_spaces(value)

    if not text:
        return ""

    text = text.strip(" .,:;-|/\\\"'()[]")

    return normalize_spaces(text).upper()


# Canonical unit forms. Keys are the normalized (uppercase, punctuation-
# stripped) spellings actually seen in the corpus; values are the form to
# report. Built from the same unit vocabulary as UNITS_PATTERN in
# quotation_parser_v1.py.

UNIT_CANONICAL = {

    "NO": "NOS",
    "NOS": "NOS",
    "NUMBER": "NOS",
    "NUMBERS": "NOS",

    "PC": "PCS",
    "PCS": "PCS",
    "PIECE": "PCS",
    "PIECES": "PCS",

    "SET": "SET",
    "SETS": "SET",

    "JOB": "JOB",
    "JOBS": "JOB",

    "LOT": "LOT",
    "LOTS": "LOT",

    "UNIT": "UNIT",
    "UNITS": "UNIT",

    "EA": "EACH",
    "EACH": "EACH",

    "KG": "KG",
    "KGS": "KG",
    "GRAM": "GRAM",
    "GRAMS": "GRAM",
    "MG": "MG",
    "TON": "TON",
    "TONS": "TON",

    "L": "LTR",
    "LTR": "LTR",
    "LTRS": "LTR",
    "LITER": "LTR",
    "LITERS": "LTR",
    "LITRE": "LTR",
    "LITRES": "LTR",

    "M": "METER",
    "MTR": "METER",
    "MTRS": "METER",
    "METER": "METER",
    "METERS": "METER",
    "METRE": "METER",
    "METRES": "METER",
    "MM": "MM",
    "CM": "CM",
    "KM": "KM",

    "SQM": "SQM",
    "SQ M": "SQM",
    "SQFT": "SQFT",
    "SQ FT": "SQFT",

    "HOUR": "HOUR",
    "HOURS": "HOUR",
    "HR": "HOUR",
    "HRS": "HOUR",
    "DAY": "DAY",
    "DAYS": "DAY",
    "MONTH": "MONTH",
    "MONTHS": "MONTH",
    "YEAR": "YEAR",
    "YEARS": "YEAR",
}


def normalize_unit(value) -> str:
    """
    Map a unit spelling to a canonical form ("Nos"/"No"/"nos" -> "NOS").

    An unrecognized unit is returned in its normalized (uppercase) form
    rather than dropped - unknown units are still real data.
    """

    normalized = normalize_text_value(value)

    if not normalized:
        return ""

    # "Sq.m" / "Sq. m" -> "SQ M" before lookup.
    lookup = normalize_spaces(
        normalized.replace(".", " ")
    )

    return UNIT_CANONICAL.get(
        lookup,
        normalized
    )


# ============================================================
# NUMBER HELPERS
# ============================================================

def to_number(value):
    """
    Convert an already-parsed CSV value to a float, or None.

    The heavy lifting (currency symbols, Indian "/-" markers, comma
    formats) was already done by quotation_parser_v1.py's parse_number()
    when it wrote the numeric columns - this only has to cope with the
    string round-trip through CSV.
    """

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


def format_number(value):
    """
    Keep numeric values tidy in CSV: 2.0 -> 2, 2.5 -> 2.5, None -> "".
    """

    if value is None:
        return ""

    try:

        if float(value).is_integer():
            return int(value)

    except (TypeError, ValueError, OverflowError):
        return ""

    return round(float(value), 4)


# ============================================================
# DATE RESOLUTION
# ============================================================

MONTH_NAMES = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8, "augest": 8,   # "Augest" is a real corpus typo
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

NUMERIC_DATE_PATTERN = re.compile(
    r"^(\d{1,4})[./-](\d{1,2})[./-](\d{2,4})$"
)

TEXT_DATE_PATTERN = re.compile(
    r"^(\d{1,2})\s+([A-Za-z]+)\s+(\d{2,4})$"
)


def expand_year(year: int) -> int:
    """
    Turn a 2-digit year into a 4-digit one ("26" -> 2026).
    """

    if year >= 100:
        return year

    return 2000 + year


def valid_date_parts(year, month, day):
    """
    True if (year, month, day) is a real calendar date inside the sane
    year range for this corpus.
    """

    if not (MIN_YEAR <= year <= MAX_YEAR):
        return False

    if not (1 <= month <= 12):
        return False

    try:
        pd.Timestamp(year=year, month=month, day=day)

    except (ValueError, OverflowError):
        return False

    return True


def parse_document_date(raw):
    """
    Parse a date as printed on a quotation document.

    The corpus contains 18 distinct raw shapes: "." / "-" / "/" separated,
    ISO-like "YYYY/MM/DD", text months ("17 October 2024"), 2-digit years,
    and at least one US-style M/DD/YYYY row.

    Ambiguity handling: where one component is > 12 the order is decided by
    that alone. Where BOTH could be a month (e.g. "05.06.2025"), day-first
    (DD-MM-YYYY) is assumed - the dominant convention in this corpus and in
    India generally - and the row is flagged ambiguous so the assumption
    stays visible and auditable downstream.

    Returns:
        (iso_date_string, is_ambiguous)  or  ("", False) if unparseable.
    """

    if raw is None:
        return ("", False)

    text = normalize_spaces(raw)

    if not text:
        return ("", False)

    # ---- Text month: "17 October 2024" -------------------------------

    match = TEXT_DATE_PATTERN.match(text)

    if match:

        day = int(match.group(1))

        month = MONTH_NAMES.get(
            match.group(2).lower()
        )

        year = expand_year(
            int(match.group(3))
        )

        if month and valid_date_parts(year, month, day):

            return (
                f"{year:04d}-{month:02d}-{day:02d}",
                False
            )

        return ("", False)

    # ---- Numeric forms ------------------------------------------------

    match = NUMERIC_DATE_PATTERN.match(text)

    if not match:
        return ("", False)

    first = int(match.group(1))
    second = int(match.group(2))
    third = int(match.group(3))

    # ISO-like "2025/07/09" - a 4-digit leading value is unambiguous.
    if first >= 1000:

        if valid_date_parts(first, second, third):

            return (
                f"{first:04d}-{second:02d}-{third:02d}",
                False
            )

        return ("", False)

    year = expand_year(third)

    day_first_ok = valid_date_parts(year, second, first)
    month_first_ok = valid_date_parts(year, first, second)

    # Unambiguous: only one reading is a real date (e.g. day > 12).
    if day_first_ok and not month_first_ok:

        return (
            f"{year:04d}-{second:02d}-{first:02d}",
            False
        )

    if month_first_ok and not day_first_ok:

        return (
            f"{year:04d}-{first:02d}-{second:02d}",
            False
        )

    # Both readings valid -> assume day-first, flag as ambiguous.
    if day_first_ok:

        return (
            f"{year:04d}-{second:02d}-{first:02d}",
            True
        )

    return ("", False)


def parse_odoo_date(raw):
    """
    Odoo writes an ISO datetime ("2025-06-05 09:12:31"). Take the date
    part, with the same sanity-range check applied.
    """

    if raw is None:
        return ""

    text = normalize_spaces(raw)

    if not text:
        return ""

    match = re.match(
        r"^(\d{4})-(\d{2})-(\d{2})",
        text
    )

    if not match:
        return ""

    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3))

    if not valid_date_parts(year, month, day):
        return ""

    return f"{year:04d}-{month:02d}-{day:02d}"


def resolve_date(order_date_raw, document_date_raw):
    """
    Pick the time dimension for a row.

    Odoo's `date_order` is structured and reliable, so it wins where the
    document was matched to a specific order. The regex-extracted document
    date is the fallback.

    Returns:
        (iso_date, date_source, is_ambiguous)
    """

    odoo_iso = parse_odoo_date(order_date_raw)

    if odoo_iso:
        return (odoo_iso, "ODOO_ORDER", False)

    parsed_iso, ambiguous = parse_document_date(
        document_date_raw
    )

    if parsed_iso:
        return (parsed_iso, "PARSED_DOC", ambiguous)

    return ("", "NONE", False)


# ============================================================
# PRICE RESOLUTION
# ============================================================

def resolve_prices(unit_price, total_price, quantity):
    """
    Fill in whichever of unit/total price is missing, when quantity makes
    it derivable.

    `unit_price` (30% populated) and `total_price` (49%) are sparse but
    complementary in this corpus, so this materially improves coverage -
    but a derived figure is never silently presented as a quoted one:
    `price_basis` always records what happened.

    Returns:
        (unit_price_final, total_price_final, price_basis)
    """

    unit_value = to_number(unit_price)
    total_value = to_number(total_price)
    quantity_value = to_number(quantity)

    has_usable_quantity = (
        quantity_value is not None
        and quantity_value > 0
    )

    if unit_value is not None and total_value is not None:
        return (unit_value, total_value, "REPORTED")

    if unit_value is not None:

        if has_usable_quantity:

            return (
                unit_value,
                unit_value * quantity_value,
                "DERIVED_FROM_UNIT"
            )

        return (unit_value, None, "REPORTED")

    if total_value is not None:

        if has_usable_quantity:

            return (
                total_value / quantity_value,
                total_value,
                "DERIVED_FROM_TOTAL"
            )

        return (None, total_value, "REPORTED")

    return (None, None, "NONE")


# ============================================================
# LOADING
# ============================================================

def read_csv(path: Path) -> pd.DataFrame:
    """
    Read a pipeline CSV as all-strings (no type inference), so that IDs
    and numbers keep their exact written form until this script decides
    to convert them.
    """

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
            QUOTATION_ITEMS_CSV,
            CUSTOMER_ENRICHED_CSV,
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
# ROW BUILDERS
# ============================================================

def blank_row() -> dict:
    """
    An output row with every column present and empty - so both row kinds
    always write an identical column set.
    """

    return {
        column: ""
        for column in OUTPUT_COLUMNS
    }


def build_attachment_row(item, document, order) -> dict:
    """
    One parsed BOQ line item, joined to its document's customer match and
    (where the document matched a specific Odoo order) that order's own
    fields.

    `item`     - a row from quotation_items.csv
    `document` - the matching customer_enriched.csv row (dict, may be {})
    `order`    - the matching sale_orders.csv row (dict, may be {})
    """

    row = blank_row()

    row["data_source"] = "ATTACHMENT_ITEM"

    # ---- Lineage ------------------------------------------------------

    row["source_file"] = item.get("source_file", "")
    row["source_path"] = item.get("source_path", "")
    row["quotation_number"] = item.get("quotation_number", "")
    row["item_no"] = item.get("item_no", "")

    # ---- Item (raw) ---------------------------------------------------

    row["description"] = item.get("description", "")
    row["make"] = item.get("make", "")
    row["model"] = item.get("model", "")
    row["version"] = item.get("version", "")
    row["quantity"] = item.get("quantity", "")
    row["unit"] = item.get("unit", "")
    row["unit_price"] = item.get("unit_price", "")
    row["total_price"] = item.get("total_price", "")
    row["unit_price_raw"] = item.get("unit_price_raw", "")
    row["total_price_raw"] = item.get("total_price_raw", "")
    row["item_confidence"] = item.get("confidence", "")

    # ---- Normalized ---------------------------------------------------

    row["make_normalized"] = normalize_text_value(
        item.get("make", "")
    )

    row["model_normalized"] = normalize_text_value(
        item.get("model", "")
    )

    row["unit_normalized"] = normalize_unit(
        item.get("unit", "")
    )

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
        "matched_industry_specify_others",
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
        "matched_order_industry_specify_others",
        "matched_order_po_number",
        "matched_order_po_value",
        "matched_order_quote_status",
    ):
        row[field] = document.get(field, "")

    row["order_state"] = order.get("state", "")

    row["firm_or_budgetary"] = order.get(
        "x_studio_firm_or_budgetary",
        ""
    )

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

    row["currency"] = document.get("currency", "")

    row["document_confidence"] = document.get(
        "document_confidence",
        ""
    )

    return row


def build_order_only_row(order, industry_proxy_name="") -> dict:
    """
    One Odoo sale order that no parsed document resolved to.

    Item-level fields stay blank by design - this row exists so customer
    and industry coverage reflects the real business, not only the subset
    of quotes that happened to have a parseable attachment.
    """

    row = blank_row()

    row["data_source"] = "ODOO_ORDER_ONLY"

    row["quotation_number"] = order.get(
        "x_studio_internal_rfq_assignment_number",
        ""
    )

    # ---- Customer -----------------------------------------------------

    row["matched_customer_id"] = order.get("partner_id_id", "")
    row["matched_customer_name"] = order.get("partner_id_name", "")
    row["matched_industry"] = order.get("x_studio_type_of_industry", "")
    row["matched_industry_specify_others"] = order.get(
        "x_studio_specify_others",
        ""
    )
    row["customer_type"] = order.get("x_studio_customer_type", "")
    row["regions"] = order.get("x_studio_responsible_region", "")
    row["customer_match_status"] = "ODOO_ONLY"

    # ---- Order --------------------------------------------------------

    row["matched_order_id"] = order.get("id", "")

    row["matched_rfq_number"] = order.get(
        "x_studio_internal_rfq_assignment_number",
        ""
    )

    row["matched_order_industry"] = order.get(
        "x_studio_type_of_industry",
        ""
    )

    row["matched_order_industry_specify_others"] = order.get(
        "x_studio_specify_others",
        ""
    )

    row["matched_order_po_number"] = order.get("x_studio_po_number", "")
    row["matched_order_po_value"] = order.get("x_studio_po_value", "")

    row["matched_order_quote_status"] = order.get(
        "x_studio_quote_status",
        ""
    )

    row["order_state"] = order.get("state", "")

    row["firm_or_budgetary"] = order.get(
        "x_studio_firm_or_budgetary",
        ""
    )

    # ---- Time ---------------------------------------------------------

    order_date_raw = order.get("date_order", "")

    row["order_date_raw"] = order_date_raw

    iso_date, date_source, ambiguous = resolve_date(
        order_date_raw,
        "",
    )

    row["quotation_date_final"] = iso_date
    row["date_source"] = date_source
    row["date_ambiguous"] = "YES" if ambiguous else ""

    if iso_date:
        row["quotation_year"] = iso_date[:4]
        row["quotation_month"] = iso_date[:7]

    row["price_basis"] = "NONE"

    return row


# ============================================================
# REVIEW FLAGGING
# ============================================================

def is_negative(value) -> bool:
    """
    True if an output price cell holds a value below zero.
    """

    number = to_number(value)

    return number is not None and number < 0


def review_reasons(row: dict) -> list[str]:
    """
    Reasons this row deserves a human look. Empty list = nothing to flag.
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

    if row["data_source"] == "ATTACHMENT_ITEM":

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

        # A negative price is never legitimate here. These come from the
        # upstream parser digit-stripping prose into a number (e.g.
        # "CAPACITY) QTY-2" -> -2), not from this join - flagged so the
        # rows are excluded from any price analysis until fixed at source.
        if is_negative(row["unit_price_final"]) or is_negative(
            row["total_price_final"]
        ):
            reasons.append("IMPLAUSIBLE_NEGATIVE_PRICE")

    return reasons


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("KNOWLEDGE BANK BUILDER")
    print("=" * 70)

    print("\nInput:")
    print(f"  {QUOTATION_ITEMS_CSV}")
    print(f"  {CUSTOMER_ENRICHED_CSV}")
    print(f"  {SALE_ORDERS_CSV}")

    print("\nOutput:")
    print(f"  {OUTPUT_FOLDER}")

    if not check_inputs():
        return 1

    # --------------------------------------------------------
    # LOAD
    # --------------------------------------------------------

    items = read_csv(QUOTATION_ITEMS_CSV)
    documents = read_csv(CUSTOMER_ENRICHED_CSV)
    orders = read_csv(SALE_ORDERS_CSV)

    print(f"\nQuotation line items : {len(items)}")
    print(f"Quotation documents  : {len(documents)}")
    print(f"Odoo sale orders     : {len(orders)}")

    # --------------------------------------------------------
    # INDEX THE JOIN TARGETS
    #
    # source_path is the documented unique key (filenames collide
    # across ~2% of the corpus, full paths do not).
    # --------------------------------------------------------

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
    # ORDERS ALREADY REPRESENTED BY A PARSED DOCUMENT
    #
    # Taken from the DOCUMENT table, not from the item loop below: a
    # document can match an Odoo order and still yield zero parsed line
    # items (no BOQ table detected). Deriving this set from the items
    # would leave those orders looking unlinked, and they would then be
    # written a second time as ODOO_ORDER_ONLY rows - double-counting
    # the same order.
    # --------------------------------------------------------

    linked_order_ids = {
        row["matched_order_id"]
        for row in documents_by_path.values()
        if row.get("matched_order_id", "")
        and row["matched_order_id"] in orders_by_id
    }

    print(f"Orders linked to docs: {len(linked_order_ids)}")

    # --------------------------------------------------------
    # BUILD ATTACHMENT ITEM ROWS
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
            build_attachment_row(
                item,
                document,
                order,
            )
        )

    attachment_row_count = len(output_rows)

    # --------------------------------------------------------
    # BUILD ODOO-ONLY ORDER ROWS
    # --------------------------------------------------------

    for order in orders.to_dict("records"):

        if order["id"] in linked_order_ids:
            continue

        output_rows.append(
            build_order_only_row(order)
        )

    order_only_row_count = (
        len(output_rows) - attachment_row_count
    )

    # --------------------------------------------------------
    # CANONICAL MAKE / MODEL / UNIT CLASS
    #
    # Runs over all rows at once: display spellings are the most
    # frequent spelling across the whole corpus. ODOO_ORDER_ONLY rows
    # have blank make/model/unit, so their canonical columns stay blank.
    # --------------------------------------------------------

    make_aliases = load_aliases(MAKE_ALIASES_CSV)
    model_aliases = load_aliases(MODEL_ALIASES_CSV)

    print(f"Make aliases loaded   : {len(make_aliases)}")
    print(f"Model aliases loaded  : {len(model_aliases)}")

    apply_canonical_columns(output_rows, make_aliases, model_aliases)

    model_family_rules = load_model_rules(MODEL_FAMILY_CSV)
    description_family_rules = load_description_rules(DESCRIPTION_FAMILY_CSV)

    print(f"Model family rules loaded       : {len(model_family_rules)}")
    print(f"Description family rules loaded : {len(description_family_rules)}")

    apply_product_family_columns(output_rows, model_family_rules, description_family_rules)

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

    output_frame = pd.DataFrame(
        output_rows,
        columns=OUTPUT_COLUMNS,
    )

    output_frame.to_csv(
        ITEMS_CSV,
        index=False,
        encoding=CSV_ENCODING,
    )

    review_frame = pd.DataFrame(
        review_rows,
        columns=REVIEW_COLUMNS,
    )

    review_frame.to_csv(
        REVIEW_CSV,
        index=False,
        encoding=CSV_ENCODING,
    )

    # --------------------------------------------------------
    # FILL RATES / DISTRIBUTIONS
    # --------------------------------------------------------

    attachment_frame = output_frame[
        output_frame["data_source"] == "ATTACHMENT_ITEM"
    ]

    fill_lines = []

    for column in OUTPUT_COLUMNS:

        non_blank = int(
            (attachment_frame[column].astype(str) != "").sum()
        )

        percentage = (
            (non_blank / len(attachment_frame) * 100)
            if len(attachment_frame)
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

    with open(
        SUMMARY_TXT,
        "w",
        encoding="utf-8"
    ) as file:

        file.write("KNOWLEDGE BANK SUMMARY\n")
        file.write("=" * 70 + "\n\n")

        file.write(f"Total rows              : {len(output_frame)}\n")
        file.write(f"  ATTACHMENT_ITEM rows  : {attachment_row_count}\n")
        file.write(f"  ODOO_ORDER_ONLY rows  : {order_only_row_count}\n\n")

        file.write(f"Source line items       : {len(items)}\n")
        file.write(f"Source documents        : {len(documents)}\n")
        file.write(f"Source sale orders      : {len(orders)}\n")
        file.write(f"Orders linked to a doc  : {len(linked_order_ids)}\n")
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

        file.write("ITEM CONFIDENCE (attachment rows)\n")
        file.write(
            "\n".join(distribution("item_confidence")) + "\n\n"
        )

        file.write(
            "FILL RATES (ATTACHMENT_ITEM rows only)\n"
        )
        file.write("\n".join(fill_lines) + "\n\n")

        file.write("OUTPUTS\n")
        file.write(f"{ITEMS_CSV}\n")
        file.write(f"{REVIEW_CSV}\n")

    # --------------------------------------------------------
    # FINAL CONSOLE (counts only - never a customer name)
    # --------------------------------------------------------

    print("\n")
    print("=" * 70)
    print("KNOWLEDGE BANK COMPLETE")
    print("=" * 70)

    print(f"Total rows             : {len(output_frame)}")
    print(f"  ATTACHMENT_ITEM      : {attachment_row_count}")
    print(f"  ODOO_ORDER_ONLY      : {order_only_row_count}")
    print(f"Orders linked to a doc : {len(linked_order_ids)}")
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
