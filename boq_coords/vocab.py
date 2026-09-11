"""
Vocabulary copied (not imported) from quotation_parser_v1.py so that file
stays frozen while this package evolves independently. Keep in sync by hand;
each block cites its v1 source line so a future diff is easy to spot.

Source: quotation_parser_v1.py, as of the coords-parser design (2026-09).
"""
from __future__ import annotations

import re

# quotation_parser_v1.py:149 BOQ_HEADER_ALIASES
BOQ_HEADER_ALIASES: dict[str, list[str]] = {
    "item_no": [
        "sr no", "sr. no", "s.no", "s. no", "sl no", "sl. no", "sl.no",
        "no", "no.", "item no", "item no.", "item number", "item",
    ],
    "description": [
        "description", "item description", "material description",
        "product description", "equipment description", "particulars",
        "details", "scope of supply", "material", "product",
    ],
    "quantity": [
        "qty", "qty.", "quantity", "quantity nos", "qnty",
    ],
    "unit": [
        "unit", "uom", "units",
    ],
    "unit_price": [
        "unit price", "unit rate", "rate", "price", "rate/unit",
        "rate per unit", "basic rate", "unit cost",
    ],
    "total_price": [
        "total price", "total amount", "amount", "amount (rs)",
        "amount(rs)", "amount (inr)", "total", "line total",
        "extended amount", "value",
    ],
    # Not in the 17-column schema; folded into description (see fields.py).
    "part_no": [
        "part no", "part no.", "part number", "model no", "model no.",
    ],
    # A dedicated MAKE/VENDOR column - distinct from an inline "Make:" label
    # inside description text (fields.py handles that case). Confirmed as a
    # real gap: a genuine "MAKE LIST" table (SR NO | ITEM DESCRIPTION | MAKE
    # | Total | Unit) had no field to classify its MAKE column into at all,
    # so the make word (e.g. "SIEMENS") fell through to the unclassified
    # "other" bucket and never reached the output make field.
    "make": [
        "make", "vendor", "manufacturer", "oem",
    ],
}

# Aliases in this set only count as total_price when the SAME header window
# also matched a unit_price column - a bare "Total" with no unit_price
# column present is ambiguous (often total QUANTITY, not price) - confirmed
# on the same "MAKE LIST" table above, where a bare "Total" column (really a
# quantity count) was misclassified as total_price with no unit_price column
# anywhere in the table to contradict it.
AMBIGUOUS_TOTAL_PRICE_ALIASES = {"total"}

# quotation_parser_v1.py:415 STOP_SECTION_MARKERS
STOP_SECTION_MARKERS: list[str] = [
    "scope definition",
    "scope defin",
    "commercial conditions",
    "terms and conditions",
    "scope of supply",
    "technical specifications",
    "warranty",
    "payment terms",
    "delivery terms",
]

# quotation_parser_v1.py:399 BOQ_SECTION_MARKERS
BOQ_SECTION_MARKERS: list[str] = [
    "bill of quantities",
    "bill of quantity",
    "bill of materials",
    "bill of material",
    "boq",
    "bom",
    "summary of prices",
    "price schedule",
    "price breakup",
    "price break up",
    "schedule of quantities",
    "schedule of rates",
]

# quotation_parser_v1.py:384 GRAND_TOTAL_LABELS
GRAND_TOTAL_LABELS: list[str] = [
    "grand total",
    "net total",
    "total amount",
    "final total",
    "total payable",
    "net amount",
    "amount payable",
]

# quotation_parser_v1.py:361 SUBTOTAL_LABELS
SUBTOTAL_LABELS: list[str] = [
    "subtotal", "sub total", "sub-total", "total before tax",
]

# quotation_parser_v1.py:908 UNITS_PATTERN
UNITS_PATTERN = (
    r"Nos?|Pcs?|Pieces?|Sets?|Jobs?|Lots?|Units?|Each|EA|Kg|Kgs|"
    r"Gram|Grams|mg|L|Ltr|Ltrs|Liter|Liters|Met(er|ers)?|Mtrs?|"
    r"MM|CM|KM|Sqm|Sq\.?m|Sqft|Sq\.?\s*ft|Ton|Tons|Hours?|Hrs?|"
    r"Days?|Months?|Years?|Man-?days?|MDY|Visits?"
)
UNITS_RE = re.compile(rf"^(?:{UNITS_PATTERN})$", re.IGNORECASE)

# quotation_parser_v1.py:960 MAX_ITEM_NUMBER
MAX_ITEM_NUMBER = 100

# Placeholder price sentinels (Step 4 rule 3 / money.py PLACEHOLDER_RX).
PRICE_SENTINELS: list[str] = [
    "quoted", "included", "inclusive", "not quoted", "existing",
    "not used in a system", "tbd", "ls", "as per actual", "na", "n/a",
]


def normalize_label(text: str) -> str:
    """quotation_parser_v1.py:470 normalize_label"""
    text = text.lower()
    text = text.replace("&", "and")
    text = re.sub(r"[^a-z0-9\s./()-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_header_cell(text: str) -> str:
    """Header-specific normalization, looser than normalize_label(): table
    headers abbreviate with trailing periods ("SR. NO.") and carry bracketed
    currency/unit annotations ("UNIT PRICE (INR)", "[INR]") that must not
    block matching against an alias like "sr no" or "unit price". Not part
    of v1 - v1's normalize_label targets label:value line detection, a
    different problem from multi-row header-cell matching."""
    text = normalize_label(text)
    text = re.sub(r"[\(\[][^)\]]*[\)\]]", " ", text)  # strip (INR), [INR]
    text = text.replace(".", "")
    return re.sub(r"\s+", " ", text).strip()


def classify_header_word(word: str) -> str | None:
    """Map a single header cell's normalized text to a schema field, using
    BOQ_HEADER_ALIASES. Matches by LONGEST alias across all fields, not by
    a fixed field-priority order: item_no's bare alias "item" is a prefix
    of description's "item description", so a fixed priority order lets
    "Item Description" get misclassified as item_no before description's
    own, more specific alias is ever tried - confirmed as a real corpus
    bug (a genuine priced table with an "Item Description" column was
    rejected outright because 'description' never made it into
    fields_found). total_price vs unit_price only ties on alias length
    when both are candidates, in which case total_price wins so "TOTAL
    PRICE" never lands on the unit column (design note, §Column bands)."""
    norm = normalize_header_cell(word)
    if not norm:
        return None

    best_field = None
    best_len = -1
    # total_price before unit_price in this list only matters as a
    # tie-break when their matched alias lengths are exactly equal.
    for field in ("total_price", "unit_price", "item_no", "quantity", "unit", "part_no", "make", "description"):
        for alias in BOQ_HEADER_ALIASES[field]:
            alias_n = alias.replace(".", "")
            if norm == alias_n or norm.startswith(alias_n + " ") or norm.startswith(alias_n + "("):
                if len(alias_n) > best_len:
                    best_len = len(alias_n)
                    best_field = field
    return best_field


def is_ambiguous_total_price_header(word: str) -> bool:
    """True if `word` classifies as total_price ONLY via one of
    AMBIGUOUS_TOTAL_PRICE_ALIASES (e.g. a bare "Total") - the caller should
    only trust this as total_price when a unit_price column is also present
    in the same header window; otherwise it's more likely a total quantity
    column, not a price."""
    norm = normalize_header_cell(word)
    return norm in AMBIGUOUS_TOTAL_PRICE_ALIASES
