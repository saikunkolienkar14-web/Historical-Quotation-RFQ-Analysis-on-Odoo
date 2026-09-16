"""
Geometric primitives shared by the ruled and banded row-segmentation paths.

Design note (plan Step 3, "Row segmentation"): plain y-rounding false-splits
single rows because of baseline jitter (e.g. a part number sitting at
y=277.5 while the rest of its row sits at y=279.4). Lines are clustered with
a tolerance derived from the actual glyph height on the page, not a fixed
constant.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import ftfy


def clean_text(text: str) -> str:
    """Repair UTF-8-decoded-as-cp1252 mojibake ("â€“" -> "–", "Â½" -> "½",
    ...) that PyMuPDF occasionally surfaces from a PDF's embedded font
    encoding. Applied once here, at word extraction - the earliest point
    text is stored - so every downstream field (description, raw_row_text,
    labeled make/model values, ...) is clean without a separate pass."""
    if not text:
        return text
    return ftfy.fix_text(text)


@dataclass(frozen=True)
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block_no: int = 0
    line_no: int = 0
    word_no: int = 0

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @classmethod
    def from_pymupdf_tuple(cls, t: tuple) -> "Word":
        x0, y0, x1, y1, text, block_no, line_no, word_no = t
        return cls(x0, y0, x1, y1, clean_text(text), block_no, line_no, word_no)


@dataclass
class Line:
    words: list[Word] = field(default_factory=list)

    @property
    def y0(self) -> float:
        return min(w.y0 for w in self.words)

    @property
    def y1(self) -> float:
        return max(w.y1 for w in self.words)

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2

    def text(self, join_words) -> str:
        ordered = sorted(self.words, key=lambda w: w.x0)
        return join_words(ordered)


@dataclass(frozen=True)
class ColumnBand:
    field: str  # item_no, description, quantity, unit, unit_price, total_price, part_no, other
    x0: float
    x1: float

    def contains(self, word: Word) -> bool:
        return self.x0 <= word.xc <= self.x1

    def overlap_fraction(self, x0: float, x1: float) -> float:
        """Fraction of [x0,x1] that lies inside this band - used for
        mapping a table's detected cell bbox onto a band by overlap rather
        than by index (plan §Column bands: index-based mapping is wrong
        when header/data cells sit at different indices)."""
        lo = max(self.x0, x0)
        hi = min(self.x1, x1)
        width = x1 - x0
        if width <= 0:
            return 0.0
        return max(0.0, hi - lo) / width


def median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def cluster_lines(words: list[Word], y_tol: float | None = None) -> list[Line]:
    """Group words into text lines by y-centre proximity.

    y_tol default: 0.6 * median glyph height on the given word set (plan
    §Row segmentation, point 1f) - plain round(y/3) bucketing is too coarse
    and merges/splits rows incorrectly under real-world baseline jitter.
    """
    if not words:
        return []
    if y_tol is None:
        y_tol = 0.6 * median([w.height for w in words]) or 3.0

    ordered = sorted(words, key=lambda w: w.yc)
    lines: list[Line] = []
    current: list[Word] = [ordered[0]]
    current_mean_yc = ordered[0].yc

    for w in ordered[1:]:
        if abs(w.yc - current_mean_yc) <= y_tol:
            current.append(w)
            current_mean_yc = sum(x.yc for x in current) / len(current)
        else:
            lines.append(Line(words=current))
            current = [w]
            current_mean_yc = w.yc
    lines.append(Line(words=current))

    lines.sort(key=lambda ln: ln.yc)
    return lines


def join_words(words: list[Word]) -> str:
    """Join words left-to-right with a single space. Deliberately the SAME
    function used for both cell text and raw_row_text (plan §raw_row_text
    caveat) so verbatim-containment holds regardless of how a money token
    might get kerning-split by PyMuPDF."""
    return " ".join(w.text for w in sorted(words, key=lambda w: w.x0))
