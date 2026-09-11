"""
Quotation Parser V1
===================

Purpose:
    Parse cleaned quotation TXT files into structured data.

Current targets:
    - Customer
    - Subject
    - Quotation number
    - Quotation date
    - Currency
    - BOQ / price table
    - Item number
    - Description
    - Quantity
    - Unit
    - Unit price
    - Total price
    - Subtotal
    - Tax
    - Discount
    - Grand total

Design:
    Conservative and pattern-aware.

Important:
    This is V1. It is intended for testing and measuring extraction
    quality before processing the entire quotation corpus.

The script does NOT modify input TXT files.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Optional


# ============================================================
# CONFIGURATION
# ============================================================

INPUT_FOLDER = Path(
    "Quotation_Data/03_preprocessed_text_2"
)

OUTPUT_FOLDER = Path(
    "Quotation_Data/03_structured_current"
)

QUOTATIONS_CSV = (
    OUTPUT_FOLDER / "quotations.csv"
)

ITEMS_CSV = (
    OUTPUT_FOLDER / "quotation_items.csv"
)

REVIEW_CSV = (
    OUTPUT_FOLDER / "parser_review.csv"
)

SUMMARY_TXT = (
    OUTPUT_FOLDER / "parser_summary.txt"
)

# ------------------------------------------------------------
# TEST LIMIT
# ------------------------------------------------------------
# Start with 10.
#
# After validation, change to:
#
# TEST_LIMIT = None
#
# to process the complete corpus.
# ------------------------------------------------------------

TEST_LIMIT = None


# ============================================================
# HEADER PATTERNS
# ============================================================

CUSTOMER_LABELS = [
    "customer",
    "customer name",
    "client",
    "client name",
    "bill to",
    "ship to",
    "buyer",
    "to",
]

SUBJECT_LABELS = [
    "subject",
    "sub",
    "re",
    "regarding",
]

QUOTATION_NUMBER_LABELS = [
    "quotation no",
    "quotation no.",
    "quotation number",
    "quote no",
    "quote no.",
    "quote number",
    "quotation ref",
    "quotation reference",
    "proposal no",
    "proposal no.",
    "proposal number",
    "offer no",
    "offer no.",
    "reference no",
    "ref no",
    "our quotation no",
    "our quotation no.",
    "our quotation number",
    "our quote no",
    "our quote no.",
    "adage quote ref",
    "adage quote reference",
    "ref",
]

DATE_LABELS = [
    "quotation date",
    "quote date",
    "date",
    "dated",
]

# ------------------------------------------------------------
# BOQ HEADER ALIASES
#
# Internal standard field
#     -> possible source column names
# ------------------------------------------------------------

BOQ_HEADER_ALIASES = {

    "item_no": [
        "sr no",
        "sr. no",
        "s.no",
        "s. no",
        "sl no",
        "sl. no",
        "sl.no",
        "no",
        "no.",
        "item no",
        "item no.",
        "item number",
        "item",
    ],

    "description": [
        "description",
        "item description",
        "material description",
        "product description",
        "equipment description",
        "particulars",
        "details",
        "scope of supply",
        "material",
        "product",
    ],

    "quantity": [
        "qty",
        "qty.",
        "quantity",
        "quantity nos",
        "qnty",
    ],

    "unit": [
        "unit",
        "uom",
        "units",
    ],

    "unit_price": [
        "unit price",
        "unit rate",
        "rate",
        "price",
        "rate/unit",
        "rate per unit",
        "basic rate",
        "unit cost",
    ],

    "total_price": [
        "total price",
        "total amount",
        "amount",
        "amount (rs)",
        "amount(rs)",
        "amount (inr)",
        "total",
        "line total",
        "extended amount",
        "value",
    ],
}


# ============================================================
# LABELED ITEM-LEVEL FIELDS (product / make / model / version)
# ============================================================
#
# Pulled out of a BOQ row's content BEFORE quantity/unit/price
# detection runs, so a labeled value's own line can't be
# mistaken for a quantity or a price. Only explicit labels are
# used - no guessing from unlabeled description text.
# ------------------------------------------------------------

LABELED_ITEM_FIELDS = [
    "product",
    "make",
    "model",
    "version",
]


LABEL_SPLIT_PATTERN = re.compile(
    r"\b(?P<label>"
    + "|".join(LABELED_ITEM_FIELDS)
    + r")\b\s*[:\-]",
    re.IGNORECASE
)


def extract_labeled_fields(
    content: list[str]
) -> tuple[dict, list[str]]:
    """
    Pull "Make: Siemens" / "Make:\\nSiemens" style labeled values
    out of a BOQ row's content.

    Handles multiple labels on one line ("Make: ENVEA, Model:
    AF22E") by splitting each match's value at the start of the
    next label on that line, so one label's value doesn't swallow
    the next label's text.

    Returns:
        fields (dict of field -> value, "" if not found)
        remaining_content (content with matched lines removed)
    """

    fields = {
        field: ""
        for field in LABELED_ITEM_FIELDS
    }

    consumed = set()

    for index, line in enumerate(content):

        if index in consumed:
            continue

        stripped = line.strip()

        matches = list(
            LABEL_SPLIT_PATTERN.finditer(stripped)
        )

        matched_any_value = False

        for match_index, match in enumerate(matches):

            field = match.group("label").lower()

            if fields[field]:
                continue

            value_start = match.end()

            value_end = (
                matches[match_index + 1].start()
                if match_index + 1 < len(matches)
                else len(stripped)
            )

            value = stripped[
                value_start:value_end
            ].strip(" ,;")

            if value:

                fields[field] = clean_field_value(
                    value
                )

                consumed.add(index)

                matched_any_value = True

        if matched_any_value:
            continue

        # Case: "Make" / "Make:" alone, value on next line.
        for field in LABELED_ITEM_FIELDS:

            if fields[field]:
                continue

            label_only_pattern = (
                rf"^{field}\s*[:\-]?$"
            )

            if re.match(
                label_only_pattern,
                stripped,
                flags=re.IGNORECASE
            ):

                if (
                    index + 1 < len(content)
                    and content[index + 1].strip()
                ):

                    fields[field] = clean_field_value(
                        content[index + 1]
                    )

                    consumed.add(index)
                    consumed.add(index + 1)

                break

    remaining_content = [
        line
        for index, line in enumerate(content)
        if index not in consumed
    ]

    return (
        fields,
        remaining_content
    )


# ============================================================
# FINANCIAL LABELS
# ============================================================

SUBTOTAL_LABELS = [
    "subtotal",
    "sub total",
    "sub-total",
    "total before tax",
]

TAX_LABELS = [
    "gst",
    "igst",
    "cgst",
    "sgst",
    "tax",
    "vat",
    "sales tax",
]

DISCOUNT_LABELS = [
    "discount",
    "less discount",
    "discount amount",
]

GRAND_TOTAL_LABELS = [
    "grand total",
    "net total",
    "total amount",
    "final total",
    "total payable",
    "net amount",
    "amount payable",
]


# ============================================================
# BOQ SECTION MARKERS
# ============================================================

BOQ_SECTION_MARKERS = [
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


STOP_SECTION_MARKERS = [
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

# An attempt was made here to add an explicit "next numbered
# section" / "NOTE:" stop signal for ending the BOQ region (e.g.
# "SECTION 2: NOTE & SCOPE DEFINITION"). It was reverted after
# measuring it on a 100-file batch:
#
# - The user's own example is already handled by the existing
#   STOP_SECTION_MARKERS entry "scope definition" -
#   "SECTION 2: NOTE & SCOPE DEFINITION" normalizes to
#   "section 2 note and scope definition", which already
#   contains that substring. So it was redundant for that case.
# - A bare "NOTE:" heading is too common as an inline footnote
#   INSIDE an item's own description ("NOTE: range shall be as
#   per XYZ") to use safely as a whole-region stop signal - it
#   truncated the region mid-table.
# - Even a "SECTION \d+ + topic word" pattern measurably made
#   things worse overall (both-prices-filled dropped 413 -> 398,
#   NO_PRICE_VALUES rose 43 -> 55): on 13 items it clipped the
#   LAST item in the table right where its own price line was,
#   losing price data that the original code was already
#   correctly capturing.
#
# If you hit a specific document where the table still runs on
# too far, the fix needs to target that document's exact wording
# rather than a broad "SECTION n" heuristic - flag the specific
# case and it can be added narrowly.


# ============================================================
# HELPERS
# ============================================================

def normalize_spaces(text: str) -> str:
    """
    Normalize repeated whitespace.
    """

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


def normalize_label(text: str) -> str:
    """
    Normalize labels for comparison.
    """

    text = text.lower()

    text = text.replace(
        "&",
        "and"
    )

    text = re.sub(
        r"[^a-z0-9\s./()-]",
        " ",
        text
    )

    text = normalize_spaces(
        text
    )

    return text


def contains_label(
    line: str,
    labels: list[str]
) -> bool:

    normalized = normalize_label(
        line
    )

    for label in labels:

        if (
            normalized == label
            or normalized.startswith(
                label + " "
            )
            or normalized.startswith(
                label + ":"
            )
        ):
            return True

    return False


KIND_ATTENTION_PATTERN = re.compile(
    r"^kind\s*attention\b|^attention\b|^attn\b",
    flags=re.IGNORECASE,
)

# A person's name preceded by an honorific (e.g. "Mr. Sher Chand
# Kamboj") is a contact name, not the customer/company - the real
# company name is expected on a following line.
HONORIFIC_PATTERN = re.compile(
    r"^(mr|mrs|ms|miss|dr|shri|smt)\.?\s+\S",
    flags=re.IGNORECASE,
)

# A bare "Name" / "Name:" header line - the actual contact/company
# name is on a following line, not this line itself.
EXTRA_SKIP_LABELS = {
    "name",
}


def is_skippable_value_line(
    line: str,
    labels: list[str]
) -> bool:
    """
    True if a line found while looking for a label's value is not
    itself a real value - e.g. it's another bare label ("Customer"),
    a Kind Attention / Attn header (with or without a name), or a
    contact person's name introduced by an honorific (Mr./Mrs./...).
    """

    normalized = normalize_label(
        line
    )

    if not normalized:
        return True

    if KIND_ATTENTION_PATTERN.match(normalized):
        return True

    if HONORIFIC_PATTERN.match(normalized):
        return True

    label_norms = {
        normalize_label(label)
        for label in labels
    }

    if normalized in label_norms:
        return True

    if normalized in EXTRA_SKIP_LABELS:
        return True

    return False


def find_next_valid_line(
    lines: list[str],
    start_index: int,
    from_offset: int,
    labels: list[str]
) -> tuple[str, int]:
    """
    Scan forward from from_offset for the first line that isn't
    blank and isn't skippable (see is_skippable_value_line).

    Returns (line, offset_of_line) or ("", -1) if none found within
    the lookahead window.
    """

    max_offset = min(
        from_offset + 6,
        len(lines) - start_index
    )

    offset = from_offset

    while offset < max_offset:

        candidate = lines[
            start_index + offset
        ].strip()

        if candidate and not is_skippable_value_line(
            candidate,
            labels
        ):

            return (candidate, offset)

        offset += 1

    return ("", -1)


def extract_after_label(
    lines: list[str],
    labels: list[str],
    start_index: int
) -> tuple[str, int]:
    """
    Extract value after a label.

    Supports:

        Customer: ABC Ltd

    and:

        Customer:
        ABC Ltd

    Returns:
        value, number of lines consumed
    """

    for offset in range(
        0,
        min(8, len(lines) - start_index)
    ):

        line = lines[
            start_index + offset
        ].strip()

        if not line:
            continue

        normalized = normalize_label(
            line
        )

        for label in labels:

            label_norm = normalize_label(
                label
            )

            # Case:
            # Customer: ABC Ltd
            #
            # Matched against the ORIGINAL line (not the lowercased/
            # punctuation-stripped `normalized` form) so the captured
            # value keeps its real casing and punctuation - matching
            # against `normalized` here previously returned a
            # lowercased, mangled value (e.g. a quotation number like
            # "2425W034R2" coming back as "2425w034r2").
            label_word_pattern = r"[\s.]*".join(
                re.escape(word)
                for word in label_norm.split()
            )

            # \b after the label stops a short label like "ref" from
            # matching as a prefix of an unrelated word ("Reference"
            # was being matched as "Ref" + captured "erence").
            pattern = (
                r"^\s*"
                + label_word_pattern
                + r"\b[\s.:\-]*(.+)$"
            )

            match = re.match(
                pattern,
                line,
                flags=re.IGNORECASE
            )

            if match:

                value = match.group(1).strip()

                if value and not is_skippable_value_line(
                    value,
                    labels
                ):
                    return (
                        value,
                        offset + 1
                    )

                if value:

                    # Value is a contact name / Kind Attention line
                    # ("Customer: Mr. Sher Chand Kamboj") - the real
                    # company name is expected below it.
                    next_line, next_offset = find_next_valid_line(
                        lines,
                        start_index,
                        offset + 1,
                        labels
                    )

                    if next_line:
                        return (
                            next_line,
                            next_offset + 1
                        )

            # Case:
            # Customer
            # ABC Ltd

            if normalized == label_norm:

                next_line, next_offset = find_next_valid_line(
                    lines,
                    start_index,
                    offset + 1,
                    labels
                )

                if next_line:
                    return (
                        next_line,
                        next_offset + 1
                    )

    return "", 0


def clean_field_value(
    value: str
) -> str:

    value = normalize_spaces(
        value
    )

    value = value.strip(
        " :-|"
    )

    return value


def extract_first_number(
    text: str
) -> Optional[float]:

    match = re.search(
        r"[-+]?\d[\d,]*(?:\.\d+)?",
        text
    )

    if not match:
        return None

    raw = match.group(
        0
    )

    try:

        return float(
            raw.replace(
                ",",
                ""
            )
        )

    except ValueError:

        return None


def is_item_number(
    line: str
) -> bool:
    """
    Detect common item number forms.

    Examples:

        1
        2
        10
        1.
        2)
        01
        1.1
    """

    line = line.strip()

    return bool(
        re.fullmatch(
            r"\d+(?:\.\d+)*[.)]?",
            line
        )
    )


def parse_number(
    value: str
) -> Optional[float]:
    """
    Convert a numeric-looking value to float.

    Returns None for non-numeric values such as:

        QUOTED
        AS PER ACTUAL
        NA

    This is intentional. The raw value is preserved separately.
    """

    if not value:
        return None

    value = value.strip()

    # Remove common currency symbols
    value = re.sub(
        r"[₹$€£]",
        "",
        value
    )

    # Strip the Indian "no paise" marker, e.g. "16,00,000/-".
    # Left in place, the trailing "-" survives the digit-filter
    # below (it's a kept character) but breaks float() since it's
    # no longer a leading sign - the whole value was silently
    # dropped as a price candidate instead of being recognized.
    value = re.sub(
        r"/-\s*$",
        "",
        value
    )

    # Keep digits, comma, decimal point and minus sign
    value = re.sub(
        r"[^0-9,.\-]",
        "",
        value
    )

    if not value:
        return None

    try:

        return float(
            value.replace(
                ",",
                ""
            )
        )

    except ValueError:

        return None

    # Keep digits, comma, period, minus
    value = re.sub(
        r"[^0-9,.\-]",
        "",
        value
    )

    if not value:
        return None

    # Handle Indian/standard comma formatting.
    try:

        return float(
            value.replace(
                ",",
                ""
            )
        )

    except ValueError:

        return None

# Common quotation units.
# This list can be extended when we encounter
# additional units in your real documents.
#
# Shared by extract_quantity_and_unit() (detecting "450 Meters")
# and is_unit_word_only() (detecting a bare "Meters" that follows
# a split-off quantity number on its own line - see
# find_item_start_positions()).

UNITS_PATTERN = (
    r"Nos?|"
    r"Pcs?|"
    r"Pieces?|"
    r"Sets?|"
    r"Jobs?|"
    r"Lots?|"
    r"Units?|"
    r"Each|"
    r"EA|"
    r"Kg|"
    r"Kgs|"
    r"Gram|"
    r"Grams|"
    r"mg|"
    r"L|"
    r"Ltr|"
    r"Ltrs|"
    r"Liter|"
    r"Liters|"
    r"Met(er|ers)?|"
    r"Mtrs?|"
    r"MM|"
    r"CM|"
    r"KM|"
    r"Sqm|"
    r"Sq\.?m|"
    r"Sqft|"
    r"Sq\.?\s*ft|"
    r"Ton|"
    r"Tons|"
    r"Hours?|"
    r"Hrs?|"
    r"Days?|"
    r"Months?|"
    r"Years?"
)


# ============================================================
# ITEM-LEVEL VALUE VALIDATION
# ============================================================
#
# These rules run at row-OUTPUT time (inside parse_boq_rows),
# never inside find_item_start_positions(). Changing how rows
# are segmented is the exact class of broad change that was
# tried and reverted twice before (see the STOP_SECTION_MARKERS
# comment and CHANGELOG v1.3.2) - validating the emitted value
# is safe, reversible, and keeps the row's description/prices
# intact when only one field is wrong.
# ------------------------------------------------------------

# Real BOQ item numbers are small. Anything larger is a PO or
# material code that landed in the item-number slot (values like
# 9021200461 and 698403961131124 appear in the corpus).
MAX_ITEM_NUMBER = 100

# A quantity WITHOUT a recognized unit is only trusted up to this
# value. With a real unit ("1600 Meters" of power cable) any
# magnitude is kept - see validate_quantity().
MAX_UNITLESS_QUANTITY = 100


# A date that landed in the item-number slot: three groups
# separated by . / or -, ending in a 2- or 4-digit year
# ("02.07.2025", "32.01.01)").
DATE_SHAPED_PATTERN = re.compile(
    r"^\d{1,4}[./-]\d{1,2}[./-]\d{2,4}[.)]?$"
)


def validate_item_number(
    raw: str
) -> tuple[str, Optional[str]]:
    """
    Validate an extracted item number.

    Accepts the real forms ("1", "1.", "2)", "1.1", "01") and
    rejects values that are clearly not item numbers: a date, or
    a number above MAX_ITEM_NUMBER.

    Returns:
        (value, warning_code_or_None) - a rejected value comes
        back as "" so the row is kept with the rest of its data.
    """

    value = (raw or "").strip()

    if not value:
        return ("", None)

    if DATE_SHAPED_PATTERN.match(value):
        return ("", "INVALID_ITEM_NUMBER")

    # Leading integer of "12", "12.", "12)", "12.3"
    match = re.match(
        r"^(\d+)",
        value
    )

    if not match:

        # Non-numeric and not date-shaped - left as-is rather
        # than guessed at (e.g. "A1", "1(a)").
        return (value, None)

    if int(match.group(1)) > MAX_ITEM_NUMBER:
        return ("", "INVALID_ITEM_NUMBER")

    return (value, None)


def validate_quantity(
    quantity: str,
    unit: str
) -> tuple[str, Optional[str]]:
    """
    Validate an extracted quantity, using the unit as the signal.

    A quantity carrying a recognized unit is trusted at any
    magnitude - "1600 Meters" of power cable is real, and a flat
    numeric cap would delete hundreds of legitimate bulk-cable
    rows. A large BARE number with no unit is almost always a
    material/PO code or price fragment that landed in the
    quantity slot ("2150000" on a row describing
    "Mete Rs. 650 Per Mtr").

    A rejected quantity is blanked, never relocated into a price
    field: these values are not prices, and unit_price is often
    already populated on the same row.

    Returns:
        (value, warning_code_or_None)
    """

    value = (quantity or "").strip()

    if not value:
        return ("", None)

    if unit:
        return (value, None)

    number = parse_number(value)

    if number is not None and number > MAX_UNITLESS_QUANTITY:
        return ("", "QUANTITY_OUT_OF_RANGE")

    return (value, None)


# Canonical unit vocabulary. Keys are lowercased spellings seen
# in the real corpus; values are the single form reported.
UNIT_CANONICAL = {

    "no": "NOS",
    "nos": "NOS",
    "no.": "NOS",
    "nos.": "NOS",
    "number": "NOS",
    "numbers": "NOS",

    "set": "SET",
    "sets": "SET",

    "lot": "LOT",
    "lots": "LOT",

    "meter": "METER",
    "meters": "METER",
    "metre": "METER",
    "metres": "METER",
    "mtr": "METER",
    "mtrs": "METER",
    "mtr.": "METER",
    "m": "METER",

    "day": "DAY",
    "days": "DAY",

    "each": "EACH",
    "ea": "EACH",

    "pc": "PCS",
    "pcs": "PCS",
    "piece": "PCS",
    "pieces": "PCS",

    "kg": "KG",
    "kgs": "KG",

    "mm": "MM",
}


def canonicalize_unit(
    unit: str
) -> tuple[str, Optional[str]]:
    """
    Fold a unit spelling to the canonical vocabulary
    ("Nos"/"No"/"NOS" -> "NOS", "Mtrs"/"Meters" -> "METER").

    An unrecognized unit is blanked and flagged rather than
    passed through, so the column holds one known vocabulary.

    Returns:
        (value, warning_code_or_None)
    """

    value = (unit or "").strip()

    if not value:
        return ("", None)

    lookup = value.lower().rstrip(".")

    canonical = UNIT_CANONICAL.get(lookup)

    if canonical:
        return (canonical, None)

    canonical = UNIT_CANONICAL.get(
        lookup + "."
    )

    if canonical:
        return (canonical, None)

    return ("", "UNKNOWN_UNIT")


def is_unit_word_only(
    line: str
) -> bool:
    """
    True when a line is nothing but a bare unit word
    (e.g. "Meters", "Nos", "Kg") - the second half of a
    "150 Meters" style value that got split onto two lines
    by PDF text extraction.
    """

    line = line.strip()

    if not line:
        return False

    return bool(
        re.fullmatch(
            UNITS_PATTERN,
            line,
            flags=re.IGNORECASE
        )
    )


def extract_quantity_and_unit(
    line: str
) -> tuple[str, str]:
    """
    Detect quantity and unit when they appear together.

    Examples:

        450 Meters
        1 Set
        25 Nos
        2 Kg
        10 Units
        3 Job

    Returns:

        quantity, unit

    Example:

        "450 Meters"
        -> ("450", "Meters")
    """

    if not line:
        return "", ""

    pattern = (
        rf"^\s*"
        rf"([0-9][0-9,]*(?:\.[0-9]+)?)"
        rf"\s+"
        rf"({UNITS_PATTERN})"
        rf"\s*$"
    )

    match = re.match(
        pattern,
        line,
        flags=re.IGNORECASE
    )

    if not match:
        return "", ""

    quantity = match.group(1)

    unit = match.group(2)

    return (
        quantity,
        unit
    )

def format_number(
    value: Optional[float]
):
    """
    Keep numeric values useful for CSV.

    Integer:
        2 -> 2

    Decimal:
        2.5 -> 2.5
    """

    if value is None:
        return ""

    if float(value).is_integer():

        return int(value)

    return value


# ============================================================
# HEADER DETECTION
# ============================================================

def detect_boq_header(
    lines: list[str]
) -> tuple[int, dict, list[str]]:
    """
    Detect a BOQ table header.

    Returns:

        header_start_index
        standard_field_to_source_header
        original_headers
    """

    for index, line in enumerate(
        lines
    ):

        # The header may be spread over one line,
        # or multiple nearby lines.

        candidates = [
            line
        ]

        if index + 1 < len(lines):

            candidates.append(
                " ".join(
                    lines[
                        index:index + 2
                    ]
                )
            )

        if index + 2 < len(lines):

            candidates.append(
                " ".join(
                    lines[
                        index:index + 3
                    ]
                )
            )

        for candidate in candidates:

            normalized = normalize_label(
                candidate
            )

            found = {}

            for standard_field, aliases in (
                BOQ_HEADER_ALIASES.items()
            ):

                for alias in aliases:

                    if alias in normalized:

                        found[
                            standard_field
                        ] = alias

                        break

            # A useful BOQ header should have
            # at least description + one price field.
            if (
                "description" in found
                and
                (
                    "unit_price" in found
                    or
                    "total_price" in found
                )
            ):

                # Try to preserve visible original headers.
                original_headers = [
                    part.strip()
                    for part in re.split(
                        r"\s{2,}|\|",
                        line
                    )
                    if part.strip()
                ]

                return (
                    index,
                    found,
                    original_headers
                )

    return (
        -1,
        {},
        []
    )


# ============================================================
# BOQ TABLE PARSING
# ============================================================

def extract_boq_region(
    lines: list[str],
    header_index: int
) -> list[str]:
    """
    Extract lines belonging to the BOQ area.

    Stops when a clear new section/financial area begins.
    """

    region = []

    for index in range(
        header_index + 1,
        len(lines)
    ):

        line = lines[index].strip()

        normalized = normalize_label(
            line
        )

        if not line:
            continue

        # Stop at major section headings.
        if any(
            marker in normalized
            for marker in STOP_SECTION_MARKERS
        ):

            break

        # Stop after financial summary.
        if any(
            normalized.startswith(
                label
            )
            for label in (
                GRAND_TOTAL_LABELS
                + SUBTOTAL_LABELS
            )
        ):

            # Keep the financial line itself;
            # caller can classify it separately.
            region.append(line)

            break

        region.append(line)

    return region


def find_item_start_positions(
    region: list[str]
) -> list[int]:
    """
    Find lines that look like BOQ item numbers.

    A bare number on its own line (e.g. "150") is normally a
    serial/item number - but the same shape shows up when a
    quantity value like "150 Meters" gets split across two
    lines by PDF text extraction ("150" then "Meters"). That
    split quantity was wrongly being treated as a new item,
    chopping the real item's row in two and throwing off every
    field detected after it (unit price ended up misaligned
    with total price, etc.).

    So: a number line is only treated as an item start when the
    very next line is NOT a bare unit word. A genuine item
    number is followed by a description, never by just "Meters"
    / "Nos" / "Kg" on its own.

    A second, separate case: a "Note:" / "Notes:" heading is
    often followed by its own numbered list ("1. ...", "2. ...",
    possibly running to "11." or more) - either the number alone
    on its own line, or inline with its text on the same line.
    Those numbers were being read as new BOQ items with no price
    attached, which was a major source of false, priceless rows.
    An earlier attempt fixed this by stopping the whole region
    scan at the "Note:" heading, but that measurably clipped real
    price data too: these notes can be embedded WITHIN a single
    item's own content, with that item's real price still to
    come after them, so truncating the region lost it. Instead,
    only the numbers that are actually part of the note's own
    sequential enumeration (1, 2, 3, 4, ... in order) are skipped
    here - nothing is truncated, so a legitimate item's price
    appearing right after the note block is unaffected.
    """

    positions = []

    in_note_block = False

    next_expected_note_number = None

    for index, line in enumerate(
        region
    ):

        stripped = line.strip()

        normalized = normalize_label(
            stripped
        )

        # ------------------------------------------------------
        # Enter a note-list block: a bare "Note:" / "Notes:"
        # heading whose next content line starts with "1.".
        # ------------------------------------------------------

        if (
            not in_note_block
            and normalized in ("note", "notes")
        ):

            next_line = ""

            for lookahead_index in range(
                index + 1,
                min(index + 4, len(region))
            ):

                candidate_line = region[
                    lookahead_index
                ].strip()

                if candidate_line:

                    next_line = candidate_line

                    break

            if re.match(
                r"^1[.)](\s|$)",
                next_line
            ):

                in_note_block = True

                next_expected_note_number = 1

                continue

        # ------------------------------------------------------
        # Inside a note-list block: skip lines that continue the
        # sequential enumeration (whether the number sits alone
        # or is followed by inline text). The first line that
        # ISN'T the next expected number ends the block, and
        # falls through to the normal item-number check below.
        # ------------------------------------------------------

        if in_note_block:

            if re.match(
                rf"^{next_expected_note_number}[.)](\s|$)",
                stripped
            ):

                next_expected_note_number += 1

                continue

            in_note_block = False

            next_expected_note_number = None

        if not is_item_number(line):
            continue

        next_line = (
            region[index + 1]
            if index + 1 < len(region)
            else ""
        )

        if is_unit_word_only(next_line):

            # This is a split "<quantity>\n<unit>" pair, not a
            # new item - skip it so it stays part of the
            # current item's content instead of starting a new
            # row.
            continue

        positions.append(
            index
        )

    return positions


def extract_numeric_candidates(
    lines: list[str]
) -> list[str]:
    """
    Get likely numeric lines from a multi-line BOQ row.

    We look for standalone/mostly numeric values.
    """

    candidates = []

    for line in lines:

        stripped = line.strip()

        if not stripped:
            continue

        # Remove currency/symbol noise for testing.
        test = re.sub(
            r"[₹$€£,\s]",
            "",
            stripped
        )

        if re.fullmatch(
            r"[-+]?\d+(?:\.\d+)?",
            test
        ):

            candidates.append(
                stripped
            )

    return candidates


def extract_unit_from_lines(
    lines: list[str]
) -> str:
    """
    Detect common quotation units.

    This is intentionally conservative.
    """

    common_units = {
        "nos",
        "no",
        "pcs",
        "pc",
        "set",
        "sets",
        "job",
        "lot",
        "ea",
        "each",
        "kg",
        "kgs",
        "g",
        "m",
        "meter",
        "meters",
        "mm",
        "cm",
        "litre",
        "liter",
        "ltr",
        "hours",
        "hr",
        "day",
        "days",
        "month",
        "months",
    }

    for line in lines:

        normalized = normalize_label(
            line
        )

        if normalized in common_units:

            return line.strip()

    return ""


# ============================================================
# NOTE / SCOPE / BULLET NOISE FILTERING
# ============================================================
#
# Removes non-BOQ noise lines from a single item's own content
# block (never truncates a scan - see the module-level comment
# above STOP_SECTION_MARKERS for why truncation was tried and
# reverted for an adjacent problem).
# ------------------------------------------------------------

BULLET_LINE_PATTERN = re.compile(
    r"^•\s*$"
)

# Narrowly scoped to the exact recurring heading wording seen in
# real documents ("SECTION 2: NOTE & SCOPE",
# "SECTION 2: TECHNICAL LITERATURE") - not a bare "SECTION n",
# which measurably regressed when tried as a region-truncation
# signal (see STOP_SECTION_MARKERS comment). Matched against the
# START of the normalized line only, so a mid-sentence reference
# like "as per Section 4.2 of IEC..." never matches.

SECTION_NOTE_SCOPE_PATTERN = re.compile(
    r"^section\s+\d+\s+(note|scope|technical literature)"
)


def is_noise_line(
    line: str
) -> bool:
    """
    True for a line that is nothing but a standalone bullet
    marker ("•" alone on its own line - the bullet glyph and
    its text end up on separate lines after PDF extraction), or
    the literal "SECTION n: NOTE & SCOPE" / "SECTION n: TECHNICAL
    LITERATURE" heading line itself.

    This intentionally does NOT try to catch the free-form
    scope/T&C prose sentences that follow those bullet markers -
    there's no reliable line-level marker to distinguish them
    from real item description text without the same kind of
    broad heuristic that has repeatedly regressed this session.
    Flag a specific recurring sentence/phrase if one keeps showing
    up mislabeled, and it can be added narrowly.
    """

    stripped = line.strip()

    if not stripped:
        return False

    if BULLET_LINE_PATTERN.match(
        stripped
    ):
        return True

    normalized = normalize_label(
        stripped
    )

    if SECTION_NOTE_SCOPE_PATTERN.match(
        normalized
    ):
        return True

    return False


def find_note_list_line_indices(
    lines: list[str]
) -> set[int]:
    """
    Given a single item's own content lines, return the indices
    of a "Note:" / "Notes:" heading and its own sequential
    numbered list (1., 2., 3., ...), if present - so the caller
    can strip them from that item's content entirely.

    This mirrors the note-block detection already proven in
    find_item_start_positions() (which only uses it to avoid
    misreading a note number as a new item start). It's
    deliberately duplicated here rather than shared, since it
    runs over a different list (one item's own content, not the
    whole BOQ region) and needs to return indices to remove rather
    than only influence item-start decisions - find_item_start_
    positions() itself is left untouched to avoid any regression
    risk to logic that's already been verified correct.
    """

    indices = set()

    in_note_block = False

    next_expected_note_number = None

    for index, line in enumerate(
        lines
    ):

        stripped = line.strip()

        normalized = normalize_label(
            stripped
        )

        if (
            not in_note_block
            and normalized in ("note", "notes")
        ):

            next_line = ""

            for lookahead_index in range(
                index + 1,
                min(index + 4, len(lines))
            ):

                candidate_line = lines[
                    lookahead_index
                ].strip()

                if candidate_line:

                    next_line = candidate_line

                    break

            if re.match(
                r"^1[.)](\s|$)",
                next_line
            ):

                in_note_block = True

                next_expected_note_number = 1

                indices.add(index)

                continue

        if in_note_block:

            if re.match(
                rf"^{next_expected_note_number}[.)](\s|$)",
                stripped
            ):

                indices.add(index)

                next_expected_note_number += 1

                continue

            in_note_block = False

            next_expected_note_number = None

    return indices


def parse_boq_rows(
    region: list[str]
) -> tuple[list[dict], list[str]]:
    """
    Reconstruct multi-line BOQ rows.

    Important behavior:

    1. Item number starts a new row.
    2. Description may contain multiple lines.
    3. Quantity + unit may appear together:
           450 Meters
           1 Set
           25 Nos
    4. Quantity and unit may also appear on separate lines.
    5. Price values may be numeric OR text such as:
           QUOTED
           AS PER ACTUAL
           NA
    6. Raw price values are preserved.
    """

    rows = []

    warnings = []

    positions = find_item_start_positions(
        region
    )

    if not positions:

        warnings.append(
            "NO_ITEM_NUMBER_ROWS_FOUND"
        )

        return (
            rows,
            warnings
        )

    for pos_index, start in enumerate(
        positions
    ):

        end = (
            positions[pos_index + 1]
            if pos_index + 1 < len(positions)
            else len(region)
        )

        block = [
            line.strip()
            for line in region[
                start:end
            ]
            if line.strip()
        ]

        if not block:
            continue

        item_no = block[0]

        content = block[1:]

        # ----------------------------------------------------
        # Remove financial summary rows
        # ----------------------------------------------------

        filtered_content = []

        for line in content:

            normalized = normalize_label(
                line
            )

            if any(
                normalized.startswith(
                    label
                )
                for label in (
                    SUBTOTAL_LABELS
                    + TAX_LABELS
                    + DISCOUNT_LABELS
                    + GRAND_TOTAL_LABELS
                )
            ):

                continue

            filtered_content.append(
                line
            )

        content = filtered_content

        # ----------------------------------------------------
        # REMOVE NOTE/SCOPE/BULLET NOISE LINES
        #
        # A numbered "Note:" list, standalone bullet markers, or
        # a "SECTION n: NOTE & SCOPE" / "SECTION n: TECHNICAL
        # LITERATURE" heading can end up glued into whichever
        # item's content block they physically fall inside
        # (usually the last item in the table) - they're not new
        # items (see find_item_start_positions()), but they also
        # aren't BOQ content, and a sentence like "2. UPS: 110
        # VAC..." can get its letters stripped down to a digit
        # string that still parses as a float, becoming a bogus
        # trailing price candidate. Only individual matched lines
        # are removed here - nothing is truncated, so a genuine
        # price appearing after one of these lines is unaffected.
        # ----------------------------------------------------

        note_list_indices = find_note_list_line_indices(
            content
        )

        content = [
            line
            for index, line in enumerate(content)
            if index not in note_list_indices
            and not is_noise_line(line)
        ]

        # ----------------------------------------------------
        # LABELED FIELDS (product / make / model / version)
        #
        # Removed before quantity/unit/price detection so a
        # labeled value's own line can't be mistaken for one
        # of those.
        # ----------------------------------------------------

        labeled_fields, content = extract_labeled_fields(
            content
        )

        # ----------------------------------------------------
        # QUANTITY + UNIT DETECTION
        # ----------------------------------------------------

        quantity = ""

        unit = ""

        quantity_line_index = None

        for index, line in enumerate(
            content
        ):

            q, u = extract_quantity_and_unit(
                line
            )

            if q and u:

                quantity = q
                unit = u

                quantity_line_index = index

                break

        # ----------------------------------------------------
        # SEPARATE QUANTITY AND UNIT
        #
        # Example:
        #
        # 450
        # Meters
        # ----------------------------------------------------

        if not quantity:

            for index in range(
                len(content) - 1
            ):

                q = content[index].strip()

                u = content[
                    index + 1
                ].strip()

                if (
                    re.fullmatch(
                        r"[0-9][0-9,]*(?:\.[0-9]+)?",
                        q
                    )
                    and
                    extract_quantity_and_unit(
                        f"{q} {u}"
                    )[0]
                ):

                    quantity = q

                    unit = u

                    quantity_line_index = index

                    break

        # ----------------------------------------------------
        # FALLBACK: standalone numeric quantity
        # ----------------------------------------------------

        if not quantity:

            for index, line in enumerate(
                content
            ):

                if re.fullmatch(
                    r"[0-9][0-9,]*(?:\.[0-9]+)?",
                    line
                ):

                    # First standalone number is treated
                    # as quantity only when there is no
                    # combined quantity/unit value.

                    quantity = line

                    quantity_line_index = index

                    break

        # ----------------------------------------------------
        # PRICE / VALUE CANDIDATES
        # ----------------------------------------------------

        value_candidates = []

        for index, line in enumerate(
            content
        ):

            # Skip quantity+unit line
            if (
                quantity_line_index
                is not None
                and index
                == quantity_line_index
            ):

                continue

            # Skip unit line when quantity and unit
            # are separate.
            if (
                unit
                and line.strip()
                == unit.strip()
            ):

                continue

            normalized = normalize_label(
                line
            )

            # Ignore obvious section/summary labels
            if any(
                normalized.startswith(
                    label
                )
                for label in (
                    SUBTOTAL_LABELS
                    + TAX_LABELS
                    + DISCOUNT_LABELS
                    + GRAND_TOTAL_LABELS
                )
            ):

                continue

            # Numeric values
            numeric = parse_number(
                line
            )

            if numeric is not None:

                value_candidates.append({
                    "raw": line,
                    "numeric": numeric,
                    "index": index,
                })

                continue

            # ------------------------------------------------
            # Preserve known textual price/status values
            # ------------------------------------------------

            if normalized in {
                "quoted",
                "quote",
                "tbd",
                "tbc",
                "na",
                "n a",
                "n/a",
                "as per actual",
                "as applicable",
                "at actual",
                "to be quoted",
            }:

                value_candidates.append({
                    "raw": line,
                    "numeric": None,
                    "index": index,
                })

        # ----------------------------------------------------
        # PRICE ASSIGNMENT
        # ----------------------------------------------------

        unit_price_raw = ""

        total_price_raw = ""

        unit_price_numeric = ""

        total_price_numeric = ""

        if len(value_candidates) >= 2:

            # Last two values are treated as:
            #
            # unit price
            # total price
            #
            # This is still a V1 heuristic.
            unit_candidate = (
                value_candidates[-2]
            )

            total_candidate = (
                value_candidates[-1]
            )

            unit_price_raw = (
                unit_candidate["raw"]
            )

            total_price_raw = (
                total_candidate["raw"]
            )

            unit_price_numeric = (
                format_number(
                    unit_candidate["numeric"]
                )
            )

            total_price_numeric = (
                format_number(
                    total_candidate["numeric"]
                )
            )

        elif len(value_candidates) == 1:

            total_candidate = (
                value_candidates[-1]
            )

            total_price_raw = (
                total_candidate["raw"]
            )

            total_price_numeric = (
                format_number(
                    total_candidate["numeric"]
                )
            )

            warnings.append(
                f"ITEM_{item_no}_ONLY_ONE_VALUE"
            )

        else:

            warnings.append(
                f"ITEM_{item_no}_NO_PRICE_VALUES"
            )

        # ----------------------------------------------------
        # DESCRIPTION
        # ----------------------------------------------------

        description_lines = []

        for index, line in enumerate(
            content
        ):

            # Don't include quantity/unit information.
            if (
                quantity_line_index
                is not None
                and index
                == quantity_line_index
            ):

                continue

            if (
                unit
                and line.strip()
                == unit.strip()
            ):

                continue

            # Don't include identified price values.
            price_raw_values = {

                unit_price_raw.strip(),

                total_price_raw.strip(),
            }

            if line.strip() in price_raw_values:

                continue

            # Don't include obvious numeric-only values
            # if they correspond to quantity/price.
            if re.fullmatch(
                r"[0-9][0-9,]*(?:\.[0-9]+)?",
                line
            ):

                continue

            # Don't include QUOTED when it was identified
            # as a price field.
            if (
                line.strip()
                in price_raw_values
            ):

                continue

            description_lines.append(
                line
            )

        description = "\n".join(
            description_lines
        ).strip()

        # ----------------------------------------------------
        # CONFIDENCE
        # ----------------------------------------------------

        confidence = "LOW"

        if (
            description
            and quantity
            and unit
            and unit_price_raw
            and total_price_raw
        ):

            confidence = "HIGH"

        elif (
            description
            and (
                quantity
                or unit_price_raw
                or total_price_raw
            )
        ):

            confidence = "MEDIUM"

        # ----------------------------------------------------
        # CREATE ROW
        # ----------------------------------------------------

        row = {

            "item_no":
                item_no,

            "description":
                description,

            "product":
                labeled_fields["product"],

            "make":
                labeled_fields["make"],

            "model":
                labeled_fields["model"],

            "version":
                labeled_fields["version"],

            "quantity":
                format_number(
                    parse_number(
                        quantity
                    )
                )
                if quantity
                else "",

            "unit":
                unit,

            # Numeric fields for analysis
            "unit_price":
                unit_price_numeric,

            "total_price":
                total_price_numeric,

            # Raw values preserved
            "unit_price_raw":
                unit_price_raw,

            "total_price_raw":
                total_price_raw,

            "raw_row_text":
                "\n".join(block),

            "confidence":
                confidence,
        }

        rows.append(
            row
        )

    return (
        rows,
        warnings
    )

# ============================================================
# FINANCIAL SUMMARY
# ============================================================

def extract_financial_summary(
    lines: list[str]
) -> dict:

    result = {

        "subtotal": "",

        "tax": "",

        "discount": "",

        "grand_total": "",
    }

    for line in lines:

        normalized = normalize_label(
            line
        )

        value = extract_first_number(
            line
        )

        if value is None:
            continue

        if any(
            normalized.startswith(
                label
            )
            for label in SUBTOTAL_LABELS
        ):

            result[
                "subtotal"
            ] = format_number(
                value
            )

        elif any(
            normalized.startswith(
                label
            )
            for label in TAX_LABELS
        ):

            result[
                "tax"
            ] = format_number(
                value
            )

        elif any(
            normalized.startswith(
                label
            )
            for label in DISCOUNT_LABELS
        ):

            result[
                "discount"
            ] = format_number(
                value
            )

        elif any(
            normalized.startswith(
                label
            )
            for label in GRAND_TOTAL_LABELS
        ):

            result[
                "grand_total"
            ] = format_number(
                value
            )

    return result


# ============================================================
# CUSTOMER
# ============================================================

def extract_customer(
    lines: list[str]
) -> tuple[str, str]:

    for index, line in enumerate(
        lines[:60]
    ):

        value, consumed = extract_after_label(
            lines,
            CUSTOMER_LABELS,
            index
        )

        if value:

            return (
                clean_field_value(value),
                "HIGH"
            )

    # Fallback: look for M/s
    for line in lines[:60]:

        match = re.search(
            r"\bM/s\.?\s+(.+)",
            line,
            flags=re.IGNORECASE
        )

        if match:

            return (
                clean_field_value(
                    match.group(1)
                ),
                "MEDIUM"
            )

    return (
        "",
        "LOW"
    )


# ============================================================
# SUBJECT
# ============================================================

def extract_subject(
    lines: list[str]
) -> tuple[str, str]:

    for index, line in enumerate(
        lines[:80]
    ):

        value, consumed = extract_after_label(
            lines,
            SUBJECT_LABELS,
            index
        )

        if value:

            return (
                clean_field_value(value),
                "HIGH"
            )

    return (
        "",
        "LOW"
    )


# ============================================================
# QUOTATION NUMBER
# ============================================================

def extract_quotation_number(
    lines: list[str]
) -> tuple[str, str]:

    for index in range(
        min(80, len(lines))
    ):

        value, consumed = extract_after_label(
            lines,
            QUOTATION_NUMBER_LABELS,
            index
        )

        if value:

            # A quotation number is always a single token with no
            # internal whitespace - trims trailing same-line text
            # ("2425W034R2 Dated: 27.06.2024" -> "2425W034R2").
            value = value.split()[0]

            # Some source PDFs glue the date label directly onto the
            # number with no space at all ("2425W090R1Dated:
            # 24.03.2025") - "Dated" is never legitimately part of a
            # quotation number, so strip it if it's stuck on the end.
            value = re.sub(
                r"dated:?$",
                "",
                value,
                flags=re.IGNORECASE
            )

            # "Ref: Q24250574-SQ2503N052" - only the part after the
            # dash is the actual quotation number; the first segment
            # is an unrelated internal reference code.
            if "-" in value:
                value = value.rsplit("-", 1)[-1]

            return (
                clean_field_value(value),
                "HIGH"
            )

    # Fallback: filename-like quotation number
    return (
        "",
        "LOW"
    )


# ============================================================
# DATE
# ============================================================

def extract_quotation_date(
    lines: list[str]
) -> tuple[str, str]:

    date_patterns = [

        r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b",

        r"\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b",

        r"\b\d{1,2}\s+"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"[a-z]*\s+\d{2,4}\b",
    ]

    for index, line in enumerate(
        lines[:100]
    ):

        normalized = normalize_label(
            line
        )

        if any(
            label in normalized
            for label in (
                "date",
                "dated"
            )
        ):

            for pattern in date_patterns:

                match = re.search(
                    pattern,
                    line,
                    flags=re.IGNORECASE
                )

                if match:

                    return (
                        match.group(0),
                        "HIGH"
                    )

    return (
        "",
        "LOW"
    )


# ============================================================
# CURRENCY
# ============================================================

def detect_currency(
    text: str
) -> str:

    upper = text.upper()

    if (
        "INR" in upper
        or "RS." in upper
        or "RS " in upper
        or "₹" in text
    ):

        return "INR"

    if (
        "USD" in upper
        or "$" in text
    ):

        return "USD"

    if (
        "EUR" in upper
        or "€" in text
    ):

        return "EUR"

    if (
        "GBP" in upper
        or "£" in text
    ):

        return "GBP"

    return ""


# ============================================================
# PARSE ONE DOCUMENT
# ============================================================

def parse_document(
    txt_path: Path
) -> tuple[dict, list[dict], list[dict]]:

    text = txt_path.read_text(
        encoding="utf-8",
        errors="replace"
    )

    lines = [
        line.strip()
        for line in text.splitlines()
    ]

    lines = [
        line
        for line in lines
        if line
    ]

    customer, customer_confidence = (
        extract_customer(
            lines
        )
    )

    subject, subject_confidence = (
        extract_subject(
            lines
        )
    )

    quotation_number, quotation_number_confidence = (
        extract_quotation_number(
            lines
        )
    )

    quotation_date, quotation_date_confidence = (
        extract_quotation_date(
            lines
        )
    )

    currency = detect_currency(
        text
    )

    header_index, header_mapping, original_headers = (
        detect_boq_header(
            lines
        )
    )

    items = []

    warnings = []

    table_detected = (
        header_index >= 0
    )

    if table_detected:

        boq_region = extract_boq_region(
            lines,
            header_index
        )

        items, boq_warnings = (
            parse_boq_rows(
                boq_region
            )
        )

        warnings.extend(
            boq_warnings
        )

    else:

        warnings.append(
            "BOQ_HEADER_NOT_FOUND"
        )

        original_headers = []

    financials = (
        extract_financial_summary(
            lines
        )
    )

    # --------------------------------------------------------
    # DOCUMENT CONFIDENCE
    # --------------------------------------------------------

    confidence_points = 0

    if customer:
        confidence_points += 1

    if subject:
        confidence_points += 1

    if quotation_number:
        confidence_points += 1

    if table_detected:
        confidence_points += 2

    if items:
        confidence_points += 2

    if confidence_points >= 6:

        document_confidence = "HIGH"

    elif confidence_points >= 4:

        document_confidence = "MEDIUM"

    else:

        document_confidence = "LOW"

    # --------------------------------------------------------
    # QUOTATION RECORD
    # --------------------------------------------------------

    quotation_record = {

        "source_file":
            txt_path.name,

        "source_path":
            str(txt_path),

        "quotation_number":
            quotation_number,

        "quotation_date":
            quotation_date,

        "customer":
            customer,

        "subject":
            subject,

        "currency":
            currency,

        "subtotal":
            financials["subtotal"],

        "tax":
            financials["tax"],

        "discount":
            financials["discount"],

        "grand_total":
            financials["grand_total"],

        "boq_detected":
            "YES"
            if table_detected
            else "NO",

        "boq_headers":
            " | ".join(
                original_headers
            ),

        "document_confidence":
            document_confidence,

        "customer_confidence":
            customer_confidence,

        "subject_confidence":
            subject_confidence,

        "quotation_number_confidence":
            quotation_number_confidence,

        "quotation_date_confidence":
            quotation_date_confidence,

        "warnings":
            "; ".join(
                warnings
            ),
    }

    # --------------------------------------------------------
    # ITEM RECORDS
    # --------------------------------------------------------

    item_records = []

    for item in items:

        item_record = {

    "source_file":
        txt_path.name,

    "source_path":
        str(txt_path),

    "quotation_number":
        quotation_number,

    "item_no":
        item["item_no"],

    "description":
        item["description"],

    "product":
        item["product"],

    "make":
        item["make"],

    "model":
        item["model"],

    "version":
        item["version"],

    "quantity":
        item["quantity"],

    "unit":
        item["unit"],

    "unit_price_raw":
        item["unit_price_raw"],

    "unit_price":
        item["unit_price"],

    "total_price_raw":
        item["total_price_raw"],

    "total_price":
        item["total_price"],

    "raw_row_text":
        item["raw_row_text"],

    "confidence":
        item["confidence"],
}

        item_records.append(
            item_record
        )

    # --------------------------------------------------------
    # REVIEW RECORDS
    # --------------------------------------------------------

    review_records = []

    if not customer:

        review_records.append({

            "source_file":
                txt_path.name,

            "field":
                "customer",

            "value":
                "",

            "confidence":
                customer_confidence,

            "reason":
                "Customer not detected",
        })

    if not subject:

        review_records.append({

            "source_file":
                txt_path.name,

            "field":
                "subject",

            "value":
                "",

            "confidence":
                subject_confidence,

            "reason":
                "Subject not detected",
        })

    if not table_detected:

        review_records.append({

            "source_file":
                txt_path.name,

            "field":
                "boq",

            "value":
                "",

            "confidence":
                "LOW",

            "reason":
                "BOQ header not detected",
        })

    for warning in warnings:

        review_records.append({

            "source_file":
                txt_path.name,

            "field":
                "document",

            "value":
                "",

            "confidence":
                document_confidence,

            "reason":
                warning,
        })

    for item in item_records:

        if item["confidence"] != "HIGH":

            review_records.append({

                "source_file":
                    txt_path.name,

                "field":
                    f"item_{item['item_no']}",

                "value":
                    item["description"],

                "confidence":
                    item["confidence"],

                "reason":
                    "BOQ row needs review",
            })

    return (
        quotation_record,
        item_records,
        review_records
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("QUOTATION STRUCTURED PARSER V1")
    print("=" * 70)

    print(
        f"\nInput:"
    )

    print(
        INPUT_FOLDER.resolve()
    )

    print(
        f"\nOutput:"
    )

    print(
        OUTPUT_FOLDER.resolve()
    )

    if not INPUT_FOLDER.exists():

        print(
            "\nERROR: Input folder not found."
        )

        return

    txt_files = sorted(
        INPUT_FOLDER.rglob("*.txt")
    )

    print(
        f"\nTXT files available: "
        f"{len(txt_files)}"
    )

    if not txt_files:

        print(
            "\nNo TXT files found."
        )

        return

    # --------------------------------------------------------
    # TEST LIMIT
    # --------------------------------------------------------

    if TEST_LIMIT is not None:

        txt_files = txt_files[
            :TEST_LIMIT
        ]

        print(
            f"TEST MODE: processing "
            f"{len(txt_files)} files"
        )

    else:

        print(
            "FULL MODE: processing "
            f"{len(txt_files)} files"
        )

    OUTPUT_FOLDER.mkdir(
        parents=True,
        exist_ok=True
    )

    quotation_rows = []
    item_rows = []
    review_rows = []

    successful = 0
    failed = 0

    # --------------------------------------------------------
    # PROCESS DOCUMENTS
    # --------------------------------------------------------

    for index, txt_file in enumerate(
        txt_files,
        start=1
    ):

        print(
            f"\n[{index}/{len(txt_files)}]"
        )

        print(
            f"File: {txt_file.name}"
        )

        try:

            quotation, items, reviews = (
                parse_document(
                    txt_file
                )
            )

            quotation_rows.append(
                quotation
            )

            item_rows.extend(
                items
            )

            review_rows.extend(
                reviews
            )

            successful += 1

            print(
                f"  Customer: "
                f"{quotation['customer'] or '[NOT FOUND]'}"
            )

            print(
                f"  Subject: "
                f"{quotation['subject'] or '[NOT FOUND]'}"
            )

            print(
                f"  BOQ: "
                f"{quotation['boq_detected']}"
            )

            print(
                f"  Items: "
                f"{len(items)}"
            )

            print(
                f"  Confidence: "
                f"{quotation['document_confidence']}"
            )

        except Exception as e:

            failed += 1

            print(
                f"  ERROR: {e}"
            )

            review_rows.append({

                "source_file":
                    txt_file.name,

                "field":
                    "document",

                "value":
                    "",

                "confidence":
                    "LOW",

                "reason":
                    f"PARSER_ERROR: {e}",
            })

    # ========================================================
    # WRITE QUOTATION CSV
    # ========================================================

    quotation_fields = [

        "source_file",
        "source_path",
        "quotation_number",
        "quotation_date",
        "customer",
        "subject",
        "currency",
        "subtotal",
        "tax",
        "discount",
        "grand_total",
        "boq_detected",
        "boq_headers",
        "document_confidence",
        "customer_confidence",
        "subject_confidence",
        "quotation_number_confidence",
        "quotation_date_confidence",
        "warnings",
    ]

    with open(
        QUOTATIONS_CSV,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=quotation_fields
        )

        writer.writeheader()

        writer.writerows(
            quotation_rows
        )

    # ========================================================
    # WRITE ITEM CSV
    # ========================================================

    item_fields = [

    "source_file",
    "source_path",
    "quotation_number",

    "item_no",

    "description",

    "product",
    "make",
    "model",
    "version",

    "quantity",

    "unit",

    "unit_price_raw",
    "unit_price",

    "total_price_raw",
    "total_price",

    "raw_row_text",

    "confidence",
]

    with open(
        ITEMS_CSV,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=item_fields
        )

        writer.writeheader()

        writer.writerows(
            item_rows
        )

    # ========================================================
    # WRITE REVIEW CSV
    # ========================================================

    review_fields = [

        "source_file",
        "field",
        "value",
        "confidence",
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
    # WRITE SUMMARY
    # ========================================================

    high_conf = sum(
        1
        for row in quotation_rows
        if row[
            "document_confidence"
        ] == "HIGH"
    )

    medium_conf = sum(
        1
        for row in quotation_rows
        if row[
            "document_confidence"
        ] == "MEDIUM"
    )

    low_conf = sum(
        1
        for row in quotation_rows
        if row[
            "document_confidence"
        ] == "LOW"
    )

    boq_count = sum(
        1
        for row in quotation_rows
        if row[
            "boq_detected"
        ] == "YES"
    )

    with open(
        SUMMARY_TXT,
        "w",
        encoding="utf-8"
    ) as file:

        file.write(
            "QUOTATION PARSER V1 SUMMARY\n"
        )

        file.write(
            "=" * 70
            + "\n\n"
        )

        file.write(
            f"Files processed: "
            f"{len(txt_files)}\n"
        )

        file.write(
            f"Successful: "
            f"{successful}\n"
        )

        file.write(
            f"Failed: "
            f"{failed}\n"
        )

        file.write(
            f"BOQ detected: "
            f"{boq_count}\n"
        )

        file.write(
            f"BOQ items extracted: "
            f"{len(item_rows)}\n\n"
        )

        file.write(
            "DOCUMENT CONFIDENCE\n"
        )

        file.write(
            f"High: "
            f"{high_conf}\n"
        )

        file.write(
            f"Medium: "
            f"{medium_conf}\n"
        )

        file.write(
            f"Low: "
            f"{low_conf}\n\n"
        )

        file.write(
            "OUTPUTS\n"
        )

        file.write(
            f"{QUOTATIONS_CSV}\n"
        )

        file.write(
            f"{ITEMS_CSV}\n"
        )

        file.write(
            f"{REVIEW_CSV}\n"
        )

    # ========================================================
    # FINAL CONSOLE
    # ========================================================

    print("\n")
    print("=" * 70)
    print("PARSER V1 COMPLETE")
    print("=" * 70)

    print(
        f"Processed: "
        f"{len(txt_files)}"
    )

    print(
        f"Successful: "
        f"{successful}"
    )

    print(
        f"Failed: "
        f"{failed}"
    )

    print(
        f"BOQ detected: "
        f"{boq_count}"
    )

    print(
        f"BOQ items extracted: "
        f"{len(item_rows)}"
    )

    print(
        f"High confidence: "
        f"{high_conf}"
    )

    print(
        f"Medium confidence: "
        f"{medium_conf}"
    )

    print(
        f"Low confidence: "
        f"{low_conf}"
    )

    print(
        "\nFiles created:"
    )

    print(
        f"  {QUOTATIONS_CSV}"
    )

    print(
        f"  {ITEMS_CSV}"
    )

    print(
        f"  {REVIEW_CSV}"
    )

    print(
        f"  {SUMMARY_TXT}"
    )

    print("\nOriginal TXT files were not modified.")


if __name__ == "__main__":
    main()