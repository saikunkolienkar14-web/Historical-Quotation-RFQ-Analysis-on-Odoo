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
    return segment_rows(words, region.bands, ruling_ys=ruling_ys or None)
