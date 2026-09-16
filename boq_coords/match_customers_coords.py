"""
Customer Matcher for boq_coords
================================

Purpose:
    Give boq_coords' own document set (03f_structured_coords/coords_documents.csv)
    the same customer match that the main pipeline's
    odoo_match_customer/match_customers.py already produces, WITHOUT
    re-implementing customer-name or quotation-number extraction here.

    boq_coords has no document-level extraction of a customer name (and
    its quotation_number comes from the PDF manifest, not the document
    text). The main pipeline's quotation_parser_v1.py already extracted
    both, for the same PDFs, into 03_structured_current/quotations.csv.
    So this script borrows those two columns (plus a few other document
    facts) by joining on source_path, then runs the exact same matching
    decision (odoo_match_customer.match_customers.match_quotation) used
    by the main pipeline.

Input (all read-only):
    Quotation_Data/03f_structured_coords/coords_documents.csv
    Quotation_Data/03_structured_current/quotations.csv
    Quotation_Data/05_odoo_export/res_partners.csv
    Quotation_Data/05_odoo_export/sale_orders.csv
    Quotation_Data/05_odoo_export/customer_industry_proxy.csv
    Quotation_Data/06_customer_matching/customer_name_overrides.csv

Output:
    Quotation_Data/06_customer_matching_coords/customer_enriched_coords.csv
    Quotation_Data/06_customer_matching_coords/customer_review_coords.csv
    Quotation_Data/06_customer_matching_coords/customer_knowledge_bank_coords.csv

Important:
    Progress/summary output is aggregate (counts) only - never a customer
    name - matching the confidentiality discipline used throughout this
    project. Redirect this script's own stdout to a file when running it:

        python boq_coords\\match_customers_coords.py > run_match_coords.log 2>&1

This script does NOT modify any of its input files, including boq_coords'
own output and the main pipeline's 03_structured_current/06_customer_matching/
folders.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(PROJECT_ROOT))

from odoo_match_customer.match_customers import (  # noqa: E402
    build_customer_record,
    find_column,
    load_industry_proxy,
    load_overrides,
    load_rfq_index,
    load_sale_order_summary,
    match_quotation,
    read_csv,
    ODOO_CITY_COLUMNS,
    ODOO_COUNTRY_COLUMNS,
    ODOO_EMAIL_COLUMNS,
    ODOO_ID_COLUMNS,
    ODOO_INDUSTRY_COLUMNS,
    ODOO_NAME_COLUMNS,
    ODOO_PHONE_COLUMNS,
    ODOO_REFERENCE_COLUMNS,
    ODOO_STATE_COLUMNS,
    QUOTATION_CUSTOMER_COLUMNS,
)


# ============================================================
# CONFIGURATION
# ============================================================

COORDS_DOCUMENTS_CSV = (
    PROJECT_ROOT
    / "Quotation_Data"
    / "03f_structured_coords"
    / "coords_documents.csv"
)

QUOTATIONS_CSV = (
    PROJECT_ROOT
    / "Quotation_Data"
    / "03_structured_current"
    / "quotations.csv"
)

ODOO_CUSTOMERS_CSV = (
    PROJECT_ROOT
    / "Quotation_Data"
    / "05_odoo_export"
    / "res_partners.csv"
)

SALE_ORDERS_CSV = (
    PROJECT_ROOT
    / "Quotation_Data"
    / "05_odoo_export"
    / "sale_orders.csv"
)

INDUSTRY_PROXY_CSV = (
    PROJECT_ROOT
    / "Quotation_Data"
    / "05_odoo_export"
    / "customer_industry_proxy.csv"
)

# Shared with the main pipeline - an override is a fact about a customer
# text string's real Odoo identity, not about which extractor produced
# the item rows, so the same table applies here unchanged.
OVERRIDES_CSV = (
    PROJECT_ROOT
    / "Quotation_Data"
    / "06_customer_matching"
    / "customer_name_overrides.csv"
)

OUTPUT_FOLDER = (
    PROJECT_ROOT
    / "Quotation_Data"
    / "06_customer_matching_coords"
)

ENRICHED_CSV = OUTPUT_FOLDER / "customer_enriched_coords.csv"
REVIEW_CSV = OUTPUT_FOLDER / "customer_review_coords.csv"
KNOWLEDGE_BANK_CSV = OUTPUT_FOLDER / "customer_knowledge_bank_coords.csv"

CSV_ENCODING = "utf-8-sig"

# Document-level fields borrowed from quotations.csv onto each
# coords_documents.csv row. customer/quotation_number feed the matcher
# directly; the rest ride along as useful context on the enriched output.
BORROWED_QUOTATION_FIELDS = [
    "customer",
    "quotation_number",
    "quotation_date",
    "currency",
    "subtotal",
    "tax",
    "discount",
    "grand_total",
]


# ============================================================
# BUILD "COORDS QUOTATIONS" TABLE
# ============================================================

def build_coords_quotations(coords_documents, quotations_by_path):
    """
    One row per boq_coords document, with customer/quotation_number/etc.
    borrowed from quotations.csv via source_path (fallback source_file).

    A coords_documents.csv row with no match in quotations.csv is kept
    with those fields blank - never dropped - so it naturally lands as
    MISSING/NO_MATCH and shows up in the review CSV rather than silently
    vanishing from the join.
    """

    quotations_by_file = {
        row.get("source_file", ""): row
        for row in quotations_by_path.values()
    }

    coords_quotations = []
    unmatched = 0

    for doc in coords_documents:

        source_path = doc.get("source_path", "")

        source_quotation = quotations_by_path.get(source_path)

        if source_quotation is None:

            source_quotation = quotations_by_file.get(
                doc.get("source_file", "")
            )

        if source_quotation is None:
            unmatched += 1

        row = dict(doc)

        for field in BORROWED_QUOTATION_FIELDS:

            row[field] = (
                source_quotation.get(field, "")
                if source_quotation
                else ""
            )

        coords_quotations.append(row)

    return coords_quotations, unmatched


# ============================================================
# MAIN
# ============================================================

def check_inputs() -> bool:

    missing = [
        path
        for path in (
            COORDS_DOCUMENTS_CSV,
            QUOTATIONS_CSV,
            ODOO_CUSTOMERS_CSV,
        )
        if not path.exists()
    ]

    if not missing:
        return True

    print("\nERROR: required input file(s) not found:")

    for path in missing:
        print(f"  {path}")

    return False


def main():

    print("=" * 70)
    print("BOQ_COORDS CUSTOMER MATCHER")
    print("=" * 70)

    if not check_inputs():
        return 1

    OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------
    # READ
    # --------------------------------------------------------

    coords_documents, _ = read_csv(COORDS_DOCUMENTS_CSV)

    print(f"\nboq_coords documents : {len(coords_documents)}")

    quotations, _ = read_csv(QUOTATIONS_CSV)

    quotations_by_path = {
        row.get("source_path", ""): row
        for row in quotations
    }

    print(f"Main-pipeline quotations : {len(quotations)}")

    coords_quotations, unmatched = build_coords_quotations(
        coords_documents,
        quotations_by_path,
    )

    print(
        f"boq_coords docs with no quotations.csv match : {unmatched}"
    )

    quotation_customer_column = find_column(
        BORROWED_QUOTATION_FIELDS,
        QUOTATION_CUSTOMER_COLUMNS,
    )

    if not quotation_customer_column:

        print("\nERROR: 'customer' field not found on borrowed columns.")
        return 1

    # --------------------------------------------------------
    # READ ODOO CUSTOMERS
    # --------------------------------------------------------

    odoo_rows, odoo_fields = read_csv(ODOO_CUSTOMERS_CSV)

    print(f"Odoo customer records : {len(odoo_rows)}")

    id_column = find_column(odoo_fields, ODOO_ID_COLUMNS)
    name_column = find_column(odoo_fields, ODOO_NAME_COLUMNS)
    industry_column = find_column(odoo_fields, ODOO_INDUSTRY_COLUMNS)
    reference_column = find_column(odoo_fields, ODOO_REFERENCE_COLUMNS)
    email_column = find_column(odoo_fields, ODOO_EMAIL_COLUMNS)
    phone_column = find_column(odoo_fields, ODOO_PHONE_COLUMNS)
    city_column = find_column(odoo_fields, ODOO_CITY_COLUMNS)
    state_column = find_column(odoo_fields, ODOO_STATE_COLUMNS)
    country_column = find_column(odoo_fields, ODOO_COUNTRY_COLUMNS)

    if not name_column:

        print("\nERROR: could not identify the Odoo customer name column.")
        return 1

    customers = []

    for row in odoo_rows:

        customer = build_customer_record(
            row,
            id_column,
            name_column,
            industry_column,
            reference_column,
            email_column,
            phone_column,
            city_column,
            state_column,
            country_column,
        )

        if customer["customer_name"]:
            customers.append(customer)

    print(f"Usable Odoo customers : {len(customers)}")

    customers_by_id = {
        customer["customer_id"]: customer
        for customer in customers
    }

    # --------------------------------------------------------
    # LOAD SUPPORTING TABLES (same as the main pipeline)
    # --------------------------------------------------------

    overrides = load_overrides(OVERRIDES_CSV)

    print(f"Manual overrides loaded : {len(overrides)}")

    industry_proxy = load_industry_proxy(INDUSTRY_PROXY_CSV)

    print(f"Industry proxy records loaded : {len(industry_proxy)}")

    order_summary = load_sale_order_summary(SALE_ORDERS_CSV)

    print(f"Customers with order history : {len(order_summary)}")

    rfq_index, rfq_column = load_rfq_index(SALE_ORDERS_CSV)

    print(f"Sale order RFQ column : {rfq_column}")
    print(f"RFQ numbers indexed : {len(rfq_index)}")

    # --------------------------------------------------------
    # MATCH
    # --------------------------------------------------------

    enriched_rows = []
    review_rows = []

    exact_count = 0
    high_count = 0
    review_count = 0
    low_count = 0
    no_match_count = 0
    missing_count = 0
    override_count = 0
    rfq_match_count = 0
    rfq_ambiguous_count = 0

    for quotation in coords_quotations:

        enriched, review, status, counters = match_quotation(
            quotation,
            quotation_customer_column,
            customers,
            customers_by_id,
            overrides,
            rfq_index,
            industry_proxy,
            order_summary,
        )

        enriched_rows.append(enriched)

        if review is not None:
            review_rows.append(review)

        exact_count += counters.get("exact_count", 0)
        high_count += counters.get("high_count", 0)
        review_count += counters.get("review_count", 0)
        low_count += counters.get("low_count", 0)
        no_match_count += counters.get("no_match_count", 0)
        missing_count += counters.get("missing_count", 0)
        override_count += counters.get("override_count", 0)
        rfq_match_count += counters.get("rfq_match_count", 0)
        rfq_ambiguous_count += counters.get("rfq_ambiguous_count", 0)

    # --------------------------------------------------------
    # WRITE ENRICHED CSV
    # --------------------------------------------------------

    enriched_fields = list(
        coords_quotations[0].keys()
        if coords_quotations
        else []
    )

    enriched_fields.extend([
        "matched_customer_id",
        "matched_customer_name",
        "matched_customer_reference",
        "matched_email",
        "matched_phone",
        "matched_city",
        "matched_state",
        "matched_country",
        "matched_industry",
        "matched_industry_specify_others",
        "matched_industry_confidence",
        "total_orders",
        "total_po_value",
        "regions",
        "latest_order_date",
        "customer_type",
        "adage_customer",
        "end_user",
        "quote_status_summary",
        "matched_order_id",
        "matched_rfq_number",
        "matched_order_industry",
        "matched_order_industry_specify_others",
        "matched_order_customer_type",
        "matched_order_region",
        "matched_order_po_number",
        "matched_order_po_value",
        "matched_order_quote_status",
        "matched_order_date",
        "customer_match_score",
        "customer_match_status",
    ])

    enriched_fields = list(dict.fromkeys(enriched_fields))

    with open(ENRICHED_CSV, "w", newline="", encoding=CSV_ENCODING) as file:

        writer = csv.DictWriter(file, fieldnames=enriched_fields)
        writer.writeheader()
        writer.writerows(enriched_rows)

    # --------------------------------------------------------
    # WRITE REVIEW CSV
    # --------------------------------------------------------

    review_fields = [
        "source_file",
        "quotation_number",
        "quotation_customer",
        "matched_customer_name",
        "match_score",
        "match_status",
        "reason",
    ]

    with open(REVIEW_CSV, "w", newline="", encoding=CSV_ENCODING) as file:

        writer = csv.DictWriter(file, fieldnames=review_fields)
        writer.writeheader()
        writer.writerows(review_rows)

    # --------------------------------------------------------
    # WRITE CUSTOMER KNOWLEDGE BANK CSV
    # --------------------------------------------------------

    knowledge_bank_fields = [
        "matched_customer_id",
        "matched_customer_name",
        "matched_customer_reference",
        "matched_email",
        "matched_phone",
        "matched_city",
        "matched_state",
        "matched_country",
        "matched_industry",
        "matched_industry_specify_others",
        "matched_industry_confidence",
        "total_orders",
        "total_po_value",
        "regions",
        "latest_order_date",
        "customer_type",
        "adage_customer",
        "end_user",
        "quote_status_summary",
        "quotation_count",
    ]

    knowledge_bank_by_id = {}

    for enriched in enriched_rows:

        customer_id = enriched.get("matched_customer_id", "")

        if not customer_id:
            continue

        if customer_id not in knowledge_bank_by_id:

            knowledge_bank_by_id[customer_id] = {
                field: enriched.get(field, "")
                for field in knowledge_bank_fields
                if field != "quotation_count"
            }

            knowledge_bank_by_id[customer_id]["quotation_count"] = 0

        knowledge_bank_by_id[customer_id]["quotation_count"] += 1

    with open(KNOWLEDGE_BANK_CSV, "w", newline="", encoding=CSV_ENCODING) as file:

        writer = csv.DictWriter(file, fieldnames=knowledge_bank_fields)
        writer.writeheader()
        writer.writerows(knowledge_bank_by_id.values())

    # --------------------------------------------------------
    # SUMMARY (counts only - never a customer name)
    # --------------------------------------------------------

    print("\n")
    print("=" * 70)
    print("BOQ_COORDS CUSTOMER MATCHING COMPLETE")
    print("=" * 70)

    print(f"Documents matched  : {len(coords_quotations)}")
    print(f"Exact matches      : {exact_count}")
    print(f"High confidence    : {high_count}")
    print(f"Review             : {review_count}")
    print(f"Low confidence     : {low_count}")
    print(f"No match           : {no_match_count}")
    print(f"Missing customer   : {missing_count}")
    print(f"Manual overrides   : {override_count}")
    print(f"RFQ number matches : {rfq_match_count}")
    print(f"RFQ ambiguous      : {rfq_ambiguous_count}")

    print("\nEnriched CSV:")
    print(ENRICHED_CSV.resolve())

    print("\nReview CSV:")
    print(REVIEW_CSV.resolve())

    print("\nCustomer knowledge bank CSV:")
    print(KNOWLEDGE_BANK_CSV.resolve())

    print("\nInput files were not modified.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
