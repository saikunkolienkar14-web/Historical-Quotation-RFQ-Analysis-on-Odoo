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

from boq_coords.fields import BULLET_CHARS
from boq_coords.geometry import Line, Word, cluster_lines, join_words, median
from boq_coords.money import parse_price, parse_quantity, resolve_quantity_and_unit
from boq_coords.vocab import (
    MAX_ITEM_NUMBER,
    NON_TERMINAL_ABBREVIATIONS,
    STOP_SECTION_MARKERS,
    normalize_label,
)

ITEM_NUMBER_RX = re.compile(r"^\(?\d{1,3}(?:\.\d+)*[A-Za-z]?[.)]?$")

MAX_MERGE_LINES = 200
MAX_MERGE_CHARS = 8000

PRICE_FIELDS = ("unit_price", "total_price")

# Set on a row whose OWN boundaries came directly from real per-row ruling
# lines the source PDF draws (not just its outer top/bottom frame - see
# _border_only), rather than being inferred from item-number gaps or price
# lines. Such a boundary is ground truth: the document itself drew a line
# between this row and its neighbour, which the TOP-path repair heuristics
# below (_reattach_misattributed_leading_lines) must never second-guess -
# confirmed real-corpus case: Q25X10031R1 prints "FAT & Inspection at
# Adage works Goa." (item 4's own heading) directly below a ruling line,
# but its item number sits vertically centred on a LATER line of the same
# cell; reattach's "a complete sentence right before an anchor's own line
# is unclaimed trailing content of the previous item" heuristic read the
# heading as trailing content of item 3 ("Documentation") and moved it
# there, even though the ruling the PDF drew already separates the two
# exactly where this row starts. RULED_ROW is NOT exempted from
# _merge_bundled_lots or _split_self_contained_subitems: a real per-row
# ruling means the PDF drew a line AT that y, not that everything between
# two consecutive rulings is exactly one logical item (confirmed
# real-corpus case needing both to keep running: Q24X10030 draws a rule
# roughly every printed line, so one item's own multi-line spec block
# spans several ruled physical rows with no rule of its own separating it
# from the next item - split's own closing-line heuristic and
# _merge_bundled_lots' merge-orphan-into-preceding rule are what
# reconcile that back into one row, regardless of a RULED_ROW flag).
RULED_ROW_FLAG = "RULED_ROW"


def _ends_sentence(text: str) -> bool:
    """Does `text` end a finished sentence, or just an abbreviation ('...doc.')
    whose period doesn't end the sentence? A trailing '.' preceded by a word
    in NON_TERMINAL_ABBREVIATIONS is treated as non-terminal; every other
    terminal-punctuation ending is trusted as-is. See NON_TERMINAL_ABBREVIATIONS
    for why this distinction matters (Q24X10030's whole-table off-by-one)."""
    t = text.rstrip()
    if not t.endswith((".", ":", ")", "!", "?")):
        return False
    if t.endswith(".") and t[:-1].split():
        last_word = t[:-1].rsplit(None, 1)[-1].lower()
        if last_word in NON_TERMINAL_ABBREVIATIONS:
            return False
    return True


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


# A word starting left of the description band's own left edge (whether
# or not it also grazes into the band on its right side - a short label
# can trail a few points across that boundary without being any less a
# label) is only folded into description when it reads as an ordinary
# word (letters and light punctuation, no digits) - never when it looks
# like a short code/tag (e.g. "AT-2X1"). Confirmed real-corpus, both
# directions, on the SAME document (Q24X10030): this table has a genuine
# "Tag No" column (its own real data - "AT-2X1".."AT-2X4" for items 1-4)
# sitting in that exact gap, which must stay OUT of description; while
# several later unnumbered rows ("Total Amount on FCA Basis",
# "Documentation Charges...", "Inspection & Testing Charges...") have
# their bold leading label word(s) land in that same gap and MUST be
# folded in, or the row loses its own heading. Digit presence is the
# cheapest reliable signal that told these apart in the confirmed case: a
# tag code always carries a digit, a label word never does.
_GAP_WORD_RX = re.compile(r"^[A-Za-z.,'&/()]+$")


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
    if best and best_overlap > 0.4:
        return best
    desc_band = next((b for b in bands if b.field == "description"), None)
    if desc_band is not None and w.x0 < desc_band.x0 and _GAP_WORD_RX.match(w.text):
        return "description"
    return "other"


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
    token ('1', '2.3', or a variant letter suffix like '3A' - confirmed
    real-corpus Sr. No. style, Q2501N005), never whitespace or multiple
    concatenated anchors. Used by __main__.py as an output-side guard: an
    item_no that fails this check is replaced with the row's own
    sequential index rather than emitted verbatim (plan §Issue 3/5 -
    catches both unsplit-sub-item concatenation and one-off garbage like a
    literal '1:00 AM')."""
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


def _price_is_placeholder(cells: dict[str, list[Word]]) -> bool:
    for pf in PRICE_FIELDS:
        if pf in cells and parse_price(join_words(cells[pf])).is_placeholder:
            return True
    return False


def _is_self_contained_item_line(cells: dict[str, list[Word]]) -> bool:
    """True if a single physical line, on its own, already carries a
    complete priced line item. Used to catch bundled, unnumbered sub-items
    (e.g. "PORTA CABIN ... 1 SET 25,64,400 25,64,400" sitting under a
    parent item with no item-number anchor of its own) that would
    otherwise be swept into the parent's row and have their independent
    prices concatenated into one unparseable string (confirmed real-corpus
    bug: Q2501N005, item 8's bundled sub-items).

    Normally requires BOTH a valid price and a valid quantity+unit reading
    (not price alone) - a stray number landing alone in a price band
    essentially never also carries a recognized-unit quantity on the same
    line, so that combination stays conservative.

    A line with its own PLACEHOLDER price ("Quoted", "Included" - never a
    bare number) and no quantity+unit reading is also accepted on its own:
    confirmed real-corpus case, Q24X10030's "additional charges" rows
    ("Documentation Charges...", "VAT/ Taxes & Duties...", each individually
    "Quoted" in its own Total column with no Qty/UOM column at all - a flat
    charge genuinely has no quantity dimension to require) and its
    "Sample Transport Line" sub-item (own price band reads "Quoted for",
    still recognized as the QUOTED placeholder, but its "Assuming 50m"
    quantity phrasing doesn't match the quantity grammar - see money.py).
    A bare NUMERIC price still requires the quantity+unit match: unlike a
    placeholder, a stray number alone in a price band is exactly the
    ambiguous case the docstring above warns about."""
    if not _has_price(cells):
        return False
    qty_text = join_words(cells.get("quantity", []))
    unit_text = join_words(cells.get("unit", []))
    pq = resolve_quantity_and_unit(qty_text, unit_text)
    if pq.value is not None:
        return True
    return _price_is_placeholder(cells)


def _split_self_contained_subitems(row: LogicalRow) -> list[LogicalRow]:
    """Split a provisional row into several when it contains TWO OR MORE
    independently self-contained item lines - each such line is its own
    real, distinct, independently-priced item that boundary detection
    merged together only because it lacked its own item-number anchor. An
    ordinary row (even with a long wrapped multi-line description) has at
    most one such line and is returned unchanged.

    Splits fall AFTER each self-contained line BY DEFAULT: that line is the
    closing data line of its own sub-item (its heading/description
    precedes it within the block), so everything up to and including it
    belongs to the current sub-item, and the next physical line starts the
    next one. Any trailing description-only tail after the last
    self-contained line (no price, no item_no) is left for
    _merge_bundled_lots to fold into the preceding split-out row, exactly
    as it already does for genuine bundled-lot continuations.

    One case splits BEFORE instead: an "opening-style" sub-item, where the
    price/qty sits on the sub-item's own HEADING line, followed by its own
    bullet-marked elaboration (confirmed real-corpus case: Q24X10030's
    "Sample Probe (Fixed Type)" / "Sample Transport Line" / "Sample
    Handling System", each carrying its own qty+price directly on the
    heading line, with "• ..." bullets underneath). Detected narrowly - a
    self-contained line whose own description text is non-empty, does NOT
    itself start with a bullet (rules out a bulleted spec line that merely
    happens to carry a price mid-paragraph - confirmed real case,
    Q2501N005's "• WITH HEATED FLOW THROUGH CELL..."), and IS immediately
    followed by a bullet-marked line (the elaboration that only makes
    sense following a heading, not preceding one). Deliberately narrow:
    checked against every self-contained line across both this project's
    real regression-test documents (Q24X10030 and Q2501N005) - it fires
    only for the four confirmed Q24X10030 headings and is a no-op
    everywhere in Q2501N005, where self-contained lines are either
    price-only (empty description) or non-bulleted text not followed by a
    bullet. Two prior attempts at this same opening/closing ambiguity
    (word-count, then font-weight/bold) were reverted for regressing
    Q2501N005's TestBundledSubItems; this signal is structural rather than
    typographic, and 118/118 tests (that one included) still pass with it
    in place."""
    self_contained_idx = [
        i for i, (_, cells) in enumerate(row.line_cells)
        if _is_self_contained_item_line(cells)
    ]
    if len(self_contained_idx) < 2:
        return [row]

    lines = row.line_cells
    # A line carrying its own valid item-number anchor always starts a new
    # item, whether or not it also happens to have a price of its own
    # (many section headings, e.g. "OXYGEN ANALYZER", don't - the price
    # sits on a LATER line, e.g. "Analyzer shelter & System integration").
    # Folding these anchor-only positions in as forced split points too -
    # not just the price-bearing self-contained ones - is required so that
    # a later CLOSING-style self-contained line doesn't walk all the way
    # back past an anchored heading in between and glue two different
    # items together. Confirmed real-corpus: Q24X10030's "OXYGEN ANALYZER"
    # heading (no price of its own) sat between "Provision for Calibration
    # Gas Bottle connection." (tag 3's own closing item) and "Analyzer
    # shelter & System integration" (tag 4's own first priced line) -
    # without this, the whole span from Provision through Analyzer
    # shelter's bullets merged into one row.
    anchor_only_idx = [
        i for i, (_, cells) in enumerate(lines)
        if i not in self_contained_idx and _valid_item_no(join_words(cells.get("item_no", [])))
    ]
    split_points = sorted(set(self_contained_idx) | set(anchor_only_idx))

    out: list[LogicalRow] = []
    start = 0
    for i in split_points:
        if i in anchor_only_idx:
            own_desc = join_words(lines[i][1].get("description", [])).strip()
            split_at = i if own_desc else i + 1
            if split_at > start:
                out.append(LogicalRow(line_cells=lines[start:split_at]))
            start = split_at
            continue
        own_desc = join_words(lines[i][1].get("description", [])).strip()
        next_desc = (
            join_words(lines[i + 1][1].get("description", [])).strip()
            if i + 1 < len(lines) else ""
        )
        # An opening-style heading isn't always followed by its own bullets
        # (Q24X10030: "Provision for Calibration Gas Bottle connection." is
        # followed by a plain-text "Note-..." line, not a bullet) - but it
        # DOES follow the PREVIOUS sub-item's own bullet-marked elaboration
        # ("Sample Handling System" -> "• Consisting of..." -> ... ->
        # "Provision..."). The line immediately before it isn't itself
        # bulleted either - it's the WRAPPED tail of that last bullet
        # ("inside SS304 enclosure, 1.5mm thick.", no bullet prefix of its
        # own) - so checking only lines[i-1] still misses it.
        #
        # Deliberately narrow: only lines[i-1] itself, or lines[i-2] when
        # lines[i-1] is a single non-bulleted wrap line, count as "a
        # bullet just before this heading" - never further back. An
        # unbounded (or even self-contained-boundary-bounded) backward
        # walk was tried first and regressed Q2501N005's "TUBE FITTING 1
        # LOT" (itself a genuine CLOSING-style item): several bulleted
        # lines sit earlier in that SAME pending block ("MEASURING
        # RANGES:" / "• O2: 0-25%", "ANALYZER OUTPUT:" / "• 4-20 MA" /
        # "• DIGITAL OUTPUTS"), but separated from "TUBE FITTING" by
        # multiple unrelated plain lines ("SPARES FOR COLD BLAST O2",
        # "PRESSURE REGULATOR -QNTY 1", ...), not a single wrap tail -
        # confirmed real difference between the two cases.
        bullet_before = False
        if i - 1 >= start:
            prev1 = join_words(lines[i - 1][1].get("description", [])).strip()
            if prev1.startswith(BULLET_CHARS):
                bullet_before = True
            elif i - 2 >= start:
                prev2 = join_words(lines[i - 2][1].get("description", [])).strip()
                bullet_before = prev2.startswith(BULLET_CHARS)
        # A self-contained line that ALSO carries its own valid item-number
        # anchor (e.g. the next GAS CHROMATOGRAPH tag's own heading line)
        # forces a fresh open ONLY when it isn't already sitting at the
        # start of the pending block (i > start) - i.e. only when there IS
        # unclosed preceding content that needs walling off, never a
        # no-op-turned-harmful override for an anchored line that's simply
        # the first thing in its own block (e.g. "Special Tools & Tackles",
        # itself anchored AND self-contained, but with nothing before it to
        # close off - forcing it open here left it unclosed until the NEXT
        # self-contained line ("Total Amount on FCA Basis") closed over
        # BOTH of them together, a real regression caught by manual review
        # after the fix below was first tried unconditionally). An anchored
        # line IS by construction the start of brand new content, never the
        # closing data line of whatever pending sub-item came before it -
        # confirmed real-corpus case needing this: Q24X10030's tag 3 GAS
        # CHROMATOGRAPH heading, reached while "Provision for Calibration
        # Gas Bottle connection." (tag 2) was still an open pending block
        # with no bullets of its own to close on.
        own_item_no = join_words(lines[i][1].get("item_no", [])).strip()
        has_own_item_no = i > start and _valid_item_no(own_item_no)
        is_opening = (
            own_desc
            and not own_desc.startswith(BULLET_CHARS)
            and (next_desc.startswith(BULLET_CHARS) or bullet_before or has_own_item_no)
        )
        split_at = i if is_opening else i + 1
        if split_at > start:
            out.append(LogicalRow(line_cells=lines[start:split_at]))
        start = split_at
    if start < len(lines):
        out.append(LogicalRow(line_cells=lines[start:]))
    return out


def _derive_boundaries_from_desc_gaps(classified: list[dict], anchor_yc: list[float]) -> list[float]:
    """Anchor row starts on gaps in the DESCRIPTION column, not on the
    item-number y-position directly - prices are frequently vertically
    centred within a tall multi-line description cell, so anchoring purely
    on item-number y drags the price onto the wrong logical row (plan
    §Row segmentation, point 1e)."""
    sorted_anchors = sorted(anchor_yc)
    desc_lines = [c for c in classified if "description" in c["cells"]]
    if len(desc_lines) < 2:
        return sorted_anchors

    ycs = [c["line"].yc for c in desc_lines]
    texts = [join_words(c["cells"]["description"]) for c in desc_lines]
    gaps = [ycs[i] - ycs[i - 1] for i in range(1, len(ycs))]
    g = median(gaps) or 1.0
    candidate_starts = [ycs[0]] + [ycs[i] for i in range(1, len(ycs)) if gaps[i - 1] > 1.8 * g]
    yc_to_idx = {yc: i for i, yc in enumerate(ycs)}

    per_anchor = []
    for a in sorted_anchors:
        below = [c for c in candidate_starts if c <= a]
        candidate = below[-1] if below else (candidate_starts[0] if candidate_starts else a)

        # Anchor precision override: when the anchor is co-located with
        # real heading text on its OWN line (it has its own "description"
        # line - most item numbers are), and the gap search reached
        # further back than that line, only trust reaching back if the
        # line immediately before the anchor's own line is clearly a
        # wrapped CONTINUATION into it (no terminal punctuation - often
        # literally cut mid-word with a hyphen) rather than a complete,
        # different sentence. A complete sentence ending in terminal
        # punctuation right before an anchor's own line is unclaimed
        # trailing content of the PREVIOUS item that the loose 1.8x-median
        # gap threshold simply didn't flag as its own paragraph break -
        # confirmed real-corpus case: Q24X10030's item "2" anchor sits on
        # "GAS CHROMATOGRAPH", immediately preceded by "...sourced locally
        # by EM." (previous item's own closing note, ending in a period) -
        # gap search reached back to "Note-..." and bled it into item 2's
        # row. Distinguishing this from the OTHER known real case this
        # gap-search exists for - a vertically centred item number sitting
        # on the SECOND line of its own two-line, hyphen-wrapped heading
        # (SQ2509N216: "SERVICE CHARGES PER MAN-" / "1 DAYS [...]", no
        # terminal punctuation on the line before) - is exactly what keeps
        # this override safe: TestSQ2509N216 is unaffected because "MAN-"
        # doesn't end in terminal punctuation, so no override fires there.
        idx = yc_to_idx.get(a)
        if idx is not None and idx > 0 and candidate < a:
            prev_text = texts[idx - 1]
            if _ends_sentence(prev_text):
                candidate = a

        per_anchor.append(candidate)

    # Two adjacent anchors can legitimately map to the SAME gap-derived
    # boundary when no larger-than-median gap separates them at all - e.g.
    # a closing-style sub-item heading that follows its predecessor's
    # content with completely ordinary line spacing (confirmed real-corpus
    # case: Q24X10030R1's "Sample Transport Line" pair, item_no "1.2a"/
    # "1.2b"). Falling back to a single, document-wide midpoint-of-anchors
    # scheme for EVERY boundary the moment any one collision is found (the
    # original fix here) throws away perfectly good gap-derived boundaries
    # for unrelated, non-colliding anchors elsewhere in the same region -
    # confirmed real-corpus regression: it fed a distant, unrelated
    # "Process Conditions" preamble bullet ("Temperature (Op/Design)...")
    # into item "1.1a"'s row purely because some OTHER pair of anchors
    # later in the same region collided. Resolve a collision locally
    # instead: only the second anchor of a colliding pair gets its own
    # boundary replaced, with the midpoint of the two anchors' own
    # y-positions (the original centred-item-number rationale - the
    # anchor is frequently not on the row's own top line, so this is
    # still an approximation, never the exact anchor y) - every other
    # anchor's gap-derived boundary is left untouched.
    region_top = min(c["line"].yc for c in classified)
    boundaries = [min(per_anchor[0], region_top)]
    for i in range(1, len(sorted_anchors)):
        b = per_anchor[i]
        if b <= boundaries[-1]:
            b = (sorted_anchors[i - 1] + sorted_anchors[i]) / 2
        boundaries.append(b)
    return boundaries


# ==========================================================================
# LAYOUT: TOP-anchored vs CENTRED item numbers
# ==========================================================================
#
# Two real, common table layouts need OPPOSITE row-boundary rules, and every
# attempt to serve both with one shared rule fixed one and broke the other
# (PROJECT_NOTES.md, 2026-09-23):
#
# - TOP (the default; everything before this existed assumed it): the item
#   number sits on or next to the item's own first line - Q24X10030's GAS
#   CHROMATOGRAPH table ("1 GAS CHROMATOGRAPH" / "Tag No..." / "Make...").
# - CENTRED: the item number and its price are vertically centred in one
#   tall merged cell, so the item's heading, make and model sit many lines
#   ABOVE the anchor - Q2501N005 ("HOT EXTRACTIVE ... LASER TYPE" at y 226,
#   its "1A" / price at y 376, its last spec line at y 519).
#
# A region is only treated as CENTRED on positive evidence (detect_layout);
# segment_rows' default stays TOP, i.e. exactly the pre-existing code path.

LAYOUT_TOP = "TOP"
LAYOUT_CENTRED = "CENTRED"

# Description lines that must sit above the FIRST anchor, on a headed page,
# before a centred layout is even considered. Q24X10030's Section-I table
# (3-line cells, anchor on the middle line) has 1; its GC table has 0;
# Q2501N005 has 9-12.
CENTRED_MIN_LINES_ABOVE_FIRST_ANCHOR = 2

# Mean |block centre - anchor| over fully-bounded blocks, in line pitches,
# above which a centred reading is rejected as not actually fitting.
CENTRED_MAX_MEAN_CENTRE_ERROR = 1.5

# Weight of "cut at a larger-than-usual gap" against "anchor at the block's
# centre" in _derive_boundaries_centred. Traced on Q2501N005: the true item
# gaps are only ~1.35x the median pitch (so the 1.8x threshold used by the
# TOP path never sees them), and on page 1 the largest gap between 1A and
# 1B is the one right AFTER the anchor line, not the item break - so neither
# signal alone is enough. 4 separates every traced case on both documents.
CENTRED_GAP_WEIGHT = 4.0

# ...capped, so one unusually wide gap INSIDE an item (a spacer before a
# sub-section, e.g. Q2501N005 page 2's 26pt gap above "LASER GAS ANALYSER")
# can't outweigh the centring evidence on its own.
CENTRED_MAX_GAP_BONUS = 2.0

# Cost per description line left BEFORE the first block (handed to the
# previous row as a continuation tail). Without it the fit "cheats": it
# leaves the first item's heading unanchored and wraps a tiny block round
# the anchor, since a 2-line block is trivially centred. Must stay low
# enough that a real tail still wins when it's set off by a clear gap -
# Q2501N005 page 8 opens with 2 lines of item 7's NOTE, then a 20pt gap.
CENTRED_LEADING_LINE_PENALTY = 1.0


def _classified_lines(words: list[Word], bands) -> list[dict]:
    lines = cluster_lines(words)
    lines = [ln for ln in lines if not _is_letterhead_boilerplate(join_words(ln.words))]
    return _classify_lines(lines, bands) if lines else []


def _anchor_ycs(classified: list[dict]) -> list[float]:
    return [
        c["line"].yc for c in classified
        if "item_no" in c["cells"] and _valid_item_no(join_words(c["cells"]["item_no"]))
    ]


def _line_pitch(classified: list[dict]) -> float:
    ycs = [c["line"].yc for c in classified if "description" in c["cells"]]
    return median([b - a for a, b in zip(ycs, ycs[1:])]) or 1.0


def _anchors_carry_own_price(classified: list[dict], anchors: list[float]) -> bool:
    """The defining trait of the centred layout: the item number and its
    price are centred TOGETHER, so most anchors have a price on their own
    line or the adjacent one. A vendor that top-aligns the number and
    centres only the price looks centred by line-count alone but fails
    this - confirmed on Q2409G010R5 (items 1.2 / 1.3 / 2.1 / 2.2 / 2.3:
    number on the heading line, price 2-6 lines lower, 0 of 5 co-located).
    Majority, not all: an item quoted as a rate ("@3,300 PER METER",
    Q2501N005's 3C / 6D) legitimately has no price at all."""
    pitch = _line_pitch(classified)
    price_ycs = [c["line"].yc for c in classified if _has_price(c["cells"])]
    own = sum(any(abs(p - a) <= pitch for p in price_ycs) for a in anchors)
    return 2 * own > len(anchors)


# A gap wider than this many line pitches is a paragraph break.
CENTRED_PARAGRAPH_GAP = 1.6


def _anchors_look_top_aligned(classified: list[dict], anchors: list[float]) -> bool:
    """Per-anchor vote on where the number sits in its item:

    - TOP vote: the anchor's own line opens a paragraph AND at least two
      more lines of that paragraph follow - a heading followed by its body.
      Confirmed on Q25N10067R1's "SECTION 2" table (1.4 / 1.5 / 2), which
      prints its price on the heading line, so it passes
      _anchors_carry_own_price and would otherwise inherit CENTRED from the
      same document's first table.
    - CENTRED vote: the anchor's line carries no description text (a number
      floating in a spacer - Q2501C001R2's "1", Q2501N005's "1B"), or it
      continues a paragraph that started above it (Q2501N005's "1A").
    - No vote: a 1-2 line cell, where centred and top-aligned look the same
      (most of Q25N10067R1's first table - genuinely centred cells whose
      short items put the number on their first line).

    True only when TOP votes outnumber CENTRED ones."""
    pitch = _line_pitch(classified)
    para = CENTRED_PARAGRAPH_GAP * pitch
    desc = sorted(c["line"].yc for c in classified if "description" in c["cells"])
    anchor_set = sorted(anchors)
    top = centred = 0
    for a in anchor_set:
        prev = [y for y in desc if y < a - 0.5]
        if not prev:
            continue
        own = [y for y in desc if abs(y - a) <= 0.5]
        if not own:
            centred += 1
            continue
        if a - prev[-1] <= para:
            centred += 1
            continue
        nxt_anchor = next((b for b in anchor_set if b > a + 0.5), float("inf"))
        follow, last = 0, a
        for y in desc:
            if y <= a + 0.5:
                continue
            if y >= nxt_anchor or y - last > para:
                break
            follow, last = follow + 1, y
        if follow >= 2:
            top += 1
    return top > centred


def _derive_boundaries_centred(classified: list[dict], anchor_yc: list[float]) -> tuple[list[float], float]:
    """Row start y-values for a CENTRED-layout region, plus the fit's mean
    centre error (in line pitches) over the blocks whose both ends lie on
    this page. One block per anchor; chosen jointly by dynamic programming
    so that each anchor sits as close as possible to the vertical centre of
    its own block, with a bonus for cutting at a larger-than-usual line gap.

    Blocks are runs of description lines; a cut sits in the gap between two
    of them. The LAST block is open-ended (it may continue onto the next
    page), so it carries no centre cost. Lines before the first block (a
    previous page's item tail) get their own leading row, which the
    existing merge passes fold into the previous item."""
    anchors = sorted(anchor_yc)
    desc = [c["line"].yc for c in classified if "description" in c["cells"]]
    region_top = min(c["line"].yc for c in classified)
    m, n = len(desc), len(anchors)
    if m < 2 or n < 2:
        # One anchor: no fully-bounded block to fit a centre to, and the
        # whole region is that one item's.
        return [region_top], 0.0

    gaps = [desc[i] - desc[i - 1] for i in range(1, m)]
    g = median(gaps) or 1.0

    # Cut j (0..m) = "a block starts at desc line j"; cut 0 is the region top.
    def cut_y(j: int) -> float:
        return float("-inf") if j == 0 else (desc[j - 1] + desc[j]) / 2

    def cut_bonus(j: int) -> float:
        if j == 0:
            return 0.0
        return -min(CENTRED_MAX_GAP_BONUS, CENTRED_GAP_WEIGHT * (gaps[j - 1] - g) / g)

    INF = float("inf")
    # cost[k][j]: best total with block k starting at cut j.
    cost = [[INF] * m for _ in range(n)]
    back = [[-1] * m for _ in range(n)]
    for j in range(m):
        if cut_y(j) <= anchors[0]:
            cost[0][j] = cut_bonus(j) + CENTRED_LEADING_LINE_PENALTY * j
    for k in range(1, n):
        for j in range(1, m):
            # block k starts at cut j: must sit after anchor k-1, at/before anchor k
            if not (anchors[k - 1] < cut_y(j) <= anchors[k]):
                continue
            for i in range(j):
                if cost[k - 1][i] == INF:
                    continue
                if i > 0 and not cut_y(i) <= anchors[k - 1]:
                    continue
                centre = (desc[i] + desc[j - 1]) / 2
                c = cost[k - 1][i] + abs(centre - anchors[k - 1]) / g + cut_bonus(j)
                if c < cost[k][j]:
                    cost[k][j], back[k][j] = c, i

    best_j = min(range(m), key=lambda j: cost[n - 1][j])
    if cost[n - 1][best_j] == INF:
        return [region_top], INF

    cuts = [best_j]
    for k in range(n - 1, 0, -1):
        cuts.append(back[k][cuts[-1]])
    cuts.reverse()

    errors = [
        abs((desc[cuts[k]] + desc[cuts[k + 1] - 1]) / 2 - anchors[k]) / g
        for k in range(n - 1)
    ]
    mean_error = sum(errors) / len(errors) if errors else 0.0

    boundaries = [region_top if j == 0 else cut_y(j) for j in cuts]
    if cuts[0] > 0:
        boundaries.insert(0, region_top)
    return boundaries, mean_error


def detect_layout(words: list[Word], bands) -> str:
    """Classify a HEADED region's layout (see the section banner above).
    Only meaningful on the page that carries the table's own header: there
    the first item starts right below the header, so lines above the first
    anchor can only be that item's own heading. A continuation page may
    open mid-item, so callers pass the headed page's result on to it
    rather than re-detecting there."""
    classified = _classified_lines(words, bands)
    anchors = sorted(_anchor_ycs(classified))
    if not anchors:
        return LAYOUT_TOP

    above = sum(1 for c in classified if "description" in c["cells"] and c["line"].yc < anchors[0] - 0.5)
    if above < CENTRED_MIN_LINES_ABOVE_FIRST_ANCHOR:
        return LAYOUT_TOP
    if not _anchors_carry_own_price(classified, anchors):
        return LAYOUT_TOP
    if _anchors_look_top_aligned(classified, anchors):
        return LAYOUT_TOP

    if len(anchors) >= 2:
        _, mean_error = _derive_boundaries_centred(classified, anchors)
        if mean_error > CENTRED_MAX_MEAN_CENTRE_ERROR:
            return LAYOUT_TOP
    return LAYOUT_CENTRED


def _border_only(ruling_ys: list[float], classified: list[dict]) -> bool:
    """True when no ruling lies strictly inside the content between the
    outermost rulings - i.e. they're the page's own frame, not row rules.
    Q2501N005's continuation pages carry exactly two (y 116.8 / 758.5)."""
    lo, hi = min(ruling_ys), max(ruling_ys)
    inside = [c["line"].yc for c in classified if lo <= c["line"].yc <= hi]
    if not inside:
        return True
    first, last = min(inside), max(inside)
    return not any(first + 1 < r < last - 1 for r in ruling_ys)


def _segment_rows_centred(classified: list[dict], ruling_ys: list[float] | None) -> list[LogicalRow] | None:
    if ruling_ys and len(ruling_ys) >= 2:
        if not _border_only(ruling_ys, classified):
            # Real per-row rulings beat any geometric inference.
            return None
        # Frame only: keep the frame's one useful effect in the TOP path -
        # text above the top frame line (running page header / URL) is
        # outside the table.
        top = min(ruling_ys)
        classified = [c for c in classified if c["line"].yc >= top]
        if not classified:
            return []

    anchors = _anchor_ycs(classified)
    if not anchors:
        return None
    # The layout is inherited from the headed page; a page whose own
    # anchors don't sit with their prices doesn't follow it (a document can
    # mix conventions - Q2409G010R5) and takes the ordinary path instead.
    if not _anchors_carry_own_price(classified, anchors):
        return None
    if _anchors_look_top_aligned(classified, anchors):
        return None

    # An unnumbered sub-item in this layout has its price centred in its
    # own cell just like a numbered one, so its self-contained price line is
    # a block centre too. Confirmed on Q2606H02: a continuation page holding
    # an unnumbered "PORTA CABIN" (price at y 290) above numbered item "5"
    # (y 375) - with only real anchors, the whole page became item 5. Also
    # gives a NOTE whose overflow text lands in the price column
    # (Q2501N005 page 4, "QUOTED DUST") its own row instead of
    # concatenating two prices on the item above. Price lines within a
    # line pitch of a real anchor are that anchor's own price (Q2501N005's
    # "6C"/"6E"/"7" print it ~6pt below the number), not a new centre.
    # A price line below an anchor that has NO price of its own is that
    # anchor's price (the page mixes in a top-aligned number), not a new
    # centre - confirmed on Q25N10067R1's "1.3 Sample Transportation Tube",
    # whose price sits 3 lines down: as a centre it split the heading off.
    pitch = _line_pitch(classified)
    price_ycs = sorted(c["line"].yc for c in classified if _has_price(c["cells"]))
    anchors_sorted = sorted(anchors)

    def claimed_by_priceless_anchor(y: float) -> bool:
        above = [a for a in anchors_sorted if a < y]
        if not above:
            return False
        a = above[-1]
        if any(abs(p - a) <= pitch for p in price_ycs):
            return False  # nearest anchor above already has its own price
        # ...and this is the first price line after it
        return next((p for p in price_ycs if p > a + pitch), None) == y

    centres = sorted(anchors + [
        c["line"].yc for c in classified
        if _is_self_contained_item_line(c["cells"])
        and all(abs(c["line"].yc - a) > pitch for a in anchors)
        and not claimed_by_priceless_anchor(c["line"].yc)
    ])
    boundaries, _ = _derive_boundaries_centred(classified, centres)

    rows: list[LogicalRow] = []
    for i, b0 in enumerate(boundaries):
        b1 = boundaries[i + 1] if i + 1 < len(boundaries) else float("inf")
        row_lines = [c for c in classified if b0 - 0.01 <= c["line"].yc < b1]
        if row_lines:
            rows.append(LogicalRow(line_cells=[(c["line"], c["cells"]) for c in row_lines]))

    # Every block is built round exactly one centre, so neither TOP-layout
    # repair pass applies: _split_self_contained_subitems would cut an
    # item's heading off at its centred price line, and
    # _reattach_misattributed_leading_lines would move the lines above the
    # anchor - this layout's own heading - to the previous row.
    return _merge_bundled_lots(rows)


def segment_rows(
    words: list[Word], bands, ruling_ys: list[float] | None = None, layout: str = LAYOUT_TOP,
) -> list[LogicalRow]:
    if not words:
        return []

    classified = _classified_lines(words, bands)
    if not classified:
        return []

    if layout == LAYOUT_CENTRED:
        rows = _segment_rows_centred(classified, ruling_ys)
        if rows is not None:
            return rows
        # No anchors on this page, or real row rulings: fall through to the
        # ordinary path below.

    item_anchors = _anchor_ycs(classified)
    money_lines_yc = [c["line"].yc for c in classified if _has_price(c["cells"])]

    # True only when ruling_ys carries a REAL per-row divider, not just the
    # table's own outer top/bottom frame (_border_only, already used by the
    # CENTRED path for the same distinction) - a 2-line frame with nothing
    # ruled between its own content is not evidence any two lines belong in
    # different rows, so it must not suppress the repair heuristics below.
    ruled_boundaries = False
    if ruling_ys and len(ruling_ys) >= 2:
        boundaries = sorted(set(ruling_ys))
        ruled_boundaries = not _border_only(ruling_ys, classified)
    elif len(item_anchors) >= 2:
        # Collision handling (two anchors mapping to the same gap-derived
        # boundary) is resolved locally, per colliding pair, inside
        # _derive_boundaries_from_desc_gaps itself - see its docstring.
        boundaries = _derive_boundaries_from_desc_gaps(classified, item_anchors)
    else:
        boundaries = sorted(set(money_lines_yc))
        # A row boundary at the FIRST price line leaves every line above it
        # outside every row - silently dropped, not flagged. Confirmed on
        # Q2501N005 (no item_no band detected): item 1A's whole heading /
        # make / model block (y 226-364) sat above its centred price line
        # (y 376) and never reached the output. Open the first row at the
        # region's own top instead, the same clamp the anchor path already
        # applies (_derive_boundaries_from_desc_gaps' region_top).
        if boundaries:
            boundaries[0] = min(boundaries[0], classified[0]["line"].yc)

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
        if ruled_boundaries:
            row.flags.append(RULED_ROW_FLAG)
        rows.append(row)

    # Split out bundled, unnumbered sub-items (see
    # _split_self_contained_subitems) before the merge pass below - each
    # split-out row already has its own price, so _merge_bundled_lots'
    # "no item_no and no price -> merge into preceding row" rule correctly
    # leaves them as their own rows. Run unconditionally, even for a
    # RULED_ROW - see RULED_ROW_FLAG's docstring (Q24X10030 needs this to
    # keep running on ruled buckets too). A row this function actually
    # splits gets fresh LogicalRow objects with no flags of their own,
    # which is correct: a bucket split apart here was never really "one
    # PDF-drawn row" to begin with.
    split_rows: list[LogicalRow] = []
    for row in rows:
        split_rows.extend(_split_self_contained_subitems(row))

    return _reattach_misattributed_leading_lines(_merge_bundled_lots(split_rows))


def _reattach_misattributed_leading_lines(rows: list[LogicalRow]) -> list[LogicalRow]:
    """A row can end up with content glued in front of its own item-number
    anchor's line - trailing, unclaimed content of the PREVIOUS item that
    no earlier boundary/split step recognised as belonging there. Confirmed
    real-corpus case this catches that _derive_boundaries_from_desc_gaps'
    own anchor-precision override (see its docstring) cannot: a ruled
    table whose only detected ruling_ys are the page's own outer top/bottom
    border (2 points), so segment_rows' ruled branch treats the ENTIRE
    page as one row before self-contained-line splitting ever runs -
    Q24X10030's item "3" row started with item 2's own leftover
    "Note-...by EM." closing text instead of its own "GAS CHROMATOGRAPH"
    heading, because that whole-page row never went through the
    anchor/gap path at all.

    Same signal as the gap-search override: a complete sentence (terminal
    punctuation) on the line right before the item-number anchor's own
    line is never a wrapped continuation INTO this row's heading, so it
    can only be unclaimed trailing content of the previous row - move it
    there. Runs as a final pass over the finished row list so it applies
    regardless of which boundary strategy (ruled / gap-derived / money-
    line) produced the rows - rows with no item_no anchor (e.g. the
    bundled-subitem case _split_self_contained_subitems handles) are
    untouched, since item_no_idx is None for them."""
    if len(rows) < 2:
        return rows
    for i in range(1, len(rows)):
        row = rows[i]
        # A RULED_ROW's own top edge is a real PDF ruling, not a guess
        # this heuristic gets to second-guess - see RULED_ROW_FLAG.
        if RULED_ROW_FLAG in row.flags:
            continue
        item_no_idx = next(
            (j for j, (_, cw) in enumerate(row.line_cells)
             if "item_no" in cw and _valid_item_no(join_words(cw["item_no"]))),
            None,
        )
        if not item_no_idx:
            continue
        _, prev_cells = row.line_cells[item_no_idx - 1]
        if "description" not in prev_cells:
            continue
        prev_text = join_words(prev_cells["description"])
        if not _ends_sentence(prev_text):
            continue
        rows[i - 1].line_cells.extend(row.line_cells[:item_no_idx])
        row.line_cells = row.line_cells[item_no_idx:]
    return rows


def peel_leading_continuation_lines(prev_row: LogicalRow, next_rows: list[LogicalRow]) -> list[LogicalRow]:
    """Move a bullet-marked leading line (or run of them) off the FIRST of
    next_rows onto prev_row, before next_rows is appended after it across
    a page-stitch boundary. A legitimate new row never opens directly on a
    bare bullet with no heading of its own above it - a bullet there can
    only be a continuation of the PREVIOUS item, pushed onto the new page
    purely by pagination (confirmed real-corpus case: Q24X10030's "Sample
    Probe (Fixed Type)" prints its own heading AND price on page 5; its
    own third bullet, "with full port gate valve...", is pushed onto page
    6 by pagination and lands as the leading content of an unrelated
    orphan row there instead of staying attached to Sample Probe).

    Whole-row _merge_bundled_lots (no item_no AND no price for the ENTIRE
    row) can't catch this: by the time the new page's own row is fully
    formed, it may have already absorbed a genuinely separate later
    item's own price within that same page (as happened here - the
    orphaned bullet line merged with "Sample Transport Line"'s own
    content before this function ever runs), so the row as a whole no
    longer looks like a bundled lot. Only the LEADING line(s) are the
    actual continuation; this peels precisely those, at the true page
    boundary where the page identity of each row is still known - which
    is why this must run per stitch point, not as a single pass over the
    fully flattened row list afterward."""
    if not next_rows:
        return next_rows
    first_row = next_rows[0]
    peel_upto = 0
    for _, cells in first_row.line_cells:
        desc = join_words(cells.get("description", [])).strip()
        if not desc.startswith(BULLET_CHARS):
            break
        peel_upto += 1
    if peel_upto == 0:
        return next_rows
    prev_row.line_cells.extend(first_row.line_cells[:peel_upto])
    remaining = first_row.line_cells[peel_upto:]
    if not remaining:
        return next_rows[1:]
    first_row.line_cells = remaining
    return next_rows


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
