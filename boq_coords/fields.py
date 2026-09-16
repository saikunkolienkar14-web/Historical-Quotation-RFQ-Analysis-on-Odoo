"""
make / model extraction from a row's description text.

Ported from quotation_parser_v1.py:246 extract_labeled_fields() - same
label-driven logic (only explicit "Make:"/"Model:" style labels are used,
never guessed from unlabeled text), adapted to operate on a list of already
line-ordered description strings rather than v1's flat file lines.

product/version were dropped from the schema (never populated in this
corpus - it only ever uses "Make:"/"Model:" inline labels, never
"Product:"/"Version:")."""
from __future__ import annotations

import re

LABELED_ITEM_FIELDS = ["make", "model"]

LABEL_SPLIT_PATTERN = re.compile(
    r"\b(?P<label>" + "|".join(LABELED_ITEM_FIELDS) + r")\b\s*[:\-]",
    re.IGNORECASE,
)


def clean_field_value(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" ,;")


def extract_labeled_fields(lines: list[str]) -> tuple[dict, list[str]]:
    """quotation_parser_v1.py:246, ported. Returns (fields, remaining_lines)."""
    fields = {f: "" for f in LABELED_ITEM_FIELDS}
    consumed: set[int] = set()

    for index, line in enumerate(lines):
        if index in consumed:
            continue
        stripped = line.strip()
        matches = list(LABEL_SPLIT_PATTERN.finditer(stripped))
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
            value = stripped[value_start:value_end].strip(" ,;")
            if value:
                fields[field] = clean_field_value(value)
                consumed.add(index)
                matched_any_value = True

        if matched_any_value:
            continue

        for f in LABELED_ITEM_FIELDS:
            if fields[f]:
                continue
            if re.match(rf"^{f}\s*[:\-]?$", stripped, flags=re.IGNORECASE):
                if index + 1 < len(lines) and lines[index + 1].strip():
                    fields[f] = clean_field_value(lines[index + 1])
                    consumed.add(index)
                    consumed.add(index + 1)
                break

    remaining = [ln for i, ln in enumerate(lines) if i not in consumed]
    return fields, remaining


HEADING_LABEL_RX = re.compile(r"^([A-Za-z][A-Za-z /]{1,30}):\s", re.IGNORECASE)

# Bullet glyphs that end a heading. U+2022/25CF/25AA are the real Unicode
# bullets; U+F0B7/F06C/F0A7 are Private-Use-Area codepoints that Wingdings
# and Symbol fonts map their bullet glyphs to, and which these source PDFs
# actually emit (confirmed: 113 occurrences of U+F0B7 across the corpus).
# Missing the PUA ones let the accumulator run straight past the bullet
# list and swallow an item's whole spec block - sometimes several merged
# items plus letterhead boilerplate - producing 700-char product_names.
BULLET_CHARS = ("•", "●", "▪", "", "", "")

HEADING_MAX_WORDS = 15


def extract_heading(text: str, max_words: int = HEADING_MAX_WORDS) -> str:
    """Short product-name heading off the top of a full description.

    Accumulates lines until a labeled field ("Service:", "Make:", ...), a
    "With " clause, or a bullet starts - whichever comes first. The full
    description (`text`) is kept unchanged elsewhere as `description_full`;
    this only derives an additional heading value for price-trend grouping
    and the product-list deliverable.

    Always returns something for non-empty input: accessory/spec-only
    sub-items often open directly on a stop line ("with welded isolation
    valve, MOC: PVDF"), which would otherwise collect nothing at all, so
    those fall back to a capped excerpt. A word cap applies either way, so
    a row-segmentation failure upstream can't leak a whole merged table
    into this field.
    """
    heading_lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if (HEADING_LABEL_RX.match(stripped)
                or stripped.lower().startswith("with ")
                or stripped.startswith(BULLET_CHARS)):
            break
        heading_lines.append(stripped)

    heading = " ".join(heading_lines).strip()

    if not heading:
        heading = " ".join(text.split()[:max_words])

    words = heading.split()
    if len(words) > max_words:
        heading = " ".join(words[:max_words]) + "..."

    return heading


# A number-letter sub-item marker ("4a", "12 B") - the letter denotes a
# child of the numbered parent, same as a dotted-decimal "4.1" does.
ITEM_LETTER_SUFFIX_RX = re.compile(r"^(?P<parent>\d{1,3})\s*(?P<letter>[A-Za-z])$")


def derive_item_hierarchy(item_no: str) -> tuple[str, int]:
    """Split a printed item number into (parent_item_no, item_level).

    "1" -> ("", 1); "1.1" -> ("1", 2); "1.1.2" -> ("1.1", 3); "4a" ->
    ("4", 2). item_no is kept as TEXT exactly as printed - "1.1" is a
    two-level item marker, not the number 1.1 - so the hierarchy is read
    off the string rather than inferred from a numeric value.

    Returns ("", 1) for a top-level item and ("", 0) for a blank item_no,
    so level 0 unambiguously means "no item number at all".
    """
    text = (item_no or "").strip()
    if not text:
        return "", 0

    core = text.rstrip(".)")

    if "." in core:
        parts = [p for p in core.split(".") if p]
        if len(parts) > 1:
            return ".".join(parts[:-1]), len(parts)
        return "", 1

    match = ITEM_LETTER_SUFFIX_RX.match(core)
    if match:
        return match.group("parent"), 2

    return "", 1


PART_NO_LABEL_RX = re.compile(r"^PART\s*NO\s*:\s*(.+)$", re.IGNORECASE)


def promote_part_no_to_model(description_lines: list[str], model: str) -> str:
    """A part-number column has no slot in the 17-column schema, so
    columns.py folds it into description as a trailing "PART NO: X" line
    (plan §Column bands). If no explicit Model: label was found, promote
    that part number into model rather than losing it as an identifier."""
    if model:
        return model
    for line in description_lines:
        m = PART_NO_LABEL_RX.match(line.strip())
        if m:
            return clean_field_value(m.group(1))
    return model
