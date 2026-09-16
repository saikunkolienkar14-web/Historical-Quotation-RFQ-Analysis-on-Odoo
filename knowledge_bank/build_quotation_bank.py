"""
Quotation Bank Builder
======================

Purpose:
    Roll `knowledge_bank_items.csv` up from line-item grain to
    quotation grain - one row per distinct quotation, answering "what did
    we quote on Q24AKIC10077, to whom, when, and for how much" directly,
    instead of requiring a group-by over the item table every time.

Input:
    Quotation_Data/07_knowledge_bank/knowledge_bank_items.csv
    Quotation_Data/07_knowledge_bank/knowledge_bank_review.csv

Output:
    Quotation_Data/07_knowledge_bank/knowledge_bank_quotations.csv
    Quotation_Data/07_knowledge_bank/knowledge_bank_quotations_summary.txt

Why not key on `quotation_number` directly:
    Measured against the corpus, `quotation_number` is ~95% populated but
    NOT unique (105 distinct values are shared by 237 documents) and about
    a fifth of its non-blank values are junk - a bare "Ref:" label in the
    source PDFs matches lines like "Ref: Email" or "Ref: Verbal" just as
    happily as a real quotation number, and quotation_parser_v1.py's
    dash-splitting rule (Ref: Q24250574-SQ2503N052 -> keep only the part
    after the last dash) turns legitimate sub-quote numbers like
    "2526W029R2-1" / "2526W029R2-2" into bare "1" / "2".

    So this script derives its own `quotation_key`: the normalized
    quotation number when it looks trustworthy (has a digit, is long
    enough, isn't a known junk value), and a fallback keyed to the source
    document otherwise. `quotation_key_basis` always records which
    happened, so a document-fallback key is never mistaken for a real
    quotation number.

Design:
    Conservative, matching build_knowledge_bank.py's discipline: raw
    quotation_number is always kept beside the derived key, and every
    aggregated money figure records its basis (`quoted_value_basis`) so a
    partial or derived total is never mistaken for a complete, as-quoted
    figure.

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


# ============================================================
# CONFIGURATION
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ITEMS_CSV = (
    PROJECT_ROOT
    / "Quotation_Data"
    / "07_knowledge_bank"
    / "knowledge_bank_items.csv"
)

REVIEW_CSV = (
    PROJECT_ROOT
    / "Quotation_Data"
    / "07_knowledge_bank"
    / "knowledge_bank_review.csv"
)

OUTPUT_FOLDER = (
    PROJECT_ROOT
    / "Quotation_Data"
    / "07_knowledge_bank"
)

QUOTATIONS_CSV = (
    OUTPUT_FOLDER / "knowledge_bank_quotations.csv"
)

SUMMARY_TXT = (
    OUTPUT_FOLDER / "knowledge_bank_quotations_summary.txt"
)

CSV_ENCODING = "utf-8-sig"

# A quotation_number shorter than this, or with no digit at all, is
# treated as unreliable - real numbers in this corpus are things like
# "Q24AKIC10077" or "2425W034R2", never bare short tokens.
MIN_TRUSTWORTHY_LENGTH = 6

# Values seen in the corpus that are NOT quotation numbers - they leaked
# in because quotation_parser_v1.py's QUOTATION_NUMBER_LABELS includes a
# bare "ref" label, which matches "Ref: Email" / "Ref: Verbal" etc. just
# as happily as a real "Ref: Q24AKIC10077".
JUNK_QUOTATION_NUMBERS = {
    "EMAIL", "VERBAL", "TENDER", "BID", "SITE", "DATE", "RFQ",
    "DETAILS", "SUGGESTED", "COMMERCIAL", "FOR", "XXXXXXXXX",
    "R0", "R1", "R2", "R3", "R4", "R5",
}

# How many distinct makes/models to list by name in one cell before
# collapsing the rest into "+N more" - keeps the cell readable for
# quotations with many line items.
MAX_LISTED_VALUES = 10


# ============================================================
# KEY NORMALIZATION
# ============================================================
#
# Mirrors normalize_rfq_number() in odoo_match_customer/match_customers.py
# so the same string maps to the same key everywhere in the pipeline.
# ------------------------------------------------------------

def normalize_rfq_number(value) -> str:
    """
    Uppercase, whitespace-removed form of a quotation/RFQ number, for
    exact comparison. Deliberately no fuzzy scoring - quotation numbers
    are single alphanumeric tokens.
    """

    if value is None:
        return ""

    return re.sub(
        r"\s+",
        "",
        str(value).strip()
    ).upper()


def is_trustworthy_quotation_number(normalized: str) -> bool:
    """
    True if a normalized quotation_number looks like a real quotation
    number rather than parser leakage (see JUNK_QUOTATION_NUMBERS and the
    module docstring).
    """

    if not normalized:
        return False

    if len(normalized) < MIN_TRUSTWORTHY_LENGTH:
        return False

    if not any(character.isdigit() for character in normalized):
        return False

    if normalized in JUNK_QUOTATION_NUMBERS:
        return False

    return True


def build_quotation_key(row: dict) -> tuple[str, str]:
    """
    Returns (quotation_key, quotation_key_basis) for one knowledge-bank
    row (either data_source).

    A trustworthy quotation_number is used directly. Otherwise the key
    falls back to the source document, so distinct documents with a
    shared junk quotation_number (e.g. 12 different documents all
    carrying "Ref: Email") are never fused into one quotation.
    """

    normalized = normalize_rfq_number(
        row.get("quotation_number", "")
    )

    if is_trustworthy_quotation_number(normalized):
        return (normalized, "QUOTATION_NUMBER")

    # ODOO_ORDER_ONLY rows have no source_file - fall back to the Odoo
    # order id instead, so Odoo-only quotes still get a stable key.
    source_file = row.get("source_file", "")

    if source_file:
        return (f"DOC::{source_file}", "DOCUMENT_FALLBACK")

    order_id = row.get("matched_order_id", "")

    if order_id:
        return (f"ORDER::{order_id}", "ORDER_FALLBACK")

    return ("", "NONE")


# ============================================================
# NUMBER HELPERS
# ============================================================

def to_number(value):
    """
    Convert an already-parsed CSV value to a float, or None.
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


def first_non_blank(series: pd.Series) -> str:
    """
    First non-blank value in a column across a group's rows, or "".
    """

    for value in series:

        text = str(value).strip()

        if text and text.lower() != "nan":
            return text

    return ""


def distinct_non_blank(series: pd.Series) -> list[str]:
    """
    Distinct non-blank, whitespace-stripped values from a column across a
    group's rows, in first-seen order.
    """

    seen = []
    seen_set = set()

    for value in series:

        text = str(value).strip()

        if not text or text.lower() == "nan":
            continue

        if text in seen_set:
            continue

        seen_set.add(text)
        seen.append(text)

    return seen


def join_capped(values: list[str], limit: int = MAX_LISTED_VALUES) -> str:
    """
    "; "-join a list of distinct values, capped so the cell stays
    readable on quotations with many line items.
    """

    if not values:
        return ""

    values = sorted(values)

    if len(values) <= limit:
        return "; ".join(values)

    shown = values[:limit]
    remaining = len(values) - limit

    return "; ".join(shown) + f"; …+{remaining} more"


# ============================================================
# LOADING
# ============================================================

def read_csv(path: Path) -> pd.DataFrame:
    """
    Read a pipeline CSV as all-strings (no type inference).
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
        for path in (ITEMS_CSV, REVIEW_CSV)
        if not path.exists()
    ]

    if not missing:
        return True

    print("\nERROR: required input file(s) not found:")

    for path in missing:
        print(f"  {path}")

    print(
        "\nRun knowledge_bank/build_knowledge_bank.py first."
    )

    return False


# ============================================================
# PER-QUOTATION AGGREGATION
# ============================================================

def aggregate_price(group: pd.DataFrame) -> tuple:
    """
    Sum item prices for one quotation's ATTACHMENT_ITEM rows.

    Only items with a positive, priced total contribute -
    IMPLAUSIBLE_NEGATIVE_PRICE rows (upstream parser prose-stripping bug,
    see PROJECT_NOTES.md) are excluded and counted separately rather than
    silently summed.

    Returns:
        (quoted_value_total, quoted_value_basis, n_items_priced,
         n_items_excluded_negative)
    """

    total = 0.0
    n_priced = 0
    n_excluded_negative = 0
    bases_used = set()

    for _, row in group.iterrows():

        value = to_number(
            row.get("total_price_final", "")
        )

        basis = row.get("price_basis", "")

        if value is None:
            continue

        if value < 0:
            n_excluded_negative += 1
            continue

        total += value
        n_priced += 1

        if basis:
            bases_used.add(basis)

    n_items = len(group)

    if n_priced == 0:
        return (None, "NONE", 0, n_excluded_negative)

    if n_priced < n_items:
        value_basis = "PARTIAL"

    elif bases_used == {"REPORTED"}:
        value_basis = "REPORTED"

    else:
        value_basis = "MIXED_DERIVED"

    return (total, value_basis, n_priced, n_excluded_negative)


def pick_best_scalar(group: pd.DataFrame, field: str) -> str:
    """
    Pick one representative value for a scalar field across a
    quotation's rows: prefer a row whose date_source is ODOO_ORDER
    (structured, reliable), then the row with the highest
    document_confidence, then the first non-blank value.
    """

    confidence_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "": 0}

    def sort_key(row):

        odoo_dated = 1 if row.get("date_source", "") == "ODOO_ORDER" else 0

        confidence = confidence_rank.get(
            row.get("document_confidence", ""),
            0
        )

        has_value = 1 if str(row.get(field, "")).strip() else 0

        return (odoo_dated, confidence, has_value)

    best_row = None
    best_key = None

    for _, row in group.iterrows():

        value = str(row.get(field, "")).strip()

        if not value or value.lower() == "nan":
            continue

        key = sort_key(row)

        if best_key is None or key > best_key:
            best_key = key
            best_row = row

    if best_row is None:
        return ""

    return str(best_row.get(field, "")).strip()


def build_quotation_row(
    quotation_key: str,
    quotation_key_basis: str,
    group: pd.DataFrame,
    review_reasons_by_path: dict,
) -> dict:
    """
    One output row: the rollup of every knowledge-bank row sharing
    `quotation_key`.
    """

    data_sources = set(group["data_source"])

    if data_sources == {"ODOO_ORDER_ONLY"}:
        data_source = "ODOO_ORDER_ONLY"

    elif "ATTACHMENT_ITEM" in data_sources:
        data_source = "ATTACHMENT_ITEM"

    else:
        data_source = first_non_blank(group["data_source"])

    item_rows = group[group["data_source"] == "ATTACHMENT_ITEM"]

    n_items = len(item_rows)

    (
        quoted_value_total,
        quoted_value_basis,
        n_items_priced,
        n_items_excluded_negative,
    ) = aggregate_price(item_rows)

    pct_items_priced = (
        round(n_items_priced / n_items * 100, 1)
        if n_items
        else 0.0
    )

    confidence_counts = item_rows["item_confidence"].value_counts()

    review_reasons = set()

    for source_path in distinct_non_blank(group["source_path"]):

        review_reasons.update(
            review_reasons_by_path.get(source_path, [])
        )

    row = {
        "quotation_key": quotation_key,
        "quotation_key_basis": quotation_key_basis,
        "quotation_number": pick_best_scalar(group, "quotation_number"),
        "data_source": data_source,
        "n_source_documents": len(
            distinct_non_blank(group["source_path"])
        ) or len(
            distinct_non_blank(group["source_file"])
        ),
        "source_files": join_capped(
            distinct_non_blank(group["source_file"])
        ),

        "matched_customer_id": pick_best_scalar(group, "matched_customer_id"),
        "matched_customer_name": pick_best_scalar(group, "matched_customer_name"),
        "matched_industry": pick_best_scalar(group, "matched_industry"),
        "customer_match_status": pick_best_scalar(group, "customer_match_status"),
        "matched_state": pick_best_scalar(group, "matched_state"),
        "matched_country": pick_best_scalar(group, "matched_country"),
        "customer_type": pick_best_scalar(group, "customer_type"),
        "regions": pick_best_scalar(group, "regions"),

        "matched_order_id": pick_best_scalar(group, "matched_order_id"),
        "matched_rfq_number": pick_best_scalar(group, "matched_rfq_number"),
        "matched_order_po_number": pick_best_scalar(group, "matched_order_po_number"),
        "matched_order_po_value": pick_best_scalar(group, "matched_order_po_value"),
        "matched_order_quote_status": pick_best_scalar(group, "matched_order_quote_status"),
        "order_state": pick_best_scalar(group, "order_state"),
        "firm_or_budgetary": pick_best_scalar(group, "firm_or_budgetary"),

        "quotation_date_final": pick_best_scalar(group, "quotation_date_final"),
        "date_source": pick_best_scalar(group, "date_source"),
        "date_ambiguous": pick_best_scalar(group, "date_ambiguous"),
        "quotation_year": pick_best_scalar(group, "quotation_year"),
        "quotation_month": pick_best_scalar(group, "quotation_month"),

        "n_items": n_items,
        "n_items_priced": n_items_priced,
        "pct_items_priced": pct_items_priced,
        "quoted_value_total": format_number(quoted_value_total),
        "quoted_value_basis": quoted_value_basis,
        "currency": pick_best_scalar(group, "currency"),

        "n_distinct_makes": len(
            distinct_non_blank(item_rows["make_canonical"])
        ),
        "makes_quoted": join_capped(
            distinct_non_blank(item_rows["make_canonical"])
        ),
        "n_distinct_models": len(
            distinct_non_blank(item_rows["model_canonical"])
        ),
        "models_quoted": join_capped(
            distinct_non_blank(item_rows["model_canonical"])
        ),

        "document_confidence": pick_best_scalar(group, "document_confidence"),
        "n_items_high": int(confidence_counts.get("HIGH", 0)),
        "n_items_medium": int(confidence_counts.get("MEDIUM", 0)),
        "n_items_low": int(confidence_counts.get("LOW", 0)),
        "n_items_excluded_negative": n_items_excluded_negative,
        "review_flags": "; ".join(sorted(review_reasons)),
    }

    return row


OUTPUT_COLUMNS = [
    "quotation_key",
    "quotation_key_basis",
    "quotation_number",
    "data_source",
    "n_source_documents",
    "source_files",

    "matched_customer_id",
    "matched_customer_name",
    "matched_industry",
    "customer_match_status",
    "matched_state",
    "matched_country",
    "customer_type",
    "regions",

    "matched_order_id",
    "matched_rfq_number",
    "matched_order_po_number",
    "matched_order_po_value",
    "matched_order_quote_status",
    "order_state",
    "firm_or_budgetary",

    "quotation_date_final",
    "date_source",
    "date_ambiguous",
    "quotation_year",
    "quotation_month",

    "n_items",
    "n_items_priced",
    "pct_items_priced",
    "quoted_value_total",
    "quoted_value_basis",
    "currency",

    "n_distinct_makes",
    "makes_quoted",
    "n_distinct_models",
    "models_quoted",

    "document_confidence",
    "n_items_high",
    "n_items_medium",
    "n_items_low",
    "n_items_excluded_negative",
    "review_flags",
]


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("QUOTATION BANK BUILDER")
    print("=" * 70)

    print("\nInput:")
    print(f"  {ITEMS_CSV}")
    print(f"  {REVIEW_CSV}")

    print("\nOutput:")
    print(f"  {OUTPUT_FOLDER}")

    if not check_inputs():
        return 1

    # --------------------------------------------------------
    # LOAD
    # --------------------------------------------------------

    items = read_csv(ITEMS_CSV)
    review = read_csv(REVIEW_CSV)

    print(f"\nKnowledge-bank rows  : {len(items)}")
    print(f"Review rows          : {len(review)}")

    review_reasons_by_path: dict = {}

    for row in review.to_dict("records"):

        source_path = row.get("source_path", "")

        if not source_path:
            continue

        reasons = [
            reason.strip()
            for reason in row.get("reason", "").split(";")
            if reason.strip()
        ]

        review_reasons_by_path.setdefault(
            source_path, []
        ).extend(reasons)

    # --------------------------------------------------------
    # DERIVE quotation_key FOR EVERY ROW
    # --------------------------------------------------------

    records = items.to_dict("records")

    key_basis_counts = {
        "QUOTATION_NUMBER": 0,
        "DOCUMENT_FALLBACK": 0,
        "ORDER_FALLBACK": 0,
        "NONE": 0,
    }

    groups: dict = {}

    for record in records:

        key, basis = build_quotation_key(record)

        key_basis_counts[basis] = key_basis_counts.get(basis, 0) + 1

        if not key:
            continue

        groups.setdefault(key, []).append(record)

    print(f"Distinct quotation keys: {len(groups)}")

    dropped_no_key = key_basis_counts.get("NONE", 0)

    if dropped_no_key:
        print(
            f"Rows with no derivable key (dropped): {dropped_no_key}"
        )

    # --------------------------------------------------------
    # BUILD OUTPUT ROWS
    # --------------------------------------------------------

    output_rows = []

    for key, rows in groups.items():

        group_df = pd.DataFrame(rows)

        # All rows in a group share the same basis by construction -
        # recompute once from the first row rather than storing it.
        _, basis = build_quotation_key(rows[0])

        output_rows.append(
            build_quotation_row(
                key,
                basis,
                group_df,
                review_reasons_by_path,
            )
        )

    # --------------------------------------------------------
    # WRITE
    # --------------------------------------------------------

    OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

    output_frame = pd.DataFrame(
        output_rows,
        columns=OUTPUT_COLUMNS,
    )

    output_frame.sort_values(
        by="quotation_key"
    ).to_csv(
        QUOTATIONS_CSV,
        index=False,
        encoding=CSV_ENCODING,
    )

    # --------------------------------------------------------
    # VERIFICATION
    # --------------------------------------------------------

    total_items_in_output = int(output_frame["n_items"].sum())

    total_attachment_items = int(
        (items["data_source"] == "ATTACHMENT_ITEM").sum()
    )

    item_count_matches = (
        total_items_in_output == total_attachment_items
    )

    duplicate_keys = int(
        output_frame["quotation_key"].duplicated().sum()
    )

    blank_keys = int(
        (output_frame["quotation_key"].astype(str).str.strip() == "").sum()
    )

    negative_totals = int(
        (
            pd.to_numeric(
                output_frame["quoted_value_total"],
                errors="coerce"
            ) < 0
        ).sum()
    )

    multi_document_quotations = int(
        (output_frame["n_source_documents"] > 1).sum()
    )

    # --------------------------------------------------------
    # DISTRIBUTIONS
    # --------------------------------------------------------

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

    def fill_rate_lines():

        lines = []

        for column in OUTPUT_COLUMNS:

            non_blank = int(
                (output_frame[column].astype(str) != "").sum()
            )

            percentage = (
                (non_blank / len(output_frame) * 100)
                if len(output_frame)
                else 0.0
            )

            lines.append(
                f"  {column:<30} {non_blank:>7}  {percentage:5.1f}%"
            )

        return lines

    # --------------------------------------------------------
    # SUMMARY FILE
    # --------------------------------------------------------

    with open(
        SUMMARY_TXT,
        "w",
        encoding="utf-8"
    ) as file:

        file.write("QUOTATION BANK SUMMARY\n")
        file.write("=" * 70 + "\n\n")

        file.write(f"Total quotations         : {len(output_frame)}\n")
        file.write(
            f"Source knowledge-bank rows: {len(items)}\n"
        )
        file.write(
            f"Rows with no derivable key (dropped): {dropped_no_key}\n\n"
        )

        file.write("QUOTATION_KEY_BASIS\n")

        for basis, count in key_basis_counts.items():
            file.write(f"  {basis:<30} {count:>7}\n")

        file.write("\n")

        file.write("DATA_SOURCE\n")
        file.write("\n".join(distribution("data_source")) + "\n\n")

        file.write("QUOTED_VALUE_BASIS\n")
        file.write("\n".join(distribution("quoted_value_basis")) + "\n\n")

        file.write("CUSTOMER_MATCH_STATUS\n")
        file.write(
            "\n".join(distribution("customer_match_status")) + "\n\n"
        )

        file.write("QUOTATION_YEAR\n")
        file.write("\n".join(distribution("quotation_year")) + "\n\n")

        file.write("VERIFICATION\n")
        file.write(
            f"  Items summed across quotations matches "
            f"ATTACHMENT_ITEM row count: "
            f"{'PASS' if item_count_matches else 'FAIL'} "
            f"({total_items_in_output} vs {total_attachment_items})\n"
        )
        file.write(
            f"  Duplicate quotation_key values: {duplicate_keys}\n"
        )
        file.write(
            f"  Blank quotation_key values: {blank_keys}\n"
        )
        file.write(
            f"  Negative quoted_value_total values: {negative_totals}\n"
        )
        file.write(
            f"  Quotations spanning >1 source document: "
            f"{multi_document_quotations}\n\n"
        )

        file.write("FILL RATES\n")
        file.write("\n".join(fill_rate_lines()) + "\n\n")

        file.write("OUTPUTS\n")
        file.write(f"{QUOTATIONS_CSV}\n")

    # --------------------------------------------------------
    # FINAL CONSOLE (counts only - never a customer name)
    # --------------------------------------------------------

    print("\n")
    print("=" * 70)
    print("QUOTATION BANK COMPLETE")
    print("=" * 70)

    print(f"Total quotations        : {len(output_frame)}")

    print("\nQuotation key basis:")

    for basis, count in key_basis_counts.items():
        print(f"  {basis:<25} {count:>7}")

    print("\nVerification:")

    print(
        f"  Item-count identity  : "
        f"{'PASS' if item_count_matches else 'FAIL'} "
        f"({total_items_in_output} vs {total_attachment_items})"
    )
    print(f"  Duplicate keys       : {duplicate_keys}")
    print(f"  Blank keys           : {blank_keys}")
    print(f"  Negative totals      : {negative_totals}")
    print(f"  Multi-document quotes: {multi_document_quotations}")

    print("\nFiles created:")
    print(f"  {QUOTATIONS_CSV}")
    print(f"  {SUMMARY_TXT}")

    print("\nInput files were not modified.")

    if not item_count_matches:
        print(
            "\nWARNING: item-count identity check FAILED - "
            "investigate before using this output."
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
