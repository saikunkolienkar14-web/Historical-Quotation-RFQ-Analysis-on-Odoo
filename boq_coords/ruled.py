"""
Ties a located TableRegion to actual page words and runs row segmentation.
Thin glue: find_tables()/find_header() give column geometry (via locate.py),
banded.segment_rows() does the real row-finding work for both the ruled and
unruled paths alike (plan §Column bands: "ruled" and "unruled" differ only
in where bands came from, not in how rows are found).
"""
from __future__ import annotations

from boq_coords.banded import LogicalRow, detect_layout, segment_rows
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


RULING_MIN_SPAN_FRACTION = 0.6
# A row divider drawn only under the description column (this corpus's
# Q25X10031 H2 Analyser tables: the quantity/price columns carry no rule
# at all) never reaches RULING_MIN_SPAN_FRACTION of the whole table width,
# so it's also accepted on covering most of the description band alone -
# but only once it REPEATS (RULING_DESC_ONLY_MIN_REPEATS below): on its
# own this width is indistinguishable from a decorative underline drawn
# under one heading/highlighted phrase, which a real, unrelated document
# in this corpus (Q2501N005) does exactly once at a width that otherwise
# passes this same check.
RULING_DESC_MIN_SPAN_FRACTION = 0.8
# A desc-band-only-width candidate is trusted as a genuine row divider only
# once at least this many of them appear on the same page - a real ruled
# table draws one under EVERY row, never just once (see
# RULING_DESC_MIN_SPAN_FRACTION).
RULING_DESC_ONLY_MIN_REPEATS = 3
# A ruling drawn as a filled rectangle rather than a line segment (same
# corpus) is a hairline - taller than that and it's a real cell/table
# border, not a row divider.
RULING_MAX_RECT_HEIGHT = 1.5
# The same divider is sometimes drawn twice, a fraction of a point apart
# (confirmed real-corpus: y=175.6 and y=176.1 on the same page) - collapse
# duplicates within this tolerance so they don't produce a phantom
# zero-height row between them. Also the tolerance used to decide whether
# two marks sit at "the same y" at all, below.
RULING_MERGE_TOLERANCE = 1.5
# Two horizontal marks at the same y with a real, un-inked gap wider than
# this are two SEPARATE decorative underlines drawn under two different
# cell values on one printed line, never one continuous row-divider -
# confirmed real-corpus false positive: Q2501N005 underlines its
# description, unit-price and total-price VALUES independently (three
# short marks on the same line, each under its own value, with wide gaps
# between them), which without this check reads exactly like one drawn
# rule spanning most of the row.
RULING_MAX_GROUP_GAP = 8.0
# A mark this narrow (a single underlined digit, a bullet dash) is never
# by itself evidence of a second, competing mark on the same line -
# excluded from the "multiple disjoint marks" check above so it can't
# block a genuine, wider divider that happens to share its y by coincidence.
RULING_MIN_SIBLING_WIDTH = 20.0


def _merge_close_ys(ys: list[float], tol: float = RULING_MERGE_TOLERANCE) -> list[float]:
    if not ys:
        return []
    ys = sorted(ys)
    merged = [ys[0]]
    for y in ys[1:]:
        if y - merged[-1] > tol:
            merged.append(y)
    return merged


def _group_x_ranges(ranges: list[tuple[float, float]], gap: float = RULING_MAX_GROUP_GAP) -> list[tuple[float, float]]:
    """Merge x-ranges that touch or nearly touch into single spans, leaving
    genuinely separate marks (a real gap wider than `gap`) as distinct
    groups - see RULING_MAX_GROUP_GAP."""
    groups: list[tuple[float, float]] = []
    for lo, hi in sorted(ranges):
        if groups and lo <= groups[-1][1] + gap:
            groups[-1] = (groups[-1][0], max(groups[-1][1], hi))
        else:
            groups.append((lo, hi))
    return groups


def _ruling_line_ys(page, region: TableRegion) -> list[float]:
    """Horizontal ruling segments spanning most of the table width, from
    the page's vector drawings - the strongest row-boundary signal when
    present (plan §Row segmentation, boundary signal priority).

    Two drawing primitives count as a ruling: an actual line segment, and a
    filled rectangle no taller than RULING_MAX_RECT_HEIGHT (confirmed
    real-corpus: Q25X10031's H2 Analyser quotations draw every row divider
    as a hairline-height filled rectangle, never a line - the original
    line-only check found zero rulings on either document, silently
    falling through to the money-line/gap-derived boundary paths, which is
    what produced the two merged-row bugs this exists to fix).

    Every candidate mark is grouped by y first (RULING_MERGE_TOLERANCE) and
    then by x-adjacency (_group_x_ranges) before either width check is
    applied, so a row of several short, separately-drawn underlines (one
    per cell value) is never mistaken for one continuous divider - see
    RULING_MAX_GROUP_GAP and RULING_DESC_ONLY_MIN_REPEATS."""
    x0, y0, x1, y1 = region.bbox
    min_span = RULING_MIN_SPAN_FRACTION * (x1 - x0)
    desc_band = next((b for b in region.bands if b.field == "description"), None)
    desc_min_span = (
        RULING_DESC_MIN_SPAN_FRACTION * (desc_band.x1 - desc_band.x0) if desc_band else None
    )

    marks: list[tuple[float, float, float]] = []  # (y, x_lo, x_hi)
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001
        return []
    for d in drawings:
        for item in d.get("items", []):
            if item[0] == "l":  # line segment
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) > 1.0:
                    continue  # not horizontal
                yy = p1.y
                x_lo, x_hi = (p1.x, p2.x) if p1.x <= p2.x else (p2.x, p1.x)
            elif item[0] == "re":  # filled rectangle used as a hairline rule
                if d.get("fill") is None:
                    continue
                rect = item[1]
                if rect.height > RULING_MAX_RECT_HEIGHT:
                    continue
                yy = (rect.y0 + rect.y1) / 2
                x_lo, x_hi = rect.x0, rect.x1
            else:
                continue
            if not (y0 - 2 <= yy <= y1 + 2):
                continue
            marks.append((yy, x_lo, x_hi))

    marks.sort(key=lambda m: m[0])
    y_clusters: list[list[tuple[float, float, float]]] = []
    for m in marks:
        if y_clusters and m[0] - y_clusters[-1][-1][0] <= RULING_MERGE_TOLERANCE:
            y_clusters[-1].append(m)
        else:
            y_clusters.append([m])

    strong: list[float] = []
    weak: list[float] = []
    for cluster in y_clusters:
        groups = _group_x_ranges([(lo, hi) for _, lo, hi in cluster])
        significant = [g for g in groups if g[1] - g[0] > RULING_MIN_SIBLING_WIDTH]
        if len(significant) > 1:
            continue  # separate underlines under separate values, not one rule
        best_lo, best_hi = max(groups, key=lambda g: g[1] - g[0])
        y = sum(m[0] for m in cluster) / len(cluster)
        if (best_hi - best_lo) >= min_span:
            strong.append(y)
        elif (
            desc_min_span is not None
            and min(best_hi, desc_band.x1) - max(best_lo, desc_band.x0) >= desc_min_span
        ):
            weak.append(y)

    ys = list(strong)
    if len(weak) >= RULING_DESC_ONLY_MIN_REPEATS:
        ys.extend(weak)
    return _merge_close_ys(sorted(set(round(y, 1) for y in ys)))


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
    region.layout = detect_layout(words, region.bands)
    rows = segment_rows(words, region.bands, ruling_ys=ruling_ys or None, layout=region.layout)

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
