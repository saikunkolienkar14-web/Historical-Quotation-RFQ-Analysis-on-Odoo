"""
Tests for the two known-bug fixes landed 2026-09-18 (see PROJECT_NOTES.md
Future Work #2 / docs/COORDS_EXTRACTOR.md#known-limitations):

1. columns.infer_bands_from_words + rows._unheaded_continuation_rows no
   longer blindly reuse a stale column-band set on an unheaded
   continuation page whose layout has shifted.
2. __main__.py now withholds unit_price/total_price (keeping the raw text)
   on any row ruled._spanned_price_ranges flags as a merged/rowspan price
   cell, instead of leaving a plausible-but-wrong number in place.

The real-document cases are the exact fixtures named in
docs/COORDS_EXTRACTOR.md as the confirmed root-caused instances of each
bug, so these are true regression tests, not just synthetic-logic checks.

stdlib-only (unittest), matching the rest of the project.

Run: venv\\Scripts\\python.exe -m unittest tests.test_price_bug_fixes -v
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boq_coords.columns import (  # noqa: E402
    MIN_INFERRED_BAND_WIDTH,
    MIN_PRICE_LIKE_VALUE,
    infer_bands_from_words,
)
from boq_coords.geometry import Word  # noqa: E402

Q24S10074_VOC_GC = (
    ROOT / "Quotation PDFs" / "raw" / "Q24S10074" / "Q24S10074_VOC_GC.pdf"
)
Q25N10067_REVISIONS = [
    ROOT / "Quotation PDFs" / "raw" / "Q25N10067" / name
    for name in (
        "Q25N10067R1_Bechtel_RIL NMD_11.04.25.pdf",
        "Q25N10067R2_Bechtel_RIL NMD_29.04.25.pdf",
        "Q25N10067R3_Bechtel_RIL NMD_20.05.25.pdf",
    )
]


def _word(x0, y0, x1, y1, text="x"):
    return Word(x0=x0, y0=y0, x1=x1, y1=y1, text=text)


class InferBandsFromWordsTest(unittest.TestCase):
    """Pure-function tests - no PDF involved. infer_bands_from_words
    classifies clusters by CONTENT (money/quantity), not just position -
    it does not force exactly len(field_order) bands, since the real bug
    it exists to fix is a continuation page that CRAMS several parent
    columns into fewer visual ones (see the function's own docstring)."""

    def _rows(self, *rows, col_x=(0, 20, 120)):
        """Build several lines' worth of words at three fixed x-starts -
        item_no, description, unit_price - each `rows` entry a
        (item_no_text, description_text, price_text) triple."""
        words = []
        y = 0
        for item_no, desc, price in rows:
            for x0, text in zip(col_x, (item_no, desc, price)):
                if text:
                    words.append(_word(x0, y, x0 + len(text) * 6, y + 10, text))
            y += 12
        return words

    def test_recovers_item_no_description_and_price_columns(self):
        words = self._rows(
            ("1", "Widget Assembly", "1,500"),
            ("2", "Gasket Set", "2,000"),
            ("3", "Bracket Kit", "3,250"),
        )
        bands = infer_bands_from_words(words, ["item_no", "description", "unit_price"])
        self.assertIsNotNone(bands)
        by_field = {b.field: b for b in bands}
        self.assertEqual(set(by_field), {"item_no", "description", "unit_price"})
        self.assertTrue(by_field["item_no"].x1 <= by_field["description"].x0 + 1e-6)
        self.assertTrue(by_field["description"].x1 <= by_field["unit_price"].x0 + 1e-6)

    def test_bare_small_digits_are_not_mistaken_for_a_price_column(self):
        # Item numbers ("1", "2", "3") satisfy money.parse_price's own
        # permissive grammar just as well as a real price - the fix must
        # not let the leftmost item_no column get classified as the price
        # column just because it also "parses as money".
        words = self._rows(
            ("1", "Widget Assembly", "1,500"),
            ("2", "Gasket Set", "2,000"),
            ("3", "Bracket Kit", "3,250"),
        )
        bands = infer_bands_from_words(words, ["item_no", "description", "unit_price"])
        self.assertIsNotNone(bands)
        by_field = {b.field: b for b in bands}
        self.assertLess(by_field["item_no"].x1, by_field["unit_price"].x0)

    def test_single_combined_cell_recovers_quantity_with_embedded_unit(self):
        # A continuation page's compressed layout: no separate unit
        # column, just "1 No." etc in one cluster - parse_quantity's own
        # regex already extracts the embedded unit, so one cluster is
        # enough (this is the actual documented bug shape).
        words = self._rows(
            ("1", "Sample Probe", "1 No."),
            ("2", "Sample Tube", "20 Meters"),
            ("3", "Heater Coil", "1 Lot"),
            col_x=(0, 20, 120),
        )
        bands = infer_bands_from_words(words, ["item_no", "description", "quantity"])
        self.assertIsNotNone(bands)
        self.assertEqual({b.field for b in bands}, {"item_no", "description", "quantity"})

    def test_returns_none_when_a_wanted_price_field_finds_no_money_cluster(self):
        # Only two real columns exist and neither is money - nothing to
        # recover a price field from, so this must not guess.
        words = self._rows(
            ("1", "Widget Assembly", ""),
            ("2", "Gasket Set", ""),
            ("3", "Bracket Kit", ""),
            col_x=(0, 20, 120),
        )
        self.assertIsNone(infer_bands_from_words(words, ["item_no", "description", "unit_price"]))

    def test_returns_none_when_only_one_real_column(self):
        words = [_word(0, y, 40, y + 10, "Widget") for y in (0, 12, 24)]
        self.assertIsNone(infer_bands_from_words(words, ["item_no", "description"]))

    def test_returns_none_for_single_field_or_no_words(self):
        self.assertIsNone(infer_bands_from_words([_word(0, 0, 4, 10)], ["description"]))
        self.assertIsNone(infer_bands_from_words([], ["item_no", "description"]))

    def test_constants_are_sane(self):
        self.assertGreater(MIN_INFERRED_BAND_WIDTH, 0)
        self.assertGreater(MIN_PRICE_LIKE_VALUE, 10)  # excludes item-no-shaped digits


@unittest.skipUnless(Q24S10074_VOC_GC.exists(), "sample corpus not present")
class SpannedPriceCellFixTest(unittest.TestCase):
    """Confirmed real case (docs/COORDS_EXTRACTOR.md): one price stated
    once for items 1-4 ("Gas Chromatograph with SHS" .. "Sample heat
    tracer line"), previously silently attributed to item 3 alone with
    confidence=HIGH. After the fix, any row flagged
    PRICE_CELL_SPANS_MULTIPLE_ROWS must have its numeric price withheld
    (raw text kept, per CLAUDE.md "raw stays beside derived")."""

    @classmethod
    def setUpClass(cls):
        from boq_coords.__main__ import process_document
        cls.items, cls.summary = process_document(Q24S10074_VOC_GC, "TEST", set())

    def test_at_least_one_spanned_row_found(self):
        spanned = [
            r for r in self.items
            if "PRICE_CELL_SPANS_MULTIPLE_ROWS" in (r.get("validation_error") or "")
        ]
        self.assertTrue(spanned, "expected the known spanned-price-cell row in this fixture")

    def test_spanned_rows_have_no_numeric_price(self):
        # Every row within the flagged span must have its NUMERIC price
        # withheld - but only the one row the y-centre bucketing actually
        # attributed the merged cell's text to (documented as item 3,
        # "Sample Probe") carries the raw text; the other rows in the
        # span never had their own price text to begin with (that's the
        # bug: one cell's text for a group of 4 items).
        spanned = [
            r for r in self.items
            if "PRICE_CELL_SPANS_MULTIPLE_ROWS" in (r.get("validation_error") or "")
        ]
        for row in spanned:
            self.assertEqual(row["unit_price"], "", row)
            self.assertEqual(row["total_price"], "", row)
            self.assertEqual(row["confidence"], "LOW")

    def test_raw_price_text_is_not_lost_for_the_row_it_landed_on(self):
        spanned = [
            r for r in self.items
            if "PRICE_CELL_SPANS_MULTIPLE_ROWS" in (r.get("validation_error") or "")
        ]
        self.assertTrue(
            any(r["unit_price_raw"] or r["total_price_raw"] for r in spanned),
            "expected at least one spanned row to still carry the raw price "
            "text (the row segment_rows' y-centre bucketing attributed it "
            "to) - CLAUDE.md 'raw stays beside derived' means the number "
            "is withheld, not the text",
        )


@unittest.skipUnless(
    all(p.exists() for p in [Q24S10074_VOC_GC, *Q25N10067_REVISIONS]),
    "sample corpus not present",
)
class ContinuationBandDriftFixTest(unittest.TestCase):
    """Confirmed real cases (docs/COORDS_EXTRACTOR.md): a continuation
    page's combined qty+unit cell ("1 No.", "20 Nos") sliding under a
    stale reused unit_price band, its leading digit misread as a
    sub-1000 "price" with quantity/unit both blank.

    These documents also carry at least one UNRELATED instance of a
    similar-looking signature on their own header-matched (non-
    continuation) table - out of scope for this fix, which only touches
    rows._unheaded_continuation_rows - so assertions here are scoped to
    rows tagged _continuation_bands_reinferred=True (the new code path
    actually firing), not the whole document."""

    def test_reinference_fires_on_at_least_one_real_document(self):
        # Confirms the new code path isn't dead: if it never fires on any
        # of the three confirmed real fixtures, the plausibility gate (or
        # something upstream) is rejecting every real case, which would
        # make the rest of this test vacuously pass.
        from boq_coords.__main__ import process_document

        any_reinferred = False
        for pdf in [Q24S10074_VOC_GC, *Q25N10067_REVISIONS]:
            items, _ = process_document(pdf, "TEST", set())
            if any(r.get("_continuation_bands_reinferred") for r in items):
                any_reinferred = True
        self.assertTrue(
            any_reinferred,
            "expected at least one row across these fixtures to have used "
            "reinferred continuation bands",
        )

    def test_reinferred_rows_never_show_the_qty_unit_misread_signature(self):
        from boq_coords.__main__ import process_document

        for pdf in [Q24S10074_VOC_GC, *Q25N10067_REVISIONS]:
            items, _ = process_document(pdf, "TEST", set())
            reinferred = [r for r in items if r.get("_continuation_bands_reinferred")]
            with self.subTest(pdf=pdf.name):
                bad = [
                    r for r in reinferred
                    if r.get("unit_price") not in (None, "")
                    and float(r["unit_price"]) < 1000
                    and not r.get("quantity")
                    and not r.get("unit")
                ]
                self.assertEqual(
                    bad, [],
                    f"{pdf.name}: a reinferred-bands row still shows the old "
                    f"qty-misread-as-price signature",
                )


if __name__ == "__main__":
    unittest.main()
