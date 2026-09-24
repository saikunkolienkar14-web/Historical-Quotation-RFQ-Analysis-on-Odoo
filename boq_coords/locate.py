"""
Locate BOQ table regions within a multi-page quotation PDF.

Design note (plan §"Locating the BOQ region"): does NOT reuse v1's
detect_boq_header():1244 - that function operates on flattened lines and
its "join the next N lines" lookahead is a crude simulation of what
column-aware merging (columns.find_header) does properly. Only the header
keyword vocabulary is shared (via boq_coords.vocab), not v1's line logic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from boq_coords.columns import HeaderMatch, build_bands_from_table, find_header, resolve_duplicate_price_bands
from boq_coords.geometry import ColumnBand, Word, cluster_lines, join_words
from boq_coords.money import parse_price
from boq_coords.vocab import (
    STOP_SECTION_MARKERS,
    classify_header_word,
    is_ambiguous_total_price_header,
    normalize_label,
)

LETTERHEAD_TOP_MAX_Y = 100.0    # empirically: Adage letterhead sits above y=100
LETTERHEAD_BOTTOM_MIN_Y = 760.0  # footer sits below y=760
LETTERHEAD_REPEAT_FRACTION = 0.6

TOC_SECTION_RX = re.compile(r"^section\s*\d+\s*:", re.IGNORECASE)

COMMERCIAL_TERMS_LABELS = {
    "price basis", "gst", "packing charges", "freight charges",
    "insurances", "delivery", "offer validity", "hsn code",
}


def money_check(text: str) -> bool:
    return parse_price(text).value is not None


@dataclass
class TableRegion:
    page_no: int
    table: object            # the pymupdf Table object
    header: HeaderMatch
    bands: list[ColumnBand]
    bbox: tuple[float, float, float, float]
    score: float
    # banded.LAYOUT_TOP / LAYOUT_CENTRED - set by ruled.rows_from_region on
    # the headed page, inherited by unheaded continuation pages (rows.py).
    layout: str = "TOP"


def _is_toc_page(page_text: str) -> bool:
    """Table of contents pages repeat 'SECTION N: ...' headings with no
    money on them - a frequent false-positive source for find_tables()
    (plan §Locating the BOQ region, point "TOC trap")."""
    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    section_lines = sum(1 for ln in lines if TOC_SECTION_RX.match(ln))
    has_money = any(money_check(ln) for ln in lines)
    return section_lines >= 2 and not has_money


def _is_commercial_terms_table(rows: list[list[str | None]]) -> bool:
    """A 2-column Price Basis / GST / Freight / Validity table - not a BOQ,
    even though find_tables() reports it as a clean ruled table."""
    if not rows:
        return False
    n_cols = max(len(r) for r in rows)
    if n_cols > 2:
        return False
    left_cells = {
        normalize_label(r[0]) for r in rows if r and r[0]
    }
    return bool(left_cells & COMMERCIAL_TERMS_LABELS)


UNRULED_HEADER_GAP = 12.0   # x-gap (pt) that separates two header column labels,
                             # vs. normal intra-label word spacing (~3-6pt).
                             # Confirmed too high at 15: a real header had
                             # "Qty" -> "Description" at a 14.7pt gap, which
                             # wrongly merged two distinct columns into one.
UNRULED_HEADER_MAX_Y = 400.0  # only look for a header line in the top half of the
                               # page - a body line matching by accident further
                               # down is not the header


def _cluster_header_words(words: list[Word]) -> list[list[Word]]:
    """Group a single text line's words into column-label clusters by x-gap.
    Words within one label (e.g. "Unit" "Price") sit close together; distinct
    columns are visually separated by a much wider gap even with no ruling."""
    ordered = sorted(words, key=lambda w: w.x0)
    clusters: list[list[Word]] = [[ordered[0]]]
    for w in ordered[1:]:
        prev = clusters[-1][-1]
        if w.x0 - prev.x1 > UNRULED_HEADER_GAP:
            clusters.append([w])
        else:
            clusters[-1].append(w)
    return clusters


def _find_unruled_region(page, page_no: int) -> TableRegion | None:
    """Fallback for a borderless (no-ruling-line) priced table that
    `page.find_tables()` can't see at all (plan §Column bands, "unruled"
    path) - confirmed as a real gap on a real document: a 25-row BOQ table
    with visible column alignment but zero drawn rules scored zero rows
    because find_boq_regions only ever tried find_tables() results.

    Column bands come from the header LINE's own word clusters (not a
    multi-row merge like the ruled find_header() - unruled headers in this
    corpus are single physical lines), extended to the midpoint between
    adjacent clusters so body words that don't align exactly under the
    header text still land in the right band."""
    page_text = page.get_text("text")
    if not any(money_check(ln.strip()) for ln in page_text.splitlines() if ln.strip()):
        return None

    words_raw = page.get_text(
        "words", clip=(0, LETTERHEAD_TOP_MAX_Y, page.rect.width, LETTERHEAD_BOTTOM_MIN_Y)
    )
    words = [Word.from_pymupdf_tuple(w[:8]) for w in words_raw if w[4].strip()]
    if not words:
        return None

    for line in cluster_lines(words):
        if line.yc > UNRULED_HEADER_MAX_Y:
            break

        merged_text = join_words(line.words)
        if money_check(merged_text):
            continue  # swallowed a data row, not a header

        clusters = _cluster_header_words(line.words)
        if len(clusters) < 3:
            continue

        field_by_cluster: dict[int, str] = {}
        ambiguous_total_clusters: set[int] = set()
        for idx, cluster in enumerate(clusters):
            cluster_text = join_words(cluster)
            field = classify_header_word(cluster_text)
            if field:
                field_by_cluster[idx] = field
                if field == "total_price" and is_ambiguous_total_price_header(cluster_text):
                    ambiguous_total_clusters.add(idx)

        # Same bare-"Total"-needs-a-unit_price-sibling gate as the ruled
        # path (columns.find_header) - see vocab.is_ambiguous_total_price_header.
        if ambiguous_total_clusters and "unit_price" not in field_by_cluster.values():
            for idx in ambiguous_total_clusters:
                del field_by_cluster[idx]

        fields_found = set(field_by_cluster.values())
        if "description" not in fields_found:
            continue
        if not (fields_found & {"unit_price", "total_price"}):
            continue

        page_left = min(w.x0 for w in words)
        page_right = max(w.x1 for w in words)
        cluster_bounds = [
            (min(w.x0 for w in cl), max(w.x1 for w in cl)) for cl in clusters
        ]

        bands: list[ColumnBand] = []
        for idx, field in field_by_cluster.items():
            left = page_left if idx == 0 else (cluster_bounds[idx - 1][1] + cluster_bounds[idx][0]) / 2
            right = page_right if idx == len(clusters) - 1 else (cluster_bounds[idx][1] + cluster_bounds[idx + 1][0]) / 2
            bands.append(ColumnBand(field=field, x0=left, x1=right))

        bands = resolve_duplicate_price_bands(bands)
        price_bands = [b for b in bands if b.field in ("unit_price", "total_price")]
        if not price_bands:
            continue

        header = HeaderMatch(start=0, span=1, field_by_col=field_by_cluster, coverage=len(fields_found))
        body_money_rows = sum(1 for ln in page_text.splitlines() if money_check(ln.strip()))
        score = header.coverage * 10 + body_money_rows
        bbox = (page_left, line.y1, page_right, LETTERHEAD_BOTTOM_MIN_Y)
        return TableRegion(page_no=page_no, table=None, header=header, bands=bands, bbox=bbox, score=score)

    return None


def find_boq_regions(doc, keep_words: bool = False) -> list[TableRegion]:
    """One entry per qualifying BOQ table found anywhere in the document.
    A document can legitimately contain more than one (multiple BOQ tables
    across sections) - all qualifying regions are kept, not just the best."""
    regions: list[TableRegion] = []

    for page_no in range(doc.page_count):
        page = doc[page_no]
        page_text = page.get_text("text")

        if _is_toc_page(page_text):
            continue

        regions_before = len(regions)

        try:
            tabs = page.find_tables()
        except Exception:  # noqa: BLE001
            tabs = None

        if tabs is not None:
            for table in tabs.tables:
                x0, y0, x1, y1 = table.bbox
                page_width = page.rect.width

                # Guard: letterhead/footer false positives - a "table" that is
                # entirely inside the header or footer band.
                if y1 <= LETTERHEAD_TOP_MAX_Y or y0 >= LETTERHEAD_BOTTOM_MIN_Y:
                    continue

                # Guard: table too narrow to plausibly be the priced BOQ grid.
                if (x1 - x0) < 0.5 * page_width:
                    continue

                try:
                    rows = table.extract()
                except Exception:  # noqa: BLE001
                    continue

                if _is_commercial_terms_table(rows):
                    continue

                header = find_header(rows, money_check)
                if header is None:
                    continue

                bands = build_bands_from_table(table, header)
                price_bands = [b for b in bands if b.field in ("unit_price", "total_price")]
                if not price_bands:
                    continue

                body_money_rows = sum(
                    1 for r in rows if any(c and money_check(c) for c in r if c)
                )

                score = header.coverage * 10 + body_money_rows
                regions.append(TableRegion(
                    page_no=page_no, table=table, header=header, bands=bands,
                    bbox=table.bbox, score=score,
                ))

        # Only try the unruled fallback if find_tables() found nothing
        # usable on this page - a ruled region always wins when present.
        if len(regions) == regions_before:
            unruled = _find_unruled_region(page, page_no)
            if unruled is not None:
                regions.append(unruled)

    return regions
