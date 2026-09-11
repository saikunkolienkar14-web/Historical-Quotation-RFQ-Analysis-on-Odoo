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
