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
        # "S.N." / "SN." - confirmed real-corpus header on Q25X10031's two
        # H2 Analyser quotations ("SN." and "S.N."), which classify_header_word
        # never recognized as item_no at all, so the printed item numbers
        # 1-6 were discarded and __main__.py's sequential-index fallback
        # fabricated a different, coincidentally-matching numbering instead.
        # classify_header_word tries both the period-as-space and
        # period-removed reading of every alias, so this one entry alone
        # matches both "S.N." ("s n") and "SN." ("sn").
        "s.n.",
        "no", "no.", "item no", "item no.", "item number", "item",
    ],
    "description": [
        "description", "item description", "material description",
        "product description", "equipment description", "particulars",
        "details", "scope of supply", "material", "product",
    ],
    "quantity": [
        "qty", "qty.", "quantity", "quantity nos", "qnty",
        # "No.of Qty" / "No. of Units": a count, not a serial number - must
        # out-length item_no's bare "no" (longest alias wins).
        "no of",
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

# banded.py's anchor-boundary "terminal punctuation" heuristic (does this
# line end a sentence, or just a mid-sentence abbreviation?) needs to tell
# a real sentence-ending period apart from one that closes an abbreviation
# like "...datasheets doc." (short for "document", sentence continues on
# the next physical line: "No. E0780601-... and technical mentioned in
# MR"). Confirmed on Q24X10030's Section-I BOM table: every item heading
# ends this way, so without this exception every row lost its own heading
# to the previous row and gained the next row's tail - a systematic,
# whole-table off-by-one. Lowercase, no trailing period (stripped by the
# caller before lookup).
NON_TERMINAL_ABBREVIATIONS: set[str] = {
    "no", "doc", "dwg", "drg", "fig", "ref", "std", "spec", "rev",
    "approx", "qty", "pt",
}

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

# A numbered document-section heading ("Section 2:", "Part B:", "Section-3
# Clarifications & Deviations") printed as its own short line - never part
# of the priced table itself. The number can be glued directly onto
# "Section"/"Part" with a hyphen instead of a space (confirmed real-corpus
# case, Q24X10030's "Section-3 Clarifications & Deviations" - an entirely
# different compliance/deviation table, not a BOQ continuation - which the
# original space-then-punctuation-only pattern below missed because the
# hyphen comes BEFORE the digit here, not after it). Loosely based on
# locate.TOC_SECTION_RX's own pattern (kept here too since vocab.py has no
# dependency on locate.py), broadened to accept the glued form and to not
# require trailing punctuation at all.
SECTION_HEADING_NUMBERED_RX = re.compile(r"^(section|part)\s*[-:.]?\s*[0-9a-z]+\b", re.IGNORECASE)

# Post-table narrative headings confirmed on real documents that
# STOP_SECTION_MARKERS doesn't cover - printed as a short heading line
# AFTER a document's BOQ table, never inside it (Q25X10031R1's "Section 2:
# Notes & Clarifications" / "Section 3: Exclusions"; Q24X10030's
# "Section-3 Clarifications & Deviations", an entirely different
# compliance/deviation table). Checked only against a short, standalone
# line (SECTION_HEADING_MAX_WORDS) - a real item description mentioning
# one of these words mid-sentence is far longer and is never mistaken for
# a heading.
#
# Deliberately does NOT include "technical literature": confirmed real-
# corpus case, Q24S10074/Q24W10129R1 both print "SECTION 2: TECHNICAL
# LITERATURE" as a running section TITLE with MORE real priced rows
# resuming right after it, never a stop signal - an earlier version of
# this list included it and lost those rows entirely. The lesson that
# leaves: a bare "Section N:" NUMBERED prefix is never trusted as a stop
# signal on its own (see is_post_table_heading_line) - only the specific
# WORDS that follow it decide, exactly as if the prefix wasn't there.
POST_TABLE_HEADING_PHRASES: list[str] = [
    "notes and clarifications",
    "notes clarifications",
    "exclusions",
    "clarifications and deviations",
]

SECTION_HEADING_MAX_WORDS = 8


def is_post_table_heading_line(text: str) -> bool:
    """True when `text` (one physical line's own words) is a heading that
    marks the end of the priced table - a short line matching
    POST_TABLE_HEADING_PHRASES or STOP_SECTION_MARKERS, with a leading
    numbered prefix ("Section 2:", "Section-3") stripped first if present
    so the match is always against the words that actually say what the
    section IS, never the bare number (see POST_TABLE_HEADING_PHRASES'
    own "technical literature" comment for why the number alone is not
    trustworthy). Used to cut a continuation page's word range off at
    this line's own top edge, rather than reading further narrative text
    as more table rows - see rows._first_heading_y."""
    stripped = text.strip()
    if not stripped:
        return False
    m = SECTION_HEADING_NUMBERED_RX.match(stripped)
    body = stripped[m.end():].lstrip(" :.-") if m else stripped
    norm = normalize_label(body)
    if not norm or len(norm.split()) > SECTION_HEADING_MAX_WORDS:
        return False
    return any(
        norm == phrase or norm.startswith(phrase + " ")
        for phrase in (*POST_TABLE_HEADING_PHRASES, *STOP_SECTION_MARKERS)
    )

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

# A row whose only "item" is a running total, never a priced line item of
# its own - confirmed real-corpus case: Q24S10070R1's own bare "TOTAL"
# label (not covered by GRAND_TOTAL_LABELS/SUBTOTAL_LABELS above, which
# only match multi-word phrases) landed as its own row with a real price
# next to it and no item number, read exactly like a legitimate item.
TOTAL_ROW_LABELS: list[str] = ["total", *GRAND_TOTAL_LABELS, *SUBTOTAL_LABELS]


def is_total_row_label(text: str) -> bool:
    """True when `text` IS a total label, exactly, not just contains or
    starts with one - "Total Conductivity Analyser" (a real item's own
    heading, confirmed real-corpus case on the same document family) must
    never match; only a cell whose ENTIRE text is "Total"/"Grand Total"/
    etc, exactly as printed in one column with nothing else, does."""
    norm = normalize_label(text)
    return norm in TOTAL_ROW_LABELS

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
    # Period -> SPACE, not -> "": a glued "SR.NO." must read "sr no", not
    # "srno" (which matches no alias, so the table got no item_no column at
    # all). 707 documents carry the glued form; on an 80-doc sample of them
    # item_no detection went 27 -> 74 (2026-09-23, Q2501N005 investigation).
    text = text.replace(".", " ")
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
            # Both readings of an alias's period: as a word break ("s.no" ->
            # "s no", matching a glued "S.NO." cell) and as nothing ("sno",
            # matching an undotted "SNO" cell - which the space reading
            # alone stopped matching, 6 cells in a 400-doc scan).
            for alias_n in {re.sub(r"\s+", " ", alias.replace(".", " ")).strip(), alias.replace(".", "")}:
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
