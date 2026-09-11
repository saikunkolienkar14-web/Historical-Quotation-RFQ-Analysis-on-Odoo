"""
Header detection and column-band construction.

Design note (plan Step 3): a first measurement pass that trusted
`table.header.names` only found 50% BOQ-table coverage on a 150-doc sample,
because that field returns the wrong row whenever a header spans more than
one physical row - very common in this corpus ("UNIT PRICE" / "(INR)" on
two separate lines). A sliding-window, column-wise merge over the first
few rows moved that to 80%. That merge is `find_header()` below.
"""
from __future__ import annotations

from dataclasses import dataclass

from boq_coords.geometry import ColumnBand
from boq_coords.vocab import classify_header_word, is_ambiguous_total_price_header

MAX_HEADER_START = 6
# Confirmed on a real document: a 4-physical-row header where different
# columns' text is staggered across rows (e.g. "SR" on row 3, "NO" two
# rows later on row 5, with an unrelated "INR"/"INR" row for other
# columns in between) - a span of 3 missed it entirely, so item_no never
# got classified and row segmentation fell back to its weakest signal.
MAX_HEADER_SPAN = 5


@dataclass
class HeaderMatch:
    start: int          # first table-row index that is part of the header
    span: int           # number of physical rows merged into the header
    field_by_col: dict[int, str]   # column index -> schema field
    coverage: int        # number of distinct fields matched


def _cell_text(cell) -> str:
    return (cell or "").strip()


def _row_has_money(row: list[str | None], money_check) -> bool:
    return any(c and money_check(c) for c in row if c)


def find_header(rows: list[list[str | None]], money_check) -> HeaderMatch | None:
    """Slide a (start, span<=3) window over the first MAX_HEADER_START rows,
    join column-wise, and accept the window whose merged text matches
    description + at least one of (unit_price, total_price, a generic
    price). Reject any window whose merged text itself contains a money
    token - that guards against swallowing the first real data row into
    the header (plan §Locating the BOQ region, point 3).
    """
    if not rows:
        return None
    n_cols = max((len(r) for r in rows), default=0)
    if n_cols == 0:
        return None

    best: HeaderMatch | None = None
    limit = min(len(rows), MAX_HEADER_START)

    for start in range(limit):
        for span in range(1, MAX_HEADER_SPAN + 1):
            end = start + span
            if end > len(rows):
                break
            window = rows[start:end]

            field_by_col: dict[int, str] = {}
            ambiguous_total_cols: set[int] = set()
            merged_has_money = False
            for col in range(n_cols):
                parts = []
                for r in window:
                    if col < len(r):
                        parts.append(_cell_text(r[col]))
                # Check each cell INDIVIDUALLY for money, not just the
                # column-wise concatenation - confirmed as a real gap: a
                # window merging two priced data rows ("1" and "3") into one
                # column produced "1 3" (or, with a label prefix,
                # "Total 1 3"), which itself doesn't match the money
                # grammar even though every cell that built it obviously
                # is money. That let a data-row-swallowing window slip
                # past this guard.
                if any(p and money_check(p) for p in parts):
                    merged_has_money = True
                merged = " ".join(p for p in parts if p).strip()
                if not merged:
                    continue
                if money_check(merged):
                    merged_has_money = True
                field = classify_header_word(merged)
                if field:
                    field_by_col[col] = field
                    if field == "total_price" and is_ambiguous_total_price_header(merged):
                        ambiguous_total_cols.add(col)

            if merged_has_money:
                continue  # window swallowed a data row - reject

            # A bare "Total" only counts as total_price when a unit_price
            # column is also present - otherwise it's more likely a total
            # QUANTITY column (confirmed on a real "MAKE LIST" table: a lone
            # "Total" column with no unit_price anywhere in the table was
            # really a quantity count, not a price).
            if ambiguous_total_cols and "unit_price" not in field_by_col.values():
                for col in ambiguous_total_cols:
                    del field_by_col[col]

            fields_found = set(field_by_col.values())
            if "description" not in fields_found:
                continue
            if not (fields_found & {"unit_price", "total_price"}):
                continue

            coverage = len(fields_found)
            candidate = HeaderMatch(start=start, span=span, field_by_col=field_by_col, coverage=coverage)

            if best is None or coverage > best.coverage or (
                coverage == best.coverage and span < best.span
            ) or (
                coverage == best.coverage and span == best.span and start < best.start
            ):
                best = candidate

    # Deliberately scan every (start, span) window rather than breaking
    # early on the first decent match: a banner row at start=0 ("NEW
    # ANALYSER") can bleed into a merged window and corrupt one column's
    # text (e.g. "NEW ANALYSER SR." fails to classify as item_no), so a
    # later start with a cleaner merge can score strictly higher coverage.
    # Verified empirically - see boq_coords design notes.
    return best


def build_bands_from_table(table, header: HeaderMatch) -> list[ColumnBand]:
    """Union header cell bboxes per matched column over the header's row
    span, then classify each resulting band by BOQ_HEADER_ALIASES. Columns
    with no cells across the whole span (phantom sub-pixel border columns -
    confirmed empirically: e.g. a real 11-column table where only columns
    1, 3, 4, 6, 9 carry header text, the rest are ~5pt border artifacts)
    are dropped rather than emitted as a band.

    pymupdf's Table has no public column-edge accessor; each row exposes
    `.cells`, a list of (x0,y0,x1,y1)-or-None, one entry per column index,
    verified against a real document's find_tables() output. We union the
    bboxes of the matched header rows for each field-bearing column.
    """
    col_xranges: dict[int, list[float]] = {}
    try:
        header_rows = table.rows[header.start: header.start + header.span]
    except Exception:  # noqa: BLE001
        header_rows = []
    for row in header_rows:
        for col_idx, cell_bbox in enumerate(getattr(row, "cells", []) or []):
            if cell_bbox is None:
                continue
            x0, _, x1, _ = cell_bbox
            col_xranges.setdefault(col_idx, []).extend([x0, x1])

    bands: list[ColumnBand] = []
    for col_idx, field in header.field_by_col.items():
        xs = col_xranges.get(col_idx)
        if not xs:
            continue
        bands.append(ColumnBand(field=field, x0=min(xs), x1=max(xs)))

    return resolve_duplicate_price_bands(bands)


def resolve_duplicate_price_bands(bands: list[ColumnBand]) -> list[ColumnBand]:
    """A rare but real header prints the SAME price label in two columns
    (confirmed on a real document: both money columns literally say
    'TOTAL PRICE (INR)' - the source PDF's own authoring error, not a
    classifier bug). Two ColumnBands sharing one field name make
    banded._classify_lines concatenate both columns' money text into one
    string (e.g. "Rs 12,50,000 Rs 12,50,000"), which then fails the
    single-value money grammar and silently reverts the whole price to
    description - a real price captured in raw_row_text but dropped from
    every priced field. If exactly two bands share a price field name and
    the other price field is absent, resolve by column order - this
    corpus's near-universal convention is unit price left of total price."""
    total_bands = [b for b in bands if b.field == "total_price"]
    unit_bands = [b for b in bands if b.field == "unit_price"]

    if len(total_bands) == 2 and not unit_bands:
        left, right = sorted(total_bands, key=lambda b: b.x0)
        bands = [b for b in bands if b not in total_bands]
        bands.append(ColumnBand(field="unit_price", x0=left.x0, x1=left.x1))
        bands.append(right)
    elif len(unit_bands) == 2 and not total_bands:
        left, right = sorted(unit_bands, key=lambda b: b.x0)
        bands = [b for b in bands if b not in unit_bands]
        bands.append(left)
        bands.append(ColumnBand(field="total_price", x0=right.x0, x1=right.x1))

    return bands
