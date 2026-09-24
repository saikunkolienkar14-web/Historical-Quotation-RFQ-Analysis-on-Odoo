"""
Multi-page stitching and document-level row assembly (plan §Multi-page
stitching). find_boq_regions() only reports pages where find_tables() AND
find_header() both succeed - a continuation page that has no ruling/no
repeated header (just more priced lines flowing from the previous page)
won't produce its own TableRegion. This module bridges that gap: after
gathering headered regions, it checks the page immediately following each
region for unheaded continuation content, preferring bands freshly
inferred from that page's own words (columns.infer_bands_from_words) over
the parent table's bands - most continuation pages do keep the same x-edges
(every sampled page in this corpus is 595.32 x 841.92 with identical
letterhead geometry), but a real subset shift layout mid-document, and
reusing stale bands there misreads a combined qty+unit cell's leading
digit as a price (see _unheaded_continuation_rows and
docs/COORDS_EXTRACTOR.md#known-limitations).
"""
from __future__ import annotations

from dataclasses import dataclass

from boq_coords.banded import LogicalRow, _merge_bundled_lots, peel_leading_continuation_lines, segment_rows
from boq_coords.columns import bands_capture_price, infer_bands_from_words
from boq_coords.geometry import Word
from boq_coords.locate import (
    LETTERHEAD_BOTTOM_MIN_Y,
    LETTERHEAD_TOP_MAX_Y,
    TableRegion,
    find_boq_regions,
    money_check,
)
from boq_coords.ruled import _ruling_line_ys, rows_from_region
from boq_coords.vocab import STOP_SECTION_MARKERS, classify_header_word, normalize_label

MAX_STITCH_PAGE_GAP = 1
BAND_EDGE_TOLERANCE = 6.0


def _bands_agree(a, b) -> bool:
    if len(a) != len(b):
        return False
    a_by_field = {band.field: band for band in a}
    b_by_field = {band.field: band for band in b}
    if set(a_by_field) != set(b_by_field):
        return False
    for field, band_a in a_by_field.items():
        band_b = b_by_field[field]
        if abs(band_a.x0 - band_b.x0) > BAND_EDGE_TOLERANCE:
            return False
        if abs(band_a.x1 - band_b.x1) > BAND_EDGE_TOLERANCE:
            return False
    return True


def _page_has_money(page) -> bool:
    text = page.get_text("text")
    return any(money_check(ln.strip()) for ln in text.splitlines() if ln.strip())


def _page_starts_new_section(page) -> bool:
    """A page whose text hits a STOP_SECTION_MARKERS heading (e.g. 'PART
    B: COMMERCIAL TERMS AND CONDITIONS' contains 'terms and conditions')
    is a different section, not a continuation of the priced table -
    confirmed as a real bug: an unguarded continuation pass pulled
    commercial-terms/footer text into the BOQ table as a garbage row on a
    document whose PART A table was only 2 rows long."""
    text = page.get_text("text")
    norm = normalize_label(text)
    return any(marker in norm for marker in STOP_SECTION_MARKERS)


HEADER_SCAN_ROWS = 4
HEADER_MIN_FIELDS = 3


def _page_has_own_table_header(page) -> bool:
    """True if this page opens its OWN table with a recognizable header row.

    Such a page is a NEW table, never an unheaded continuation of the
    previous one - confirmed as a real bug on the AMADAS document: page 7
    is a separate "MAKE LIST" table (SR NO | ITEM DESCRIPTION | MAKE |
    Total | Unit). find_boq_regions correctly declines to treat it as a
    BOQ region (its bare "Total" is a quantity total, not a price, so it
    has no price column), but the continuation pass then swallowed the
    whole page into the PREVIOUS page's table using that page's band
    geometry - clipping the left edge off every description ("pply of
    SITRANS...") and collapsing 20 source rows into one row whose
    product_name was the table header plus several merged line items.
    """
    try:
        tables = page.find_tables().tables
    except Exception:  # noqa: BLE001 - pymupdf raises broadly on odd pages
        return False

    for table in tables:
        try:
            extracted = table.extract()
        except Exception:  # noqa: BLE001
            continue
        for row in extracted[:HEADER_SCAN_ROWS]:
            fields = {
                classify_header_word(str(cell))
                for cell in row
                if cell and str(cell).strip()
            }
            fields.discard(None)
            if "description" in fields and len(fields) >= HEADER_MIN_FIELDS:
                return True
    return False


def _unheaded_continuation_rows(doc, page_no: int, region: TableRegion) -> list[LogicalRow] | None:
    """Try extracting rows from `page_no` with no header on this page -
    used when the immediately-following page has no qualifying TableRegion
    of its own but visibly continues the priced table (plan §Multi-page
    stitching, point 3: "no header -> dropped, not emitted" refers to a
    REPEATED header; an ABSENT header is the continuation signal itself).

    Column bands: there's no header row here to rebuild bands from (no
    columns.build_bands_from_table() call is possible), so this reuses
    `region.bands` (this function's entire pre-fix behavior) UNLESS
    columns.bands_capture_price() shows those bands demonstrably don't
    fit this page's own words - only then does it fall back to
    columns.infer_bands_from_words() to recover real bands. Order
    matters here: trying inference first and using it whenever it looks
    internally plausible was confirmed to regress a previously-working
    document (Q2501N005 - see bands_capture_price's docstring) by
    replacing perfectly good reused bands with a worse inferred split.
    Reused bands failing is the actual bug signal
    (docs/COORDS_EXTRACTOR.md#known-limitations: a combined qty+unit cell
    sliding under a stale reused price band, its leading digit misread as
    a price) - inference is a fallback for that failure, not a default."""
    if page_no >= doc.page_count:
        return None
    page = doc[page_no]
    if _page_starts_new_section(page):
        return None
    if _page_has_own_table_header(page):
        return None
    if not _page_has_money(page):
        return None

    x0 = min(b.x0 for b in region.bands)
    x1 = max(b.x1 for b in region.bands)
    y_top = LETTERHEAD_TOP_MAX_Y
    y_bottom = LETTERHEAD_BOTTOM_MIN_Y

    words_raw = page.get_text("words", clip=(x0, y_top, x1, y_bottom))
    words = [Word.from_pymupdf_tuple(w[:8]) for w in words_raw if w[4].strip()]
    if not words:
        return None

    inferred_bands = None
    if not bands_capture_price(words, region.bands):
        field_order = [b.field for b in sorted(region.bands, key=lambda b: b.x0)]
        inferred_bands = infer_bands_from_words(words, field_order)
    bands = inferred_bands if inferred_bands is not None else region.bands

    ruling_ys = _ruling_line_ys(page, TableRegion(
        page_no=page_no, table=None, header=region.header, bands=bands,
        bbox=(x0, y_top, x1, y_bottom), score=0,
    ))
    # No header on this page, so the page may open mid-item and can't tell
    # its own layout - inherit the headed page's (see banded.detect_layout).
    rows = segment_rows(words, bands, ruling_ys=ruling_ys or None, layout=region.layout)
    if rows and inferred_bands is not None:
        for row in rows:
            row.flags.append("CONTINUATION_BANDS_REINFERRED")
    return rows or None


@dataclass
class DocumentTable:
    rows: list[LogicalRow]
    page_nos: list[int]
    header_field_by_col: dict


def extract_document_tables(doc) -> list[DocumentTable]:
    """Top-level entry point: find all BOQ regions in the document, extract
    rows for each, and stitch page-adjacent regions/continuations that
    share compatible column geometry into single logical tables."""
    regions = find_boq_regions(doc)
    if not regions:
        return []

    regions.sort(key=lambda r: r.page_no)
    region_rows: dict[int, list[LogicalRow]] = {
        id(r): rows_from_region(doc[r.page_no], r) for r in regions
    }

    tables: list[DocumentTable] = []
    used_page_regions: set[int] = set()

    for i, region in enumerate(regions):
        if id(region) in used_page_regions:
            continue
        used_page_regions.add(id(region))

        combined_rows = list(region_rows[id(region)])
        combined_pages = [region.page_no]
        current = region

        # 1. Stitch onto an immediately-following HEADERED region with
        #    compatible bands (repeated header on the next page).
        stitched_into_headered = True
        while stitched_into_headered:
            stitched_into_headered = False
            for other in regions:
                if id(other) in used_page_regions:
                    continue
                if other.page_no - current.page_no > MAX_STITCH_PAGE_GAP:
                    continue
                if other.page_no <= current.page_no:
                    continue
                if not _bands_agree(current.bands, other.bands):
                    continue
                next_rows = region_rows[id(other)]
                if combined_rows:
                    next_rows = peel_leading_continuation_lines(combined_rows[-1], next_rows)
                combined_rows.extend(next_rows)
                combined_pages.append(other.page_no)
                used_page_regions.add(id(other))
                current = other
                stitched_into_headered = True
                break

        # 2. Stitch onto UNHEADED continuation pages immediately after the
        #    last page absorbed so far.
        next_page = current.page_no + 1
        while next_page < doc.page_count:
            already_has_region = any(
                r.page_no == next_page and id(r) not in used_page_regions for r in regions
            )
            if already_has_region:
                break  # let the outer loop's own region handling take it
            extra_rows = _unheaded_continuation_rows(doc, next_page, current)
            if not extra_rows:
                break
            if combined_rows:
                extra_rows = peel_leading_continuation_lines(combined_rows[-1], extra_rows)
            combined_rows.extend(extra_rows)
            combined_pages.append(next_page)
            next_page += 1

        # Leading bullet-marked continuation lines were already peeled off
        # at each stitch point above (peel_leading_continuation_lines),
        # which needs the per-page row boundaries still visible to work.
        # This second, coarser pass catches what that one can't: a row
        # that is ENTIRELY an orphaned continuation (no item_no, no price,
        # not just a leading bullet) - the same _merge_bundled_lots rule
        # already applied per-page inside segment_rows, re-run here once
        # more across the now-fully-stitched, flat row list so it also
        # catches a continuation that crossed a page boundary.
        combined_rows = _merge_bundled_lots(combined_rows)

        tables.append(DocumentTable(
            rows=combined_rows, page_nos=combined_pages,
            header_field_by_col=region.header.field_by_col,
        ))

    return tables
