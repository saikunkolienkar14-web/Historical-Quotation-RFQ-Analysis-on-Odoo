"""
Integration regression tests against two real documents in the corpus,
locking in the values verified manually during development (see plan
Step 3). If these ever start failing, something in locate/columns/banded
broke silently.

stdlib-only (unittest), matching the rest of the project.

Run: venv\\Scripts\\python.exe -m unittest tests.test_boq_coords_integration -v
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf  # noqa: E402

from boq_coords.banded import segment_rows  # noqa: E402
from boq_coords.fields import derive_item_hierarchy, extract_heading  # noqa: E402
from boq_coords.geometry import ColumnBand, Word  # noqa: E402
from boq_coords.money import parse_price  # noqa: E402
from boq_coords.rows import extract_document_tables  # noqa: E402

SQ2507E254 = (
    ROOT / "Quotation PDFs" / "raw" / "SQ2507E254"
    / "OFFER-SQ2507E254-Meghalaya Cements Ltd-SO2 ANALYSER -1100068223.pdf"
)
SQ2603W060 = (
    ROOT / "Quotation PDFs" / "raw" / "SQ2603W060"
    / "SQ2603W060 OFFER FOR SPARE FOR UTCL BELA CEMENT WORKS.pdf"
)
Q2501N005 = (
    ROOT / "Quotation PDFs" / "raw" / "Q2501N005"
    / "OFFER#Q2501N005_DANIELI COROUS _Techno commercial.pdf"
)
AMADAS = (
    ROOT / "Quotation PDFs" / "raw" / "Q24S10070"
    / "Q24S10070R1_AMADAS_IOCLPARADEEP_Commercial.pdf"
)


@unittest.skipUnless(SQ2507E254.exists(), "sample corpus not present")
class TestSQ2507E254(unittest.TestCase):
    """3-row merged header (banner + SR./NO. + UNIT PRICE/[INR]), single
    item with a dense multi-line description containing 'POWER SUPPLY: 230
    VAC, 50 Hz' - exactly the bug-class text from the plan's Context
    section. Also confirms the commercial-terms table on the next page is
    NOT picked up as a second BOQ region."""

    @classmethod
    def setUpClass(cls):
        cls.doc = pymupdf.open(SQ2507E254)
        cls.tables = extract_document_tables(cls.doc)

    @classmethod
    def tearDownClass(cls):
        cls.doc.close()

    def test_exactly_one_boq_region(self):
        # Commercial-terms (Price Basis/GST) table must be rejected.
        self.assertEqual(len(self.tables), 1)

    def test_single_row_extracted(self):
        self.assertEqual(len(self.tables[0].rows), 1)

    def test_item_number(self):
        row = self.tables[0].rows[0]
        self.assertEqual(row.text("item_no"), "1")

    def test_price_values(self):
        row = self.tables[0].rows[0]
        up = parse_price(row.text("unit_price"))
        tp = parse_price(row.text("total_price"))
        self.assertEqual(up.value, 975_000.0)
        self.assertEqual(tp.value, 975_000.0)

    def test_power_supply_text_did_not_corrupt_price(self):
        row = self.tables[0].rows[0]
        desc = row.text("description")
        self.assertIn("POWER SUPPLY", desc.upper())
        # The whole point: this line must be in description, not smuggled
        # into a price field as e.g. 23050 (v1's actual failure mode).
        up = parse_price(row.text("unit_price")).value
        self.assertNotEqual(up, 23050)

    def test_description_reads_in_order(self):
        # Regression guard for the line-order bug found during development
        # (LogicalRow.text() used to flatten all lines and sort by x0,
        # scrambling reading order).
        desc = row = self.tables[0].rows[0].text("description")
        self.assertTrue(desc.startswith("MODEL: ULTRAMAT 23") or "MODEL: ULTRAMAT 23" in desc.splitlines()[0:2])

    def test_raw_row_text_contains_price_verbatim(self):
        row = self.tables[0].rows[0]
        self.assertIn(row.text("unit_price"), row.raw_row_text())


@unittest.skipUnless(SQ2603W060.exists(), "sample corpus not present")
class TestSQ2603W060(unittest.TestCase):
    """4-item spares list, no item-number column at all in this template.
    Row 2 ('SIZE: 6/4 MM') is the real-corpus instance of the exact
    'Size-6x4' -> -64 bug pattern from the plan's Context section."""

    @classmethod
    def setUpClass(cls):
        cls.doc = pymupdf.open(SQ2603W060)
        cls.tables = extract_document_tables(cls.doc)

    @classmethod
    def tearDownClass(cls):
        cls.doc.close()

    def test_four_rows_extracted(self):
        self.assertEqual(len(self.tables[0].rows), 4)

    def test_size_fragment_did_not_corrupt_price(self):
        rows = self.tables[0].rows
        size_row = next(r for r in rows if "SIZE: 6/4 MM" in r.text("description"))
        up = parse_price(size_row.text("unit_price")).value
        tp = parse_price(size_row.text("total_price")).value
        self.assertGreater(up, 0)
        self.assertGreater(tp, 0)
        self.assertNotEqual(up, -64)
        self.assertNotEqual(tp, -64)

    def test_all_prices_positive(self):
        for row in self.tables[0].rows:
            for f in ("unit_price", "total_price"):
                v = parse_price(row.text(f)).value
                if v is not None:
                    self.assertGreater(v, 0, f"{f} in row {row.text('description')[:40]!r}")

    def test_arithmetic_consistent(self):
        from boq_coords.money import arithmetic_ok, parse_quantity
        for row in self.tables[0].rows:
            q = parse_quantity(row.text("quantity")).value
            up = parse_price(row.text("unit_price")).value
            tp = parse_price(row.text("total_price")).value
            self.assertTrue(arithmetic_ok(q, up, tp), row.text("description")[:40])


SQ2509N216 = (
    ROOT / "Quotation PDFs" / "raw" / "SQ2509N216" / "OFFER-SQ2509N216-ACC LAKHERI-SERVICE.pdf"
)


@unittest.skipUnless(SQ2509N216.exists(), "sample corpus not present")
class TestSQ2509N216(unittest.TestCase):
    """2-item table with vertically-centred item numbers (item 1's '1'
    sits on the SECOND line of its own two-line description heading, not
    the first) and no larger gap between the two dense multi-line
    description blocks for the gap heuristic to key off. Found as a real
    bug during development: both items collapsed into one row with
    item_no '1 2', and separately, PART B's commercial-terms page leaked
    into the table as a garbage row via the unheaded-continuation path."""

    @classmethod
    def setUpClass(cls):
        cls.doc = pymupdf.open(SQ2509N216)
        cls.tables = extract_document_tables(cls.doc)

    @classmethod
    def tearDownClass(cls):
        cls.doc.close()

    def test_exactly_two_rows(self):
        # Not 1 (collapsed) and not 3+ (PART B leaked in as a garbage row).
        self.assertEqual(len(self.tables[0].rows), 2)

    def test_distinct_item_numbers(self):
        item_nos = [r.text("item_no") for r in self.tables[0].rows]
        self.assertEqual(item_nos, ["1", "2"])

    def test_no_leading_description_line_lost(self):
        row1 = self.tables[0].rows[0]
        self.assertIn("SERVICE CHARGES", row1.text("description"))

    def test_prices(self):
        rows = self.tables[0].rows
        self.assertEqual(parse_price(rows[0].text("unit_price")).value, 36_750.0)
        self.assertEqual(parse_price(rows[1].text("unit_price")).value, 50_000.0)


Q25W10166 = (
    ROOT / "Quotation PDFs" / "raw" / "Q25W10166" / "Q25W10166_Tkis_PP_Petronet_IOCL Gujarat.pdf"
)


@unittest.skipUnless(Q25W10166.exists(), "sample corpus not present")
class TestQ25W10166(unittest.TestCase):
    """25-row SUMMARY OF PRICES table with NO ruling lines at all - the
    real document that exposed a genuine gap in the original design: the
    plan called for an unruled/borderless-table fallback (locate.py
    _find_unruled_region), but nothing had ever wired it into
    find_boq_regions(); this table scored zero rows until it was built.
    Also: item numbers here are 'A'/'B' letters mixed with plain integers
    (1-25), and per-row prices are printed in LAKHS with no per-row unit -
    only the document's own printed grand total ('2615 Lakhs') resolves
    that scale, which this extractor does not attempt (documented known
    limitation, not asserted here)."""

    @classmethod
    def setUpClass(cls):
        cls.doc = pymupdf.open(Q25W10166)
        cls.tables = extract_document_tables(cls.doc)

    @classmethod
    def tearDownClass(cls):
        cls.doc.close()

    def test_unruled_table_is_found_at_all(self):
        # Before the unruled fallback existed, this was an empty list.
        self.assertTrue(self.tables)
        total_rows = sum(len(t.rows) for t in self.tables)
        self.assertGreater(total_rows, 15)

    def test_plain_integer_items_extracted_with_correct_raw_price(self):
        rows = [r for t in self.tables for r in t.rows]
        row3 = next(r for r in rows if r.text("item_no") == "3")
        self.assertIn("102-AT-11301", row3.text("description"))
        # Printed cell is '199.00' (Lakhs); extractor reads it literally -
        # the Lakhs scale is a known, documented limitation, not fixed here.
        self.assertEqual(parse_price(row3.text("unit_price")).value, 199.0)


_technoquest_matches = list((ROOT / "Quotation PDFs" / "raw" / "2526G010").glob("*.pdf")) \
    if (ROOT / "Quotation PDFs" / "raw" / "2526G010").exists() else []
Technoquest2526G010 = _technoquest_matches[0] if _technoquest_matches else Path("__missing__")


@unittest.skipUnless(Technoquest2526G010.exists(), "sample corpus not present")
class TestDuplicatePriceHeader(unittest.TestCase):
    """The source PDF's own header literally prints 'TOTAL PRICE (INR)'
    twice (columns 4 and 7 of the table), not 'UNIT PRICE' / 'TOTAL PRICE'
    - a real authoring error, not a classifier bug. Before the fix, both
    columns built a ColumnBand named 'total_price', which made
    _classify_lines concatenate both money cells into one string
    ("Rs 12,50,000 Rs 12,50,000"), fail the single-value money grammar,
    and silently revert the whole price into description - a real price
    visible in raw_row_text but absent from every priced field."""

    @classmethod
    def setUpClass(cls):
        cls.doc = pymupdf.open(Technoquest2526G010)
        cls.tables = extract_document_tables(cls.doc)

    @classmethod
    def tearDownClass(cls):
        cls.doc.close()

    def test_item_1_price_not_dropped_to_description(self):
        rows = [r for t in self.tables for r in t.rows]
        row1 = next(r for r in rows if r.text("item_no") == "1")
        up = parse_price(row1.text("unit_price")).value
        tp = parse_price(row1.text("total_price")).value
        self.assertEqual(up, 1_250_000.0)
        self.assertEqual(tp, 1_250_000.0)
        self.assertNotIn("Rs 12,50,000", row1.text("description"))


MULTI_NUMBER_RX = re.compile(r"\d[\d,]{2,}\s+\d[\d,]{2,}")


@unittest.skipUnless(Q2501N005.exists(), "sample corpus not present")
class TestBundledSubItems(unittest.TestCase):
    """Real-corpus instance of the row-segmentation bug reported by the
    user: item 8's own row is followed by three UNNUMBERED sub-items
    (PORTA CABIN / DATALOGGER / DUST MONITOR ANALYZER), each a distinct,
    independently-priced product with its own '1 SET <price> <price>'
    line. Before the fix, boundary detection had no item-number anchor to
    split on between them, so all three were swept into one row and their
    three separate prices were space-joined into one unparseable string
    per price column (e.g. "25,64,400 1,56,200 13,50,800")."""

    @classmethod
    def setUpClass(cls):
        cls.doc = pymupdf.open(Q2501N005)
        cls.tables = extract_document_tables(cls.doc)
        cls.rows = [r for t in cls.tables for r in t.rows]

    @classmethod
    def tearDownClass(cls):
        cls.doc.close()

    def test_bundled_subitems_get_independent_numeric_prices(self):
        expected = {
            "DATALOGGER": 156_200.0,
            "DUST MONITOR": 1_350_800.0,
        }
        for needle, price in expected.items():
            matches = [r for r in self.rows if needle in r.text("description").upper()]
            self.assertTrue(matches, needle)
            for row in matches:
                up = parse_price(row.text("unit_price")).value
                tp = parse_price(row.text("total_price")).value
                self.assertEqual(up, price, needle)
                self.assertEqual(tp, price, needle)

    def test_no_row_has_a_concatenated_multi_number_price_cell(self):
        for row in self.rows:
            for field in ("unit_price", "total_price"):
                text = row.text(field)
                self.assertIsNone(MULTI_NUMBER_RX.search(text), (field, text))


def _make_price_line(yc: float, qty_unit: str, unit_price: str, total_price: str) -> "Line":
    """Build one physical Line the way real PDF text would land, for a
    synthetic segment_rows() test with no PDF involved. Each field's tokens
    are placed inside that field's ColumnBand x-range (see BANDS below) so
    _band_for_word buckets them correctly."""
    from boq_coords.geometry import Line

    words = []
    for band_x0, text in ((0, qty_unit), (100, unit_price), (150, total_price)):
        wx = band_x0
        for token in text.split():
            words.append(Word(x0=wx, y0=yc - 1, x1=wx + 5, y1=yc + 1, text=token))
            wx += 6
    return Line(words=words)


class TestSegmentRowsSplitsBundledSubItems(unittest.TestCase):
    """Synthetic, no-PDF unit test for banded._split_self_contained_subitems
    via segment_rows() directly - mirrors the real Q2501N005 pattern (3
    physical lines, each a complete self-contained item, no item-number
    column, only the region's two outer-border rulings) plus a negative
    case guarding that an ordinary single-item row is never split."""

    BANDS = [
        ColumnBand(field="quantity", x0=-1, x1=45),
        ColumnBand(field="unit_price", x0=95, x1=145),
        ColumnBand(field="total_price", x0=145, x1=195),
    ]

    def test_three_bundled_lines_split_into_three_rows(self):
        lines = [
            _make_price_line(100.0, "1 SET", "25,64,400", "25,64,400"),
            _make_price_line(120.0, "1 SET", "1,56,200", "1,56,200"),
            _make_price_line(140.0, "1 SET", "13,50,800", "13,50,800"),
        ]
        words = [w for ln in lines for w in ln.words]
        rows = segment_rows(words, self.BANDS, ruling_ys=[80.0, 200.0])

        self.assertEqual(len(rows), 3)
        prices = sorted(parse_price(r.text("total_price")).value for r in rows)
        self.assertEqual(prices, [156_200.0, 1_350_800.0, 2_564_400.0])

    def test_single_item_with_one_price_line_is_not_split(self):
        # Ordinary case: one real price line only - must never be split,
        # even though it's the only physical line in the region.
        lines = [_make_price_line(100.0, "1 SET", "50,000", "50,000")]
        words = [w for ln in lines for w in ln.words]
        rows = segment_rows(words, self.BANDS, ruling_ys=[80.0, 200.0])

        self.assertEqual(len(rows), 1)
        self.assertEqual(parse_price(rows[0].text("total_price")).value, 50_000.0)


class TestExtractHeading(unittest.TestCase):
    """product_name derivation. Two real corpus failure modes are locked in
    here: a heading that collected NOTHING because the very first line was
    already a stop line (355/2227 rows), and a heading that collected
    EVERYTHING because the source's bullet is a Wingdings/Symbol
    Private-Use-Area glyph (U+F0B7) rather than a real "•" (max 728 chars,
    including letterhead boilerplate)."""

    def test_stop_pattern_on_first_line_still_yields_a_heading(self):
        text = ("with welded isolation valve, MOC: PVDF\n"
                "RF flange, MOC: Hastelloy\ntube:")
        heading = extract_heading(text)
        self.assertTrue(heading.strip(), "accessory/spec-only item lost its heading")

    def test_private_use_area_bullet_stops_the_heading(self):
        text = "DUST MONITOR ANALYZER\n spec one\n spec two"
        self.assertEqual(extract_heading(text), "DUST MONITOR ANALYZER")

    def test_heading_is_word_capped_when_no_stop_pattern_matches(self):
        text = "\n".join(f"WORD{i} ALPHA BETA" for i in range(40))
        heading = extract_heading(text)
        self.assertLessEqual(len(heading.split()), 16)  # 15 words + "..."

    def test_ordinary_headings_are_unchanged(self):
        cases = [
            ("GAS ANALYSER SYSTEM FOR STANDALONE NH3 SYSTEM\nService: PROCESS GAS...",
             "GAS ANALYSER SYSTEM FOR STANDALONE NH3 SYSTEM"),
            ("GAS ANALYSER SYSYTEM FOR MEASUREMENT AT KILN INLET\nWith Closed Loop Probe...",
             "GAS ANALYSER SYSYTEM FOR MEASUREMENT AT KILN INLET"),
            ("DUST MONITOR ANALYZER\nPRINCIPLE: MEASURING THE OPTICAL...",
             "DUST MONITOR ANALYZER"),
        ]
        for text, want in cases:
            self.assertEqual(extract_heading(text), want)


class TestItemHierarchy(unittest.TestCase):
    """item_no stays TEXT as printed; the hierarchy it encodes is exposed
    explicitly rather than left for a consumer to re-parse (and to coerce
    "1.1" into the float 1.1)."""

    def test_dotted_decimal_and_letter_markers(self):
        self.assertEqual(derive_item_hierarchy("1"), ("", 1))
        self.assertEqual(derive_item_hierarchy("1.1"), ("1", 2))
        self.assertEqual(derive_item_hierarchy("1.1.2"), ("1.1", 3))
        self.assertEqual(derive_item_hierarchy("4a"), ("4", 2))
        self.assertEqual(derive_item_hierarchy("12 B"), ("12", 2))

    def test_trailing_punctuation_and_blanks(self):
        self.assertEqual(derive_item_hierarchy("2."), ("", 1))
        self.assertEqual(derive_item_hierarchy("3)"), ("", 1))
        self.assertEqual(derive_item_hierarchy(""), ("", 0))
        self.assertEqual(derive_item_hierarchy("   "), ("", 0))


@unittest.skipUnless(AMADAS.exists(), "sample corpus not present")
class TestAmadasMakeListNotAbsorbed(unittest.TestCase):
    """Page 7 of this document is a separate "MAKE LIST" table (SR NO |
    ITEM DESCRIPTION | MAKE | Total | Unit). find_boq_regions correctly
    declines it as a BOQ region - its bare "Total" is a quantity total,
    not a price - but the unheaded-continuation pass used to swallow the
    whole page into the PREVIOUS page's table, clipping every description's
    left edge and collapsing ~20 source rows into one row whose
    product_name was the table header plus several merged line items."""

    @classmethod
    def setUpClass(cls):
        cls.doc = pymupdf.open(AMADAS)
        cls.rows = [r for t in extract_document_tables(cls.doc) for r in t.rows]

    @classmethod
    def tearDownClass(cls):
        cls.doc.close()

    def test_no_row_contains_the_make_list_table_header(self):
        for row in self.rows:
            desc = row.text("description").upper()
            self.assertNotIn("MAKE LIST", desc)
            self.assertNotIn("ITEM DESCRIPTION", desc)

    def test_priced_rows_still_extracted(self):
        # The guard must not cost us the real priced table on pages 4/6.
        priced = [r for r in self.rows if parse_price(r.text("unit_price")).value]
        self.assertGreaterEqual(len(priced), 5)


if __name__ == "__main__":
    unittest.main()
