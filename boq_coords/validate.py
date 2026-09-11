"""
Row validator - Step 4 of the plan. Every extracted row must pass:

  1. Traceability  - every number in unit_price/total_price/quantity
                      appears verbatim somewhere in raw_row_text.
  2. Arithmetic     - quantity * unit_price == total_price within 1 rupee,
                      when both are numeric.
  3. Price form     - numeric only with Indian comma grouping, Lakhs/Cr, or
                      a recognized sentinel (QUOTED/Inclusive/etc). Anything
                      else is null, not a guess.
  4. Quantity form  - a number followed by a unit word. A bare number with
                      no unit is not a quantity.
  5. Price bounds   - never negative, never below 1000.
  6. item_no shape  - a single well-formed anchor, never multiple
                      concatenated item numbers or unrelated swept-in text.

Rows are never dropped - a failing row gets confidence=LOW and the failed
rule name(s) in validation_error, same as the plan specifies.
"""
from __future__ import annotations

import re

from boq_coords.banded import is_clean_item_no
from boq_coords.money import (
    arithmetic_ok,
    parse_price,
    parse_quantity,
    price_in_bounds,
)
from boq_coords.vocab import UNITS_RE

NUMBER_TOKEN_RX = re.compile(r"[\d,]+(?:\.\d+)?")


def _numbers_in(text: str) -> set[str]:
    return set(NUMBER_TOKEN_RX.findall(text or ""))


def _digits_only(s: str) -> str:
    return re.sub(r"[^\d]", "", s)


def validate_row(row: dict) -> list[str]:
    """row: dict with keys quantity, unit, unit_price_raw, unit_price,
    total_price_raw, total_price, raw_row_text (strings, numeric fields
    already parsed to '' or a number-as-string). Returns a list of failed
    rule names (empty if the row passes everything)."""
    errors: list[str] = []
    raw_row_text = row.get("raw_row_text", "") or ""
    raw_digits = _digits_only(raw_row_text)

    # Rule 1: traceability
    for field in ("unit_price", "total_price", "quantity"):
        val = row.get(field)
        if val in (None, ""):
            continue
        val_str = str(val)
        # Compare on digits only so "975000" matches "9,75,000.00" verbatim
        # in the source text regardless of comma/decimal formatting.
        digits = _digits_only(val_str.split(".")[0])
        if digits and digits not in raw_digits:
            errors.append(f"NOT_VERBATIM_IN_RAW_TEXT:{field}")

    # Rule 2: arithmetic
    qty = _to_float(row.get("quantity"))
    unit_price = _to_float(row.get("unit_price"))
    total_price = _to_float(row.get("total_price"))
    if not arithmetic_ok(qty, unit_price, total_price):
        errors.append("PRICE_ARITHMETIC_MISMATCH")

    # Rule 3: price form (re-derive from *_raw to catch a value that was
    # written as numeric without ever passing through parse_price)
    for raw_field, num_field, is_unit in (
        ("unit_price_raw", "unit_price", True),
        ("total_price_raw", "total_price", False),
    ):
        raw_val = row.get(raw_field, "") or ""
        num_val = row.get(num_field)
        if num_val in (None, ""):
            continue
        parsed = parse_price(raw_val)
        if parsed.value is None:
            errors.append(f"INVALID_PRICE_FORM:{num_field}")

    # Rule 4: quantity form. Some templates carry quantity and unit in two
    # SEPARATE table columns (money.resolve_quantity_and_unit) - in that
    # case quantity_raw is deliberately just the bare number (the unit was
    # never glued onto it), so re-deriving quantity form from quantity_raw
    # alone false-positives on perfectly valid rows. Trust row["unit"]
    # directly when it's already a recognized unit word, exactly like
    # resolve_quantity_and_unit does at extraction time.
    if row.get("quantity") not in (None, ""):
        unit_val = (row.get("unit") or "").strip()
        has_valid_unit_column = bool(unit_val) and bool(UNITS_RE.match(unit_val.rstrip(".")))
        if not has_valid_unit_column:
            qty_raw = row.get("quantity_raw", row.get("quantity", "")) or ""
            pq = parse_quantity(str(qty_raw))
            if pq.value is None:
                errors.append("QUANTITY_MISSING_UNIT")

    # Rule 5: price bounds
    if unit_price is not None and not price_in_bounds(unit_price, is_unit=True):
        errors.append("PRICE_OUT_OF_RANGE:unit_price")
    if total_price is not None and not price_in_bounds(total_price, is_unit=False):
        errors.append("PRICE_OUT_OF_RANGE:total_price")

    # Rule 6: item_no shape. item_no_raw is the pre-fallback band text
    # (__main__.py substitutes the sequential item index when this fails)
    # - flagging it here, rather than silently substituting with no trace,
    # is what makes confidence actually reflect this failure mode (57 rows
    # in a 50-doc smoke test had garbled item_no like "2 .1 .2 .3" while
    # still showing confidence=HIGH before this rule existed).
    item_no_raw = row.get("item_no_raw", row.get("item_no", "")) or ""
    if item_no_raw and not is_clean_item_no(item_no_raw):
        errors.append("ITEM_NO_MALFORMED")

    return errors


def _to_float(v) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def apply_validation(row: dict) -> dict:
    """Mutates a copy of `row` with confidence/validation_error set per the
    outcome of validate_row(), and returns it. Never drops the row."""
    out = dict(row)
    errors = validate_row(row)
    if errors:
        out["confidence"] = "LOW"
        out["validation_error"] = ";".join(errors)
    else:
        out.setdefault("validation_error", "")
    return out
