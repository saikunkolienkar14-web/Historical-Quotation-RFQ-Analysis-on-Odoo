"""
Row segmentation - the core algorithm shared by the ruled and unruled
paths (plan Step 3, "Row segmentation"). `find_tables()` gives reliable
column geometry but NOT reliable row segmentation: the frequent failure is
a ruled table whose header is correct but whose entire body collapsed into
one giant cell because rules exist only around the header. So both paths
funnel into segment_rows() here, differing only in where their ColumnBand
list came from (table header cells vs. inferred bands).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field

from boq_coords.geometry import Line, Word, cluster_lines, join_words, median
from boq_coords.money import parse_price, parse_quantity, resolve_quantity_and_unit
from boq_coords.vocab import MAX_ITEM_NUMBER, STOP_SECTION_MARKERS, normalize_label

ITEM_NUMBER_RX = re.compile(r"^\(?\d{1,3}(?:\.\d+)*[.)]?$")

MAX_MERGE_LINES = 200
MAX_MERGE_CHARS = 8000

PRICE_FIELDS = ("unit_price", "total_price")


@dataclass
class LogicalRow:
    # Per-line, per-field word groups, in row (top-to-bottom) order. A flat
    # field -> words bag would lose line order for multi-line cells (e.g.
    # a wrapped description) once words from different lines are sorted
    # together by x0 alone - that scrambled reading order in testing, so
    # line structure is preserved explicitly instead.
    line_cells: list[tuple[Line, dict[str, list[Word]]]] = dc_field(default_factory=list)

    # Geometry-derived warnings attached by the caller (ruled.py) after
    # segmentation, e.g. "PRICE_CELL_SPANS_MULTIPLE_ROWS" - purely additive,
    # never read by segment_rows itself, never changes any cell's content.
    flags: list[str] = dc_field(default_factory=list)

    @property
    def cells(self) -> dict[str, list[Word]]:
        out: dict[str, list[Word]] = {}
        for _, cw in self.line_cells:
            for f, ws in cw.items():
                out.setdefault(f, []).extend(ws)
        return out

    @property
    def lines(self) -> list[Line]:
        return [ln for ln, _ in self.line_cells]

    def text(self, field: str) -> str:
        parts = []
        for _, cw in self.line_cells:
            if field in cw and cw[field]:
                parts.append(join_words(cw[field]))
        if not parts:
            return ""
        # item_no is a single anchor per row, by definition - joining every
        # line's item_no-band content (as the price/quantity fields below
        # deliberately do for their own rare multi-line case) concatenates
        # unrelated tokens that merely happen to sit in the item_no x-range
        # on a later physical line of the same merged row - confirmed as a
        # real bug: unsplit sub-item markers (".1", ".2", ".a"...) or a
        # stray footnote number produced values like "2 .1 .2 .3". Only the
        # row's own first line can be the real item number.
        if field == "item_no":
            return parts[0]
        # Other single-value fields (price/qty) are normally one token on
        # one line - join with a space in the rare multi-line case so the
        # money/quantity grammar still sees one string. Description is
        # expected to be multi-line - keep line breaks.
        sep = "\n" if field in ("description", "other") else " "
        return sep.join(parts)

    def raw_row_text(self) -> str:
        return "\n".join(
            join_words(sorted(ln.words, key=lambda w: w.x0)) for ln in self.lines
        )


def _band_for_word(w: Word, bands) -> str:
    best = None
    best_overlap = 0.0
    for b in bands:
        if b.contains(w):
            return b.field
        ov = b.overlap_fraction(w.x0, w.x1)
        if ov > best_overlap:
            best_overlap = ov
            best = b.field
    return best if best and best_overlap > 0.4 else "other"


def _is_stop_line(text: str) -> bool:
    norm = normalize_label(text)
    return any(marker in norm for marker in STOP_SECTION_MARKERS)


def _classify_lines(lines: list[Line], bands) -> list[dict]:
    """For each text line, bucket its words by band, then apply the
    structural gate: a word only stays in a price band if that band's
    full line-content parses as money or a recognized placeholder;
    otherwise it reverts to description. This is what stops
    "Power Supply: 230 VAC, 50 Hz" from ever reaching a price field,
    independent of what the number parser alone would do with it
    (plan §"The structural fix for the known bug class")."""
    out = []
    for line in lines:
        cell_words: dict[str, list[Word]] = {}
        for w in sorted(line.words, key=lambda w: w.x0):
            f = _band_for_word(w, bands)
            cell_words.setdefault(f, []).append(w)

        for pf in PRICE_FIELDS:
            if pf not in cell_words:
                continue
            text = join_words(cell_words[pf])
            parsed = parse_price(text)
            if parsed.value is None and not parsed.is_placeholder:
                cell_words.setdefault("description", []).extend(cell_words.pop(pf))

        out.append({"line": line, "cells": cell_words})
    return out


def _valid_item_no(text: str) -> bool:
    text = text.strip()
    if not ITEM_NUMBER_RX.match(text):
        return False
    m = re.match(r"\d+", text)
    return bool(m) and int(m.group(0)) <= MAX_ITEM_NUMBER


def is_clean_item_no(text: str) -> bool:
    """Public wrapper for _valid_item_no - a single well-formed item-number
    token ('1', '2.3'), never letters, whitespace, or multiple concatenated
    anchors. Used by __main__.py as an output-side guard: an item_no that
    fails this check is replaced with the row's own sequential index rather
    than emitted verbatim (plan §Issue 3/5 - catches both unsplit-sub-item
    concatenation and one-off garbage like a literal '1:00 AM')."""
    return _valid_item_no(text)


LETTERHEAD_BOILERPLATE_PHRASES = [
    "corporate hq",
    "registered office",
    "satra plaza",
    # The postal-address half of the same letterhead/footer block, which
    # wraps across its own lines and so escaped the phrases above - these
    # were bleeding onto the END of real item descriptions (41 rows had
    # "...Navi Mumbai 400703 Maharashtra INDIA" inside product_name).
    # Deliberately specific to this company's own address: a bare token
    # like "limited" would match legitimate item text ("limited to...").
    "palm beach road",
    "navi mumbai 400703",
    "maharashtra india",
    "automation private limited",
]


def _is_letterhead_boilerplate(text: str) -> bool:
    """The company's own repeated letterhead/footer block ("CORPORATE HQ &
    REGISTERED OFFICE", "Satra Plaza, Office No..."), confirmed bleeding
    into the last row's description on pages where the footer sits above
    LETTERHEAD_BOTTOM_MIN_Y (locate.py) and so survives the word clip.
    Filtered at the line level, before boundary-bucketing, so it never gets
    swept into whichever row's boundary happens to extend to the bottom of
    the clipped region."""
    norm = normalize_label(text)
    return any(p in norm for p in LETTERHEAD_BOILERPLATE_PHRASES)


def _has_price(cells: dict[str, list[Word]]) -> bool:
    for pf in PRICE_FIELDS:
        if pf in cells:
            p = parse_price(join_words(cells[pf]))
            if p.value is not None or p.is_placeholder:
                return True
    return False


def _is_self_contained_item_line(cells: dict[str, list[Word]]) -> bool:
    """True if a single physical line, on its own, already carries a
    complete priced line item - its own valid price AND its own
    unit-qualified quantity. Used to catch bundled, unnumbered sub-items
    (e.g. "PORTA CABIN ... 1 SET 25,64,400 25,64,400" sitting under a
    parent item with no item-number anchor of its own) that would
    otherwise be swept into the parent's row and have their independent
    prices concatenated into one unparseable string (confirmed real-corpus
    bug: Q2501N005, item 8's bundled sub-items). Requiring BOTH a valid
    price and a valid quantity+unit reading (not price alone) keeps this
    conservative - a stray number landing alone in a price band essentially
    never also carries a recognized-unit quantity on the same line."""
    if not _has_price(cells):
        return False
    qty_text = join_words(cells.get("quantity", []))
    unit_text = join_words(cells.get("unit", []))
    pq = resolve_quantity_and_unit(qty_text, unit_text)
    return pq.value is not None


def _split_self_contained_subitems(row: LogicalRow) -> list[LogicalRow]:
    """Split a provisional row into several when it contains TWO OR MORE
    independently self-contained item lines - each such line is its own
    real, distinct, independently-priced item that boundary detection
    merged together only because it lacked its own item-number anchor. An
    ordinary row (even with a long wrapped multi-line description) has at
    most one such line and is returned unchanged.

    Splits fall AFTER each self-contained line: that line is the closing
    data line of its own sub-item (its heading/description precedes it
    within the block), so everything up to and including it belongs to the
    current sub-item, and the next physical line starts the next one. Any
    trailing description-only tail after the last self-contained line (no
    price, no item_no) is left for _merge_bundled_lots to fold into the
    preceding split-out row, exactly as it already does for genuine
    bundled-lot continuations."""
    self_contained_idx = [
        i for i, (_, cells) in enumerate(row.line_cells)
        if _is_self_contained_item_line(cells)
    ]
    if len(self_contained_idx) < 2:
        return [row]

    out: list[LogicalRow] = []
    start = 0
    for i in self_contained_idx:
        out.append(LogicalRow(line_cells=row.line_cells[start:i + 1]))
        start = i + 1
    if start < len(row.line_cells):
        out.append(LogicalRow(line_cells=row.line_cells[start:]))
    return out


def _derive_boundaries_from_desc_gaps(classified: list[dict], anchor_yc: list[float]) -> list[float]:
    """Anchor row starts on gaps in the DESCRIPTION column, not on the
    item-number y-position directly - prices are frequently vertically
    centred within a tall multi-line description cell, so anchoring purely
    on item-number y drags the price onto the wrong logical row (plan
    §Row segmentation, point 1e)."""
    desc_lines = [c for c in classified if "description" in c["cells"]]
    if len(desc_lines) < 2:
        return sorted(anchor_yc)

    ycs = [c["line"].yc for c in desc_lines]
    gaps = [ycs[i] - ycs[i - 1] for i in range(1, len(ycs))]
    g = median(gaps) or 1.0
    candidate_starts = [ycs[0]] + [ycs[i] for i in range(1, len(ycs)) if gaps[i - 1] > 1.8 * g]

    boundaries = []
    for a in sorted(anchor_yc):
        below = [c for c in candidate_starts if c <= a]
        boundaries.append(below[-1] if below else (candidate_starts[0] if candidate_starts else a))
    return sorted(set(boundaries))


def segment_rows(words: list[Word], bands, ruling_ys: list[float] | None = None) -> list[LogicalRow]:
    if not words:
        return []

    lines = cluster_lines(words)
    lines = [ln for ln in lines if not _is_letterhead_boilerplate(join_words(ln.words))]
    if not lines:
        return []
    classified = _classify_lines(lines, bands)

    item_anchors = [
        c["line"].yc for c in classified
        if "item_no" in c["cells"] and _valid_item_no(join_words(c["cells"]["item_no"]))
    ]
    money_lines_yc = [c["line"].yc for c in classified if _has_price(c["cells"])]

    if ruling_ys and len(ruling_ys) >= 2:
        boundaries = sorted(set(ruling_ys))
    elif len(item_anchors) >= 2:
        boundaries = _derive_boundaries_from_desc_gaps(classified, item_anchors)
        if len(boundaries) < len(set(item_anchors)):
            # Gap-derivation collapsed two distinct item-number anchors
            # into the same boundary - happens when adjacent multi-line
            # description cells are similarly dense with no larger gap
            # between them for the gap heuristic to key off (confirmed on
            # a real 2-item document with tall, gapless description
            # blocks: both item numbers landed in one merged row).
            #
            # Item numbers are also frequently vertically centred rather
            # than top-aligned within their cell (confirmed on the same
            # document: "1" sits on the same line as "DAYS", the SECOND
            # line of a two-line heading, not the first) - so falling
            # back to raw anchor y as the boundary cuts each row's own
            # leading description line(s) off into the wrong row, and
            # drops the very first row's leading line(s) entirely (they
            # fall before boundary[0]). Use the anchors' MIDPOINTS as
            # interior boundaries instead - not exact, but keeps each
            # row's content roughly centred on its own anchor rather than
            # systematically biased earlier - and start the first
            # boundary at the region's actual top line, not the first
            # anchor, so nothing above item 1's own number is lost.
            sorted_anchors = sorted(set(item_anchors))
            region_top = min(c["line"].yc for c in classified)
            boundaries = [region_top] + [
                (sorted_anchors[i] + sorted_anchors[i + 1]) / 2
                for i in range(len(sorted_anchors) - 1)
            ]
    else:
        boundaries = sorted(set(money_lines_yc))

    if not boundaries:
        # Nothing to anchor on - treat the whole region as a single row.
        boundaries = [classified[0]["line"].yc]

    rows: list[LogicalRow] = []
    for i, b0 in enumerate(boundaries):
        b1 = boundaries[i + 1] if i + 1 < len(boundaries) else float("inf")
        row_lines = [c for c in classified if b0 - 0.01 <= c["line"].yc < b1]
        if not row_lines:
            continue
        row = LogicalRow(line_cells=[(c["line"], c["cells"]) for c in row_lines])
        rows.append(row)

    # Split out bundled, unnumbered sub-items (see
    # _split_self_contained_subitems) before the merge pass below - each
    # split-out row already has its own price, so _merge_bundled_lots'
    # "no item_no and no price -> merge into preceding row" rule correctly
    # leaves them as their own rows.
    split_rows: list[LogicalRow] = []
    for row in rows:
        split_rows.extend(_split_self_contained_subitems(row))

    return _merge_bundled_lots(split_rows)


def _merge_bundled_lots(rows: list[LogicalRow]) -> list[LogicalRow]:
    """A row with a description but no money and no item number merges into
    the PRECEDING row - this is what makes a bundled lot become one row
    (plan decision: bundled lot = one row). Capped and stopped at any
    STOP_SECTION_MARKERS line so a merge can't run away into an unrelated
    document section (e.g. Terms & Conditions)."""
    if not rows:
        return rows

    merged: list[LogicalRow] = [rows[0]]
    for row in rows[1:]:
        has_item_no = "item_no" in row.cells and _valid_item_no(join_words(row.cells["item_no"]))
        has_price = _has_price(row.cells)

        row_text = row.raw_row_text()
        if _is_stop_line(row_text):
            merged.append(row)
            continue

        if not has_item_no and not has_price and merged:
            prev = merged[-1]
            total_lines = len(prev.lines) + len(row.lines)
            total_chars = len(prev.raw_row_text()) + len(row_text)
            if total_lines <= MAX_MERGE_LINES and total_chars <= MAX_MERGE_CHARS:
                prev.line_cells.extend(row.line_cells)
                continue

        merged.append(row)

    return merged
