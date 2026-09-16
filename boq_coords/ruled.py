"""
Ties a located TableRegion to actual page words and runs row segmentation.
Thin glue: find_tables()/find_header() give column geometry (via locate.py),
banded.segment_rows() does the real row-finding work for both the ruled and
unruled paths alike (plan §Column bands: "ruled" and "unruled" differ only
in where bands came from, not in how rows are found).
"""
from __future__ import annotations

from boq_coords.banded import LogicalRow, segment_rows
from boq_coords.geometry import Word
from boq_coords.locate import TableRegion

# A price-column table cell taller than this multiple of the table's own
# typical single-row height is treated as a merged/rowspan cell (one price
# printed once for a GROUP of items, not per row) rather than an ordinary
# tall row.
SPANNED_PRICE_CELL_RATIO = 1.4


def _spanned_price_ranges(region: TableRegion) -> list[tuple[float, float]]:
    """Y-ranges of price-column cells that visibly span more than one
    physical row in the source table - detected directly from pymupdf's own
    per-row `table.rows[i].cells` bboxes, which is unambiguous (a merged
    cell's bbox is simply taller than its neighbours; pymupdf gives the
    rows it spans a `None` cell at that column).

    Confirmed real-corpus bug (Q24S10074_VOC_GC.pdf, smoke test
    2026-09-12): the source table states one price once for a group of 4
    items ("Gas Chromatograph with SHS" through "Sample heat tracer line");
    banded.segment_rows has no notion the cell is merged, so it attributes
    the price text to whichever row-bucket its vertical centre happens to
    land in - silently, confidence=HIGH, no validation flag. This function
    only *detects* the condition and never changes any emitted value - see
    validate.py's PRICE_CELL_SPANS_MULTIPLE_ROWS rule, which flags every
    row whose own y-range falls inside one of the ranges returned here.

    The comparison is deliberately made against THIS SAME table-row's own
    other (non-price) cells, not a table-wide median - an ordinary row
    whose price simply wraps to two lines (matching that row's own,
    equally-tall item_no/description cells) must not be flagged. Only a
    price cell that is taller than its OWN row's siblings is evidence the
    cell actually extends into rows below it (confirmed false-positive on
    a real document during smoke testing: Q25AKIC10080_Blue
    NH3_LINDE_20012025.pdf has many genuinely two-line-wrapped price cells
    whose row-wide height matches every other cell in that same row -
    comparing against a corpus-wide baseline flagged all of them; comparing
    within the row does not).

    Returns [] whenever `region.table` is unavailable (the unruled path,
    and unheaded continuation pages, which build a throwaway region with
    `table=None`) - this check is only possible where pymupdf's own table
    geometry exists to consult.
    """
    if region.table is None:
        return []

    body_rows = region.table.rows[region.header.start + region.header.span:]
    if not body_rows:
        return []

    price_cols = [
        col for col, field in region.header.field_by_col.items()
        if field in ("unit_price", "total_price")
    ]
    if not price_cols:
        return []

    # The baseline must come from other NAMED columns (item_no,
    # description, quantity, ...) only - not every non-None cell. A table
    # can have unnamed phantom sub-pixel border columns alongside the real
    # ones (columns.build_bands_from_table already drops these as bands
    # for exactly this reason: "phantom sub-pixel border columns"), and
    # such a column's own short height is not a meaningful row baseline -
    # confirmed as a real false-positive source during smoke testing: an
    # 8-point-tall unnamed column dragged the baseline down to a fifth of
    # every real column's own height, flagging every ordinary two-line
    # price cell in the table as if it spanned multiple rows.
    named_non_price_cols = [
        col for col in region.header.field_by_col
        if col not in price_cols
    ]

    ranges: list[tuple[float, float]] = []
    for row in body_rows:
        cells = getattr(row, "cells", None) or []

        non_price_heights = [
            cells[col][3] - cells[col][1] for col in named_non_price_cols
            if col < len(cells) and cells[col]
        ]
        if not non_price_heights:
            continue
        row_baseline = min(non_price_heights)
        if row_baseline <= 0:
            continue

        for col in price_cols:
            if col >= len(cells) or cells[col] is None:
                continue
            _, y0, _, y1 = cells[col]
            if (y1 - y0) > SPANNED_PRICE_CELL_RATIO * row_baseline:
                ranges.append((y0, y1))

    return ranges


def _ruling_line_ys(page, region: TableRegion) -> list[float]:
    """Horizontal ruling segments spanning most of the table width, from
    the page's vector drawings - the strongest row-boundary signal when
    present (plan §Row segmentation, boundary signal priority)."""
    x0, y0, x1, y1 = region.bbox
    min_span = 0.6 * (x1 - x0)
    ys: list[float] = []
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001
        return ys
    for d in drawings:
        for item in d.get("items", []):
            if item[0] != "l":  # line segment
                continue
            p1, p2 = item[1], item[2]
            if abs(p1.y - p2.y) > 1.0:
                continue  # not horizontal
            if not (y0 - 2 <= p1.y <= y1 + 2):
                continue
            span = abs(p2.x - p1.x)
            if span >= min_span:
                ys.append(p1.y)
    return sorted(set(round(y, 1) for y in ys))


def rows_from_region(page, region: TableRegion) -> list[LogicalRow]:
    x0, y0, x1, y1 = region.bbox
    if region.table is not None:
        header_rows = region.table.rows[region.header.start: region.header.start + region.header.span]
        header_bottom_y = max(
            (c[3] for r in header_rows for c in (getattr(r, "cells", None) or []) if c),
            default=y0,
        )
    else:
        # Unruled region: bbox.y0 is already set to the header line's own
        # y1 (see locate._find_unruled_region), so it already excludes the
        # header text - no table object to consult for cell geometry.
        header_bottom_y = y0

    words_raw = page.get_text("words", clip=(x0, header_bottom_y - 1, x1, y1))
    words = [Word.from_pymupdf_tuple(w[:8]) for w in words_raw if w[4].strip()]

    ruling_ys = _ruling_line_ys(page, region)
    rows = segment_rows(words, region.bands, ruling_ys=ruling_ys or None)

    spanned_ranges = _spanned_price_ranges(region)
    if spanned_ranges:
        for row in rows:
            if not row.lines:
                continue
            row_y0 = min(ln.y0 for ln in row.lines)
            row_y1 = max(ln.y1 for ln in row.lines)
            if any(row_y0 < y1 and row_y1 > y0 for y0, y1 in spanned_ranges):
                row.flags.append("PRICE_CELL_SPANS_MULTIPLE_ROWS")

    return rows
