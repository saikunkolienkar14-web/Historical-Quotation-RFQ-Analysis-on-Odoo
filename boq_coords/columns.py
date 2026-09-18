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

from boq_coords.geometry import ColumnBand, Word, cluster_lines, join_words, median
from boq_coords.money import parse_price, parse_quantity
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


# Minimum width (pt) an inferred column must have to be trusted - a split
# that would produce a narrower column means the gap-based clustering
# below picked a spurious boundary (e.g. two closely-kerned words in the
# same real column), not a genuine column edge.
MIN_INFERRED_BAND_WIDTH = 5.0

# A gap between consecutive word x0-positions is treated as a real column
# boundary only when it's BOTH an absolute outlier and a clear outlier
# relative to this page's own typical intra-column word spacing - a fixed
# threshold alone doesn't adapt across documents with different fonts/
# layouts, and a purely relative one misfires on a page with almost no
# text. Confirmed against real continuation pages during development.
MIN_COLUMN_GAP = 15.0
COLUMN_GAP_SIGNIFICANCE = 3.0

# Fraction of a cluster's own lines that must parse as money/quantity for
# that cluster to be classified as a price/quantity column. Confirmed
# necessary against a real document (Q25N10067R1_Bechtel_RIL NMD): a
# cluster whose ONLY content is prose that happens to sit in a genuine
# gap must not be mistaken for a price column just because a gap exists.
CONTENT_CLASSIFICATION_THRESHOLD = 0.6


# A bare 1-2 digit number with no comma grouping/decimal/currency parses
# as valid "money" under money.parse_price's deliberately permissive
# grammar (this corpus often has no currency symbol at all) - but it is
# far more likely an item number or quantity in that shape. Item_no's own
# column would otherwise satisfy the money check just as well as a real
# price column and get misclassified. price_in_bounds' own MIN_*_PRICE
# floors (1000) already encode "this corpus's prices are never this
# small"; this reuses that same domain knowledge at a looser threshold
# (only to tell columns apart, not to validate a final price).
MIN_PRICE_LIKE_VALUE = 100.0


def _looks_like_price(text: str) -> bool:
    parsed = parse_price(text)
    if parsed.is_placeholder:
        return True
    return parsed.value is not None and parsed.value >= MIN_PRICE_LIKE_VALUE


def bands_capture_price(words: list[Word], bands: list[ColumnBand]) -> bool:
    """True when `bands`' price field(s), applied to `words`, mostly
    contain money (or a recognized placeholder) - i.e. these bands
    already fit this page's own words.

    Used by rows._unheaded_continuation_rows as a gate: only attempt
    infer_bands_from_words (a less certain fallback) when the parent
    table's REUSED bands demonstrably don't fit this continuation page -
    never replace bands that are already working. Confirmed as a real
    regression risk, not a hypothetical one: Q2501N005's continuation
    pages (no item_no column at all, so row splitting for the bundled
    PORTA CABIN/DATALOGGER/DUST MONITOR sub-items relies entirely on
    correctly recognizing each one's own price line) have no layout
    shift, and unconditionally reinferring there produced a worse split
    than the perfectly good reused bands, re-breaking a previously-fixed
    row-merging bug (tests.test_boq_coords_integration.TestBundledSubItems).

    True (nothing to check) when `bands` has no price field at all.
    """
    price_fields = [f for f in ("unit_price", "total_price") if f in {b.field for b in bands}]
    if not price_fields:
        return True

    lines = cluster_lines(words)
    if not lines:
        return False

    checked = 0
    plausible = 0
    for line in lines:
        cell_words: dict[str, list[Word]] = {}
        for w in line.words:
            for band in bands:
                if band.contains(w):
                    cell_words.setdefault(band.field, []).append(w)
                    break
        for field in price_fields:
            if field not in cell_words:
                continue
            checked += 1
            if _looks_like_price(join_words(cell_words[field])):
                plausible += 1

    if checked == 0:
        return False
    return (plausible / checked) >= CONTENT_CLASSIFICATION_THRESHOLD


def _column_gaps(words: list[Word]) -> list[float]:
    """x-positions where a real column boundary most likely sits."""
    xs = sorted(w.x0 for w in words)
    if len(xs) < 2:
        return []
    gaps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
    baseline = median(gaps) or 1.0
    threshold = max(MIN_COLUMN_GAP, COLUMN_GAP_SIGNIFICANCE * baseline)
    return [xs[i + 1] for i in range(len(gaps)) if gaps[i] >= threshold]


def infer_bands_from_words(words: list[Word], field_order: list[str]) -> list[ColumnBand] | None:
    """Header-free column-band inference for an unheaded continuation page
    (rows._unheaded_continuation_rows). There is no header row on such a
    page to rebuild bands from the way build_bands_from_table() does.

    Deliberately does NOT assume the continuation page has the same
    NUMBER of visual columns as the parent table, only roughly the same
    left-to-right ORDER - the documented bug shape
    (docs/COORDS_EXTRACTOR.md#known-limitations) is specifically a
    "detailed spec" page that CRAMS several of the parent table's columns
    into fewer, differently-positioned ones (a combined qty+unit cell, a
    single placeholder covering both price columns), so forcing exactly
    len(field_order) bands guarantees a wrong split on the real case this
    exists to fix.

    Instead: cluster words by x-gap significance (_column_gaps) into
    however many groups the page's own geometry actually supports, then
    classify each cluster by its own CONTENT - a cluster whose lines
    mostly parse as money becomes a price column (parse_quantity's own
    regex already recognizes an embedded unit, e.g. "1 No.", so a single
    cluster is enough to recover a merged qty+unit cell without a
    separate unit band), the leftmost remaining cluster becomes item_no
    when one is wanted, and whatever is left becomes description.

    Returns None - never a confident-but-wrong guess - whenever there
    aren't at least 2 real column clusters, no cluster classifies as a
    price column (a price field was expected but nothing recovered one),
    or nothing is left for description. The caller falls back to reusing
    the parent table's own bands (this function's pre-fix behavior)
    whenever this returns None.
    """
    if not words:
        return None

    price_fields = [f for f in field_order if f in ("unit_price", "total_price")]
    wants_quantity = "quantity" in field_order
    wants_item_no = "item_no" in field_order

    boundaries = _column_gaps(words)
    left = min(w.x0 for w in words)
    right = max(w.x1 for w in words) + 1.0
    edges = [left, *boundaries, right]
    if len(edges) < 3:  # fewer than 2 real columns found
        return None

    clusters: list[tuple[float, float]] = []
    for lo, hi in zip(edges, edges[1:]):
        if hi - lo < MIN_INFERRED_BAND_WIDTH:
            return None
        clusters.append((lo, hi))

    lines = cluster_lines(words)
    if not lines:
        return None

    def texts_in(lo: float, hi: float) -> list[str]:
        return [
            join_words(in_cluster)
            for line in lines
            if (in_cluster := [w for w in line.words if lo <= w.xc < hi])
        ]

    def classifies_as(texts: list[str], check) -> bool:
        if not texts:
            return False
        hits = sum(1 for t in texts if check(t))
        return (hits / len(texts)) >= CONTENT_CLASSIFICATION_THRESHOLD

    cluster_texts = [texts_in(lo, hi) for lo, hi in clusters]

    money_idx = [
        i for i, texts in enumerate(cluster_texts)
        if classifies_as(texts, _looks_like_price)
    ]
    if price_fields and not money_idx:
        return None

    assigned: dict[int, str] = {}
    if len(money_idx) == 1 and price_fields:
        assigned[money_idx[0]] = "total_price" if "total_price" in price_fields else price_fields[0]
    elif len(money_idx) >= 2 and price_fields:
        # This corpus's near-universal convention is unit price left of
        # total price (see resolve_duplicate_price_bands above).
        if "unit_price" in price_fields:
            assigned[money_idx[0]] = "unit_price"
        if "total_price" in price_fields:
            assigned[money_idx[-1]] = "total_price"

    if wants_quantity:
        qty_idx = [
            i for i, texts in enumerate(cluster_texts)
            if i not in assigned and classifies_as(texts, lambda t: parse_quantity(t).value is not None)
        ]
        if qty_idx:
            assigned[qty_idx[0]] = "quantity"

    remaining = [i for i in range(len(clusters)) if i not in assigned]
    if wants_item_no and remaining and remaining[0] == 0:
        assigned[0] = "item_no"
        remaining = remaining[1:]

    if not remaining or "description" not in field_order:
        return None

    bands = [ColumnBand(field=field, x0=clusters[i][0], x1=clusters[i][1]) for i, field in assigned.items()]
    bands.append(ColumnBand(
        field="description",
        x0=clusters[remaining[0]][0],
        x1=clusters[remaining[-1]][1],
    ))

    return bands


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
