"""
Local Odoo Customer Matcher
===========================

Purpose
-------
Match customer names extracted from quotation documents against
an Odoo customer master CSV.

NO ODOO API CALLS ARE MADE.

The Odoo CSV is treated as the authoritative customer reference.

Input
-----
1. quotations.csv
2. res_partners.csv
3. sale_orders.csv (order history enrichment)
4. customer_industry_proxy.csv (industry enrichment)

Output
------
customer_enriched.csv
customer_review.csv
customer_knowledge_bank.csv (one row per matched Odoo customer)
"""

from pathlib import Path
import csv
import re
import unicodedata
from difflib import SequenceMatcher


# ============================================================
# CONFIGURATION
# ============================================================

# Anchored to the project root (one level up from this file's
# own folder) rather than a plain relative path, so paths always
# resolve correctly regardless of the working directory the
# script happens to be run from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ------------------------------------------------------------
# Quotation parser output
# ------------------------------------------------------------

QUOTATIONS_CSV = (
    PROJECT_ROOT / "Quotation_Data/03_structured_current/quotations.csv"
)

# ------------------------------------------------------------
# Odoo customer master CSV
#
# Produced by odoo_export/export_customers.py (live Odoo pull,
# not a manual export) - res.partner records referenced by the
# fetched sale.order records.
# ------------------------------------------------------------

ODOO_CUSTOMERS_CSV = (
    PROJECT_ROOT / "Quotation_Data/05_odoo_export/res_partners.csv"
)

# ------------------------------------------------------------
# Odoo sale order export CSV
#
# Also produced by odoo_export/export_customers.py - one row per
# sale.order, keyed to res.partner via partner_id_id. Used here
# only to aggregate per-customer order history (counts, PO value,
# regions, industry, status) - never written back to Odoo.
# ------------------------------------------------------------

SALE_ORDERS_CSV = (
    PROJECT_ROOT / "Quotation_Data/05_odoo_export/sale_orders.csv"
)

# ------------------------------------------------------------
# Customer -> industry proxy CSV
#
# Produced by odoo_export/customer_industry_proxy.py - industry
# lives per sale.order, not on res.partner, so this is the most
# common industry across a customer's orders (see that script's
# docstring for the tie-break rule).
# ------------------------------------------------------------

INDUSTRY_PROXY_CSV = (
    PROJECT_ROOT / "Quotation_Data/05_odoo_export/customer_industry_proxy.csv"
)

# ------------------------------------------------------------
# Output
# ------------------------------------------------------------

OUTPUT_FOLDER = (
    PROJECT_ROOT / "Quotation_Data/06_customer_matching"
)

ENRICHED_CSV = (
    OUTPUT_FOLDER / "customer_enriched.csv"
)

REVIEW_CSV = (
    OUTPUT_FOLDER / "customer_review.csv"
)

KNOWLEDGE_BANK_CSV = (
    OUTPUT_FOLDER / "customer_knowledge_bank.csv"
)

# ------------------------------------------------------------
# Manual override table
#
# Fuzzy name matching can never be 100% certain. Once a human
# has confirmed or corrected a specific quotation customer's
# real Odoo identity (by reviewing customer_review.csv), that
# decision belongs here - checked FIRST, before fuzzy matching
# runs at all - so it's guaranteed correct on every future run
# regardless of how the fuzzy score would have come out.
#
# Columns: quotation_customer_text, odoo_customer_id, note
# Matched case-insensitively against the exact text that was in
# quotations.csv's customer column (not the fuzzy-normalized
# form), so an override always means exactly what a human meant
# by it.
# ------------------------------------------------------------

OVERRIDES_CSV = (
    OUTPUT_FOLDER / "customer_name_overrides.csv"
)


# ============================================================
# COLUMN CONFIGURATION
# ============================================================

# The script will first try these quotation columns.
QUOTATION_CUSTOMER_COLUMNS = [
    "customer",
    "customer_raw",
    "Customer",
    "Customer_Name",
]

# The script will first try these Odoo columns.
ODOO_ID_COLUMNS = [
    "id",
    "ID",
    "partner_id",
    "customer_id",
]

ODOO_NAME_COLUMNS = [
    "name",
    "Name",
    "customer_name",
    "Customer_Name",
]

ODOO_INDUSTRY_COLUMNS = [
    "industry",
    "industry_id",
    "Industry",
    "Industry_Name",
]

ODOO_REFERENCE_COLUMNS = [
    "ref",
    "Reference",
    "customer_code",
    "Customer_Code",
    "code",
]

ODOO_EMAIL_COLUMNS = [
    "email",
    "Email",
]

ODOO_PHONE_COLUMNS = [
    "phone",
    "Phone",
    "mobile",
    "Mobile",
]

ODOO_CITY_COLUMNS = [
    "city",
    "City",
]

ODOO_STATE_COLUMNS = [
    "state_id_name",
    "state",
    "State",
]

ODOO_COUNTRY_COLUMNS = [
    "country_id_name",
    "country",
    "Country",
]

# ------------------------------------------------------------
# RFQ/quotation number column on sale_orders.csv
#
# This technical field (x_studio_internal_rfq_assignment_number)
# carries Adage's own quotation number (e.g. "Q24AKIC10077"), the
# same number printed on the quotation document itself
# ("Our Quotation No: ..." / "ADAGE QUOTE REF: ..."). An exact
# match here resolves the customer directly via that specific
# sale.order's partner_id - no fuzzy name comparison needed.
# ------------------------------------------------------------

RFQ_NUMBER_COLUMNS = [
    "x_studio_internal_rfq_assignment_number",
]


# ============================================================
# MATCHING THRESHOLDS
# ============================================================

# Exact normalized match
EXACT_THRESHOLD = 1.00

# Automatically accept
HIGH_THRESHOLD = 0.90

# Keep as possible match/review
MEDIUM_THRESHOLD = 0.75

# Below this = no useful match
LOW_THRESHOLD = 0.50


# ============================================================
# NAME NORMALIZATION
# ============================================================

def normalize_customer_name(name):
    """
    Normalize a customer name for comparison.

    Example:

        M/s. ABC Industries Pvt. Ltd.

    becomes approximately:

        abc industries private limited
    """

    if name is None:
        return ""

    text = str(name)

    # Unicode normalization
    text = unicodedata.normalize(
        "NFKC",
        text
    )

    # Lowercase
    text = text.lower()

    # Remove common prefixes
    text = re.sub(
        r"\bm/s\.?\b",
        " ",
        text
    )

    # Normalize common business abbreviations
    replacements = {
        "pvt.": "private",
        "pvt": "private",
        "ltd.": "limited",
        "ltd": "limited",
        "co.": "company",
        "corp.": "corporation",
        "corp": "corporation",
        "&": "and",
    }

    for old, new in replacements.items():

        text = text.replace(
            old,
            new
        )

    # Remove punctuation
    text = re.sub(
        r"[^a-z0-9\s]",
        " ",
        text
    )

    # Normalize whitespace
    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# TOKEN NORMALIZATION
# ============================================================

def tokenize(name):
    """
    Convert normalized name into tokens.
    """

    normalized = normalize_customer_name(
        name
    )

    return set(
        normalized.split()
    )


# ============================================================
# SIMILARITY
# ============================================================

def similarity_score(
    quotation_name,
    odoo_name
):
    """
    Calculate a combined similarity score.

    Components:

    1. Sequence similarity
    2. Token overlap
    """

    q = normalize_customer_name(
        quotation_name
    )

    o = normalize_customer_name(
        odoo_name
    )

    if not q or not o:
        return 0.0

    # Exact normalized match
    if q == o:
        return 1.0

    # Sequence similarity
    sequence_score = SequenceMatcher(
        None,
        q,
        o
    ).ratio()

    # Token overlap
    q_tokens = tokenize(
        quotation_name
    )

    o_tokens = tokenize(
        odoo_name
    )

    if q_tokens and o_tokens:

        intersection = (
            q_tokens & o_tokens
        )

        union = (
            q_tokens | o_tokens
        )

        token_score = (
            len(intersection)
            / len(union)
        )

    else:

        token_score = 0.0

    # Weighted score
    score = (
        0.65 * sequence_score
        +
        0.35 * token_score
    )

    return round(
        score,
        4
    )


# ============================================================
# STATUS
# ============================================================

def get_match_status(score):

    if score >= EXACT_THRESHOLD:
        return "EXACT"

    if score >= HIGH_THRESHOLD:
        return "HIGH"

    if score >= MEDIUM_THRESHOLD:
        return "REVIEW"

    if score >= LOW_THRESHOLD:
        return "LOW"

    return "NO_MATCH"


# ============================================================
# FIND COLUMN
# ============================================================

def find_column(
    fieldnames,
    candidates
):
    """
    Find a usable column from a list of possible names.
    """

    if not fieldnames:
        return None

    # Exact match first
    for candidate in candidates:

        if candidate in fieldnames:
            return candidate

    # Case-insensitive match
    lower_map = {
        field.lower(): field
        for field in fieldnames
    }

    for candidate in candidates:

        found = lower_map.get(
            candidate.lower()
        )

        if found:
            return found

    return None


# ============================================================
# READ CSV
# ============================================================

def read_csv(path):

    with open(
        path,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:

        reader = csv.DictReader(
            file
        )

        rows = list(reader)

        fieldnames = (
            reader.fieldnames
            or []
        )

    return rows, fieldnames


# ============================================================
# NORMALIZE ODOO ROW
# ============================================================

def build_customer_record(
    row,
    id_column,
    name_column,
    industry_column,
    reference_column,
    email_column=None,
    phone_column=None,
    city_column=None,
    state_column=None,
    country_column=None,
):
    """
    Convert an Odoo CSV row into a standard internal structure.
    """

    def get(column):

        return (
            (row.get(column, "") or "")
            if column
            else ""
        )

    customer_id = get(id_column)

    customer_name = get(name_column)

    industry = get(industry_column)

    reference = get(reference_column)

    return {

        "customer_id":
            customer_id.strip(),

        "customer_name":
            customer_name.strip(),

        "industry":
            industry.strip(),

        "reference":
            reference.strip(),

        "email":
            get(email_column).strip(),

        "phone":
            get(phone_column).strip(),

        "city":
            get(city_column).strip(),

        "state":
            get(state_column).strip(),

        "country":
            get(country_column).strip(),
    }


# ============================================================
# MANUAL OVERRIDES
# ============================================================

def load_overrides(path):
    """
    Load the manual override table, if it exists.

    Returns a dict keyed on the lowercased/stripped quotation
    customer text -> Odoo customer id. Missing file is not an
    error - the table starts empty and grows as the user reviews
    matches.
    """

    if not path.exists():
        return {}

    rows, _ = read_csv(
        path
    )

    overrides = {}

    for row in rows:

        raw_text = (
            row.get(
                "quotation_customer_text",
                ""
            )
            or ""
        ).strip().lower()

        odoo_id = (
            row.get(
                "odoo_customer_id",
                ""
            )
            or ""
        ).strip()

        if raw_text and odoo_id:
            overrides[raw_text] = odoo_id

    return overrides


# ============================================================
# INDUSTRY PROXY (per-customer, derived from sale orders)
# ============================================================

def load_industry_proxy(path):
    """
    Load the customer -> industry proxy table, if it exists.

    Returns a dict keyed on Odoo customer id (string) -> dict with
    industry / industry_specify_others / order_count /
    industry_order_count. Missing file is not an error - it just means
    no industry enrichment is available yet (run
    odoo_export/customer_industry_proxy.py).
    """

    if not path.exists():
        return {}

    rows, _ = read_csv(
        path
    )

    proxy = {}

    for row in rows:

        partner_id = (
            row.get("partner_id", "") or ""
        ).strip()

        if not partner_id:
            continue

        proxy[partner_id] = {

            "industry":
                (row.get("industry", "") or "").strip(),

            "industry_specify_others":
                (row.get("industry_specify_others", "") or "").strip(),

            "order_count":
                (row.get("order_count", "") or "").strip(),

            "industry_order_count":
                (row.get("industry_order_count", "") or "").strip(),
        }

    return proxy


# ============================================================
# SALE ORDER HISTORY (per-customer aggregation)
# ============================================================

def load_sale_order_summary(path):
    """
    Load sale_orders.csv, if it exists, and aggregate it into a
    per-customer order-history summary keyed on Odoo customer id
    (string) -> dict.

    Missing file is not an error - it just means no order-history
    enrichment is available yet (run odoo_export/export_customers.py).

    "Most recent order wins" is used for single-valued fields
    (customer_type / adage_customer / end_user), the same
    convention odoo_export/customer_industry_proxy.py already uses
    for its industry tie-break.
    """

    if not path.exists():
        return {}

    rows, _ = read_csv(
        path
    )

    grouped = {}

    for row in rows:

        partner_id = (
            row.get("partner_id_id", "") or ""
        ).strip()

        if not partner_id:
            continue

        grouped.setdefault(
            partner_id,
            []
        ).append(row)

    summary = {}

    for partner_id, order_rows in grouped.items():

        total_orders = len(order_rows)

        total_po_value = 0.0

        regions = set()

        quote_status_counts = {}

        latest_row = None
        latest_date = ""

        for row in order_rows:

            po_value_raw = (
                row.get("x_studio_po_value", "") or ""
            ).strip()

            if po_value_raw:

                try:

                    total_po_value += float(
                        po_value_raw.replace(",", "")
                    )

                except ValueError:

                    pass

            region = (
                row.get("x_studio_responsible_region", "") or ""
            ).strip()

            if region:
                regions.add(region)

            status = (
                row.get("x_studio_quote_status", "") or ""
            ).strip()

            if status:

                quote_status_counts[status] = (
                    quote_status_counts.get(status, 0) + 1
                )

            order_date = (
                row.get("date_order", "") or ""
            ).strip()

            if order_date and order_date >= latest_date:

                latest_date = order_date
                latest_row = row

        quote_status_summary = ", ".join(
            f"{status}:{count}"
            for status, count in sorted(
                quote_status_counts.items()
            )
        )

        summary[partner_id] = {

            "total_orders":
                total_orders,

            "total_po_value":
                round(total_po_value, 2),

            "regions":
                ", ".join(sorted(regions)),

            "latest_order_date":
                latest_date,

            "customer_type":
                (
                    (latest_row or {}).get(
                        "x_studio_customer_type", ""
                    )
                    or ""
                ).strip(),

            "adage_customer":
                (
                    (latest_row or {}).get(
                        "x_studio_adage_customer_name", ""
                    )
                    or ""
                ).strip(),

            "end_user":
                (
                    (latest_row or {}).get(
                        "x_studio_end_user_name", ""
                    )
                    or ""
                ).strip(),

            "quote_status_summary":
                quote_status_summary,
        }

    return summary


# ============================================================
# RFQ NUMBER MATCHING
# ============================================================

def normalize_rfq_number(value):
    """
    Normalize an RFQ/quotation number for exact comparison:
    uppercase, all whitespace removed. Quotation numbers are
    single alphanumeric tokens (e.g. "Q24AKIC10077"), so this is
    deliberately just case/whitespace normalization - no fuzzy
    scoring involved.
    """

    if value is None:
        return ""

    text = re.sub(
        r"\s+",
        "",
        str(value).strip()
    ).upper()

    return text


def load_rfq_index(path):
    """
    Load sale_orders.csv and index it by normalized RFQ number
    (see normalize_rfq_number) -> list of matching order rows, so
    a collision (the same number on orders for two different
    customers) can be detected rather than guessed.

    Returns (index, rfq_column). Missing file or missing column
    returns ({}, None) - not an error, the caller falls back to
    fuzzy name matching.
    """

    if not path.exists():
        return {}, None

    rows, fieldnames = read_csv(
        path
    )

    rfq_column = find_column(
        fieldnames,
        RFQ_NUMBER_COLUMNS
    )

    if not rfq_column:
        return {}, None

    index = {}

    for row in rows:

        raw_value = (
            row.get(rfq_column, "") or ""
        ).strip()

        key = normalize_rfq_number(
            raw_value
        )

        if not key:
            continue

        index.setdefault(
            key,
            []
        ).append(row)

    return index, rfq_column


def build_order_extra(order_row, matched_rfq_number):
    """
    Flatten one matched sale.order row's own fields into the
    "knowledge bank" columns for that specific quotation - these
    are per-document facts (this exact order's industry, region,
    PO value, ...), more precise than the customer-level
    aggregation in build_enrichment_extra().
    """

    def get(field):

        return (
            order_row.get(field, "") or ""
        ).strip()

    return {

        "matched_order_id":
            get("id"),

        "matched_rfq_number":
            matched_rfq_number,

        "matched_order_industry":
            get("x_studio_type_of_industry"),

        "matched_order_industry_specify_others":
            get("x_studio_specify_others"),

        "matched_order_customer_type":
            get("x_studio_customer_type"),

        "matched_order_region":
            get("x_studio_responsible_region"),

        "matched_order_po_number":
            get("x_studio_po_number"),

        "matched_order_po_value":
            get("x_studio_po_value"),

        "matched_order_quote_status":
            get("x_studio_quote_status"),

        "matched_order_date":
            get("date_order"),
    }


EMPTY_ORDER_EXTRA = {

    "matched_order_id": "",
    "matched_rfq_number": "",
    "matched_order_industry": "",
    "matched_order_industry_specify_others": "",
    "matched_order_customer_type": "",
    "matched_order_region": "",
    "matched_order_po_number": "",
    "matched_order_po_value": "",
    "matched_order_quote_status": "",
    "matched_order_date": "",
}

EMPTY_ENRICHMENT_EXTRA = {

    "matched_industry": "",
    "matched_industry_specify_others": "",
    "matched_industry_confidence": "",
    "total_orders": "",
    "total_po_value": "",
    "regions": "",
    "latest_order_date": "",
    "customer_type": "",
    "adage_customer": "",
    "end_user": "",
    "quote_status_summary": "",
}


# ============================================================
# FIND BEST MATCH
# ============================================================

def find_best_match(
    quotation_customer,
    customers
):
    """
    Find the best Odoo customer match.
    """

    if not quotation_customer:

        return None

    best_customer = None
    best_score = 0.0

    for customer in customers:

        score = similarity_score(
            quotation_customer,
            customer[
                "customer_name"
            ]
        )

        if score > best_score:

            best_score = score

            best_customer = customer

    if best_customer is None:

        return None

    return {

        "customer":
            best_customer,

        "score":
            best_score,

        "status":
            get_match_status(
                best_score
            ),
    }


# ============================================================
# PER-QUOTATION MATCH
# ============================================================
#
# Extracted from main()'s loop so a second driver script (one that
# points at a different document-level table, e.g. boq_coords's own
# documents joined to quotations.csv) can reuse the exact same
# override -> RFQ -> fuzzy-name decision without copy-pasting it.
# Behavior is unchanged from the original inline loop.
# ------------------------------------------------------------

def match_quotation(
    quotation,
    quotation_customer_column,
    customers,
    customers_by_id,
    overrides,
    rfq_index,
    industry_proxy,
    order_summary,
):
    """
    Decide the Odoo customer match for one document-level quotation row.

    Returns:
        (enriched_row, review_row_or_None, status, counters)

    `counters` is a dict of counter-name -> increment (1) for whichever
    counters this decision affects, so the caller's running totals stay
    in one place without this function reaching into caller state.
    """

    def build_enrichment_extra(customer_id):

        proxy = industry_proxy.get(
            customer_id,
            {}
        )

        history = order_summary.get(
            customer_id,
            {}
        )

        industry_confidence = ""

        if proxy.get("industry_order_count") and proxy.get("order_count"):

            industry_confidence = (
                f"{proxy['industry_order_count']} of "
                f"{proxy['order_count']} orders"
            )

        return {

            "matched_industry":
                proxy.get("industry", ""),

            "matched_industry_specify_others":
                proxy.get("industry_specify_others", ""),

            "matched_industry_confidence":
                industry_confidence,

            "total_orders":
                history.get("total_orders", ""),

            "total_po_value":
                history.get("total_po_value", ""),

            "regions":
                history.get("regions", ""),

            "latest_order_date":
                history.get("latest_order_date", ""),

            "customer_type":
                history.get("customer_type", ""),

            "adage_customer":
                history.get("adage_customer", ""),

            "end_user":
                history.get("end_user", ""),

            "quote_status_summary":
                history.get("quote_status_summary", ""),
        }

    quote_customer = (
        quotation.get(
            quotation_customer_column,
            ""
        )
        or ""
    ).strip()

    # ----------------------------------------------------
    # MISSING CUSTOMER
    # ----------------------------------------------------

    if not quote_customer:

        enriched = dict(
            quotation
        )

        enriched.update({

            "matched_customer_id": "",
            "matched_customer_name": "",
            "matched_customer_reference": "",
            "matched_email": "",
            "matched_phone": "",
            "matched_city": "",
            "matched_state": "",
            "matched_country": "",
            "customer_match_score": 0,
            "customer_match_status": "MISSING",

            **EMPTY_ENRICHMENT_EXTRA,
            **EMPTY_ORDER_EXTRA,
        })

        review = {

            "source_file":
                quotation.get("source_file", ""),

            "quotation_number":
                quotation.get("quotation_number", ""),

            "quotation_customer": "",
            "matched_customer_name": "",
            "match_score": 0,
            "match_status": "MISSING",
            "reason": "Quotation customer is empty",
        }

        return enriched, review, "MISSING", {"missing_count": 1}

    # ----------------------------------------------------
    # MANUAL OVERRIDE (checked before fuzzy matching)
    # ----------------------------------------------------

    override_id = overrides.get(
        quote_customer.lower()
    )

    if (
        override_id
        and override_id in customers_by_id
    ):

        customer = customers_by_id[
            override_id
        ]

        enriched = dict(
            quotation
        )

        enriched.update({

            "matched_customer_id":
                customer["customer_id"],

            "matched_customer_name":
                customer["customer_name"],

            "matched_customer_reference":
                customer["reference"],

            "matched_email":
                customer["email"],

            "matched_phone":
                customer["phone"],

            "matched_city":
                customer["city"],

            "matched_state":
                customer["state"],

            "matched_country":
                customer["country"],

            "customer_match_score": 1.0,
            "customer_match_status": "OVERRIDE",

            **build_enrichment_extra(
                customer["customer_id"]
            ),
            **EMPTY_ORDER_EXTRA,
        })

        return enriched, None, "OVERRIDE", {"override_count": 1}

    # ----------------------------------------------------
    # RFQ NUMBER MATCH (checked before fuzzy name matching)
    #
    # quotations.csv's quotation_number, when present, is compared
    # exactly against Odoo's x_studio_internal_rfq_assignment_number -
    # a match resolves the customer directly via that specific
    # sale.order's partner_id, no fuzzy scoring involved. Falls back
    # to fuzzy name matching below only when no RFQ number was
    # extracted or no match is found.
    # ----------------------------------------------------

    quotation_number_value = (
        quotation.get(
            "quotation_number",
            ""
        )
        or ""
    ).strip()

    rfq_key = normalize_rfq_number(
        quotation_number_value
    )

    rfq_orders = (
        rfq_index.get(rfq_key, [])
        if rfq_key
        else []
    )

    if rfq_orders:

        distinct_partner_ids = {
            (order.get("partner_id_id", "") or "").strip()
            for order in rfq_orders
            if (order.get("partner_id_id", "") or "").strip()
        }

        if len(distinct_partner_ids) > 1:

            enriched = dict(
                quotation
            )

            enriched.update({

                "matched_customer_id": "",
                "matched_customer_name": "",
                "matched_customer_reference": "",
                "matched_email": "",
                "matched_phone": "",
                "matched_city": "",
                "matched_state": "",
                "matched_country": "",
                "customer_match_score": 0,
                "customer_match_status": "RFQ_AMBIGUOUS",

                **EMPTY_ENRICHMENT_EXTRA,
                **EMPTY_ORDER_EXTRA,
            })

            conflicting_names = sorted({
                (order.get("partner_id_name", "") or "").strip()
                for order in rfq_orders
            })

            review = {

                "source_file":
                    quotation.get("source_file", ""),

                "quotation_number":
                    quotation_number_value,

                "quotation_customer":
                    quote_customer,

                "matched_customer_name":
                    " / ".join(conflicting_names),

                "match_score": 0,
                "match_status": "RFQ_AMBIGUOUS",

                "reason":
                    "RFQ number matched multiple different "
                    "Odoo customers",
            }

            return (
                enriched,
                review,
                "RFQ_AMBIGUOUS",
                {"rfq_ambiguous_count": 1},
            )

        partner_id = next(
            iter(distinct_partner_ids)
        )

        rfq_customer = customers_by_id.get(
            partner_id
        )

        if rfq_customer:

            chosen_order = max(
                rfq_orders,
                key=lambda order: (
                    order.get("date_order", "") or ""
                )
            )

            enriched = dict(
                quotation
            )

            enriched.update({

                "matched_customer_id":
                    rfq_customer["customer_id"],

                "matched_customer_name":
                    rfq_customer["customer_name"],

                "matched_customer_reference":
                    rfq_customer["reference"],

                "matched_email":
                    rfq_customer["email"],

                "matched_phone":
                    rfq_customer["phone"],

                "matched_city":
                    rfq_customer["city"],

                "matched_state":
                    rfq_customer["state"],

                "matched_country":
                    rfq_customer["country"],

                "customer_match_score": 1.0,
                "customer_match_status": "RFQ_MATCH",

                **build_enrichment_extra(
                    rfq_customer["customer_id"]
                ),

                **build_order_extra(
                    chosen_order,
                    quotation_number_value
                ),
            })

            return (
                enriched,
                None,
                "RFQ_MATCH",
                {"rfq_match_count": 1},
            )

        # The order's partner id wasn't resolved in the
        # res_partners export (rare) - fall through to fuzzy
        # name matching below rather than guessing.

    # ----------------------------------------------------
    # MATCH (fuzzy name fallback - only reached when no RFQ
    # number was extracted, or no RFQ match was found above)
    # ----------------------------------------------------

    result = find_best_match(
        quote_customer,
        customers
    )

    enriched = dict(
        quotation
    )

    if result is None:

        enriched.update({

            "matched_customer_id": "",
            "matched_customer_name": "",
            "matched_customer_reference": "",
            "matched_email": "",
            "matched_phone": "",
            "matched_city": "",
            "matched_state": "",
            "matched_country": "",
            "customer_match_score": 0,
            "customer_match_status": "NO_MATCH",

            **EMPTY_ENRICHMENT_EXTRA,
            **EMPTY_ORDER_EXTRA,
        })

        review = {

            "source_file":
                quotation.get("source_file", ""),

            "quotation_number":
                quotation.get("quotation_number", ""),

            "quotation_customer":
                quote_customer,

            "matched_customer_name": "",
            "match_score": 0,
            "match_status": "NO_MATCH",

            "reason":
                "No Odoo customer candidate found",
        }

        return enriched, review, "NO_MATCH", {"no_match_count": 1}

    customer = result["customer"]
    score = result["score"]
    status = result["status"]

    enriched.update({

        "matched_customer_id":
            customer["customer_id"],

        "matched_customer_name":
            customer["customer_name"],

        "matched_customer_reference":
            customer["reference"],

        "matched_email":
            customer["email"],

        "matched_phone":
            customer["phone"],

        "matched_city":
            customer["city"],

        "matched_state":
            customer["state"],

        "matched_country":
            customer["country"],

        "customer_match_score": score,
        "customer_match_status": status,

        **build_enrichment_extra(
            customer["customer_id"]
        ),
        **EMPTY_ORDER_EXTRA,
    })

    review = None
    counters = {}

    if status == "EXACT":

        counters = {"exact_count": 1}

    elif status == "HIGH":

        counters = {"high_count": 1}

    elif status == "REVIEW":

        counters = {"review_count": 1}

        review = {

            "source_file":
                quotation.get("source_file", ""),

            "quotation_number":
                quotation.get("quotation_number", ""),

            "quotation_customer":
                quote_customer,

            "matched_customer_name":
                customer["customer_name"],

            "match_score": score,
            "match_status": status,

            "reason":
                "Medium-confidence customer match",
        }

    elif status == "LOW":

        counters = {"low_count": 1}

        review = {

            "source_file":
                quotation.get("source_file", ""),

            "quotation_number":
                quotation.get("quotation_number", ""),

            "quotation_customer":
                quote_customer,

            "matched_customer_name":
                customer["customer_name"],

            "match_score": score,
            "match_status": status,

            "reason":
                "Low-confidence customer match",
        }

    else:

        counters = {"no_match_count": 1}

        review = {

            "source_file":
                quotation.get("source_file", ""),

            "quotation_number":
                quotation.get("quotation_number", ""),

            "quotation_customer":
                quote_customer,

            "matched_customer_name":
                customer["customer_name"],

            "match_score": score,
            "match_status": status,

            "reason":
                "No reliable match",
        }

    return enriched, review, status, counters


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("LOCAL ODOO CUSTOMER MATCHER")
    print("=" * 70)

    # --------------------------------------------------------
    # CHECK FILES
    # --------------------------------------------------------

    if not QUOTATIONS_CSV.exists():

        print("\nERROR:")
        print(
            "Quotation CSV not found:"
        )

        print(
            QUOTATIONS_CSV.resolve()
        )

        return

    if not ODOO_CUSTOMERS_CSV.exists():

        print("\nERROR:")
        print(
            "Odoo customer CSV not found:"
        )

        print(
            ODOO_CUSTOMERS_CSV.resolve()
        )

        return

    OUTPUT_FOLDER.mkdir(
        parents=True,
        exist_ok=True
    )

    # --------------------------------------------------------
    # READ QUOTATIONS
    # --------------------------------------------------------

    quotations, quotation_fields = (
        read_csv(
            QUOTATIONS_CSV
        )
    )

    print(
        f"\nQuotation rows: "
        f"{len(quotations)}"
    )

    # --------------------------------------------------------
    # FIND QUOTATION CUSTOMER COLUMN
    # --------------------------------------------------------

    quotation_customer_column = (
        find_column(
            quotation_fields,
            QUOTATION_CUSTOMER_COLUMNS
        )
    )

    if not quotation_customer_column:

        print(
            "\nERROR:"
        )

        print(
            "Could not find customer column in "
            "quotations.csv."
        )

        print(
            "\nAvailable columns:"
        )

        for field in quotation_fields:

            print(
                f"  {field}"
            )

        return

    print(
        f"Quotation customer column: "
        f"{quotation_customer_column}"
    )

    # --------------------------------------------------------
    # READ ODOO CUSTOMERS
    # --------------------------------------------------------

    odoo_rows, odoo_fields = (
        read_csv(
            ODOO_CUSTOMERS_CSV
        )
    )

    print(
        f"Odoo customer records: "
        f"{len(odoo_rows)}"
    )

    # --------------------------------------------------------
    # FIND ODOO COLUMNS
    # --------------------------------------------------------

    id_column = find_column(
        odoo_fields,
        ODOO_ID_COLUMNS
    )

    name_column = find_column(
        odoo_fields,
        ODOO_NAME_COLUMNS
    )

    industry_column = find_column(
        odoo_fields,
        ODOO_INDUSTRY_COLUMNS
    )

    reference_column = find_column(
        odoo_fields,
        ODOO_REFERENCE_COLUMNS
    )

    email_column = find_column(
        odoo_fields,
        ODOO_EMAIL_COLUMNS
    )

    phone_column = find_column(
        odoo_fields,
        ODOO_PHONE_COLUMNS
    )

    city_column = find_column(
        odoo_fields,
        ODOO_CITY_COLUMNS
    )

    state_column = find_column(
        odoo_fields,
        ODOO_STATE_COLUMNS
    )

    country_column = find_column(
        odoo_fields,
        ODOO_COUNTRY_COLUMNS
    )

    print(
        f"\nOdoo ID column       : "
        f"{id_column}"
    )

    print(
        f"Odoo name column     : "
        f"{name_column}"
    )

    print(
        f"Odoo industry column : "
        f"{industry_column}"
    )

    print(
        f"Odoo reference       : "
        f"{reference_column}"
    )

    if not name_column:

        print(
            "\nERROR:"
        )

        print(
            "Could not identify the Odoo customer "
            "name column."
        )

        print(
            "\nAvailable columns:"
        )

        for field in odoo_fields:

            print(
                f"  {field}"
            )

        return

    # --------------------------------------------------------
    # BUILD INTERNAL CUSTOMER MASTER
    # --------------------------------------------------------

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

        if customer[
            "customer_name"
        ]:

            customers.append(
                customer
            )

    print(
        f"\nUsable Odoo customers: "
        f"{len(customers)}"
    )

    customers_by_id = {
        customer["customer_id"]: customer
        for customer in customers
    }

    # --------------------------------------------------------
    # LOAD MANUAL OVERRIDES
    # --------------------------------------------------------

    overrides = load_overrides(
        OVERRIDES_CSV
    )

    print(
        f"Manual overrides loaded: "
        f"{len(overrides)}"
    )

    # --------------------------------------------------------
    # LOAD INDUSTRY PROXY / SALE ORDER HISTORY
    # --------------------------------------------------------

    industry_proxy = load_industry_proxy(
        INDUSTRY_PROXY_CSV
    )

    print(
        f"Industry proxy records loaded: "
        f"{len(industry_proxy)}"
    )

    order_summary = load_sale_order_summary(
        SALE_ORDERS_CSV
    )

    print(
        f"Customers with order history: "
        f"{len(order_summary)}"
    )

    rfq_index, rfq_column = load_rfq_index(
        SALE_ORDERS_CSV
    )

    print(
        f"Sale order RFQ column: "
        f"{rfq_column}"
    )

    print(
        f"RFQ numbers indexed  : "
        f"{len(rfq_index)}"
    )

    rfq_match_count = 0
    rfq_ambiguous_count = 0

    # --------------------------------------------------------
    # MATCH QUOTATIONS
    #
    # Per-quotation decision (override -> RFQ exact match -> fuzzy
    # name fallback) lives in match_quotation() so a second driver
    # script pointed at a different document table can reuse it
    # unchanged.
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

    for quotation in quotations:

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

    # ========================================================
    # WRITE ENRICHED CSV
    # ========================================================

    enriched_fields = list(
        quotations[0].keys()
        if quotations
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

    # Remove duplicates while preserving order
    enriched_fields = list(
        dict.fromkeys(
            enriched_fields
        )
    )

    with open(
        ENRICHED_CSV,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=enriched_fields
        )

        writer.writeheader()

        writer.writerows(
            enriched_rows
        )

    # ========================================================
    # WRITE REVIEW CSV
    # ========================================================

    review_fields = [

        "source_file",

        "quotation_number",

        "quotation_customer",

        "matched_customer_name",

        "match_score",

        "match_status",

        "reason",
    ]

    with open(
        REVIEW_CSV,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=review_fields
        )

        writer.writeheader()

        writer.writerows(
            review_rows
        )

    # ========================================================
    # WRITE CUSTOMER KNOWLEDGE BANK CSV
    #
    # One row per distinct MATCHED Odoo customer (dedupe by
    # matched_customer_id), not one row per quotation - a
    # browsable customer directory rather than a quotation log.
    # ========================================================

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

        customer_id = enriched.get(
            "matched_customer_id",
            ""
        )

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

    knowledge_bank_rows = list(
        knowledge_bank_by_id.values()
    )

    with open(
        KNOWLEDGE_BANK_CSV,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=knowledge_bank_fields
        )

        writer.writeheader()

        writer.writerows(
            knowledge_bank_rows
        )

    # ========================================================
    # SUMMARY
    # ========================================================

    print("\n")
    print("=" * 70)
    print("CUSTOMER MATCHING COMPLETE")
    print("=" * 70)

    print(
        f"Quotation records : "
        f"{len(quotations)}"
    )

    print(
        f"Exact matches     : "
        f"{exact_count}"
    )

    print(
        f"High confidence   : "
        f"{high_count}"
    )

    print(
        f"Review             : "
        f"{review_count}"
    )

    print(
        f"Low confidence    : "
        f"{low_count}"
    )

    print(
        f"No match          : "
        f"{no_match_count}"
    )

    print(
        f"Missing customer  : "
        f"{missing_count}"
    )

    print(
        f"Manual overrides  : "
        f"{override_count}"
    )

    print(
        f"RFQ number matches: "
        f"{rfq_match_count}"
    )

    print(
        f"RFQ ambiguous     : "
        f"{rfq_ambiguous_count}"
    )

    print(
        "\nEnriched CSV:"
    )

    print(
        ENRICHED_CSV.resolve()
    )

    print(
        "\nReview CSV:"
    )

    print(
        REVIEW_CSV.resolve()
    )

    print(
        "\nCustomer knowledge bank CSV:"
    )

    print(
        KNOWLEDGE_BANK_CSV.resolve()
    )


if __name__ == "__main__":
    main()
