"""
Tests for knowledge_bank/build_analysis_export.py's pure helpers.
Synthetic values only - no quotation or customer data.

Run: venv\\Scripts\\python.exe -m unittest tests.test_analysis_export -v
"""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "knowledge_bank"))

from build_analysis_export import (  # noqa: E402
    classify_lines,
    clean_text,
    doc_id_for,
    expected_finalization,
    fiscal_year,
    include_in_total,
    match_quality,
    parse_days,
    parse_document_date,
    parse_percent,
    parse_revision,
    standardize_regions,
    standardize_state,
    standardize_unit,
)


class ValueHelpersTest(unittest.TestCase):

    def test_placeholders_become_blank(self):
        for raw in ("Unknown", "UNKNOWN", "NA", "N/A", " - ", ""):
            self.assertEqual(clean_text(raw), "")
        self.assertEqual(clean_text("  Steel   plant "), "Steel plant")
        self.assertEqual(clean_text("NONE"), "NONE")  # a real code (price_basis), not a placeholder

    def test_revision_suffix(self):
        self.assertEqual(parse_revision("Q25N10072R2"), ("Q25N10072", 2))
        self.assertEqual(parse_revision("SQ2502E050R1"), ("SQ2502E050", 1))
        self.assertEqual(parse_revision("Q25N10072_R12"), ("Q25N10072", 12))
        self.assertEqual(parse_revision("SQ2502E050"), ("SQ2502E050", 0))
        self.assertEqual(parse_revision("R3_Commercial"), ("R3_Commercial", 0))
        self.assertEqual(parse_revision("Q24X10030R"), ("Q24X10030R", 0))

    def test_document_dates_are_day_first(self):
        self.assertEqual(parse_document_date("02.04.2025"), date(2025, 4, 2))
        self.assertEqual(parse_document_date("2.4.2025"), date(2025, 4, 2))
        self.assertEqual(parse_document_date("21-02-2025"), date(2025, 2, 21))
        self.assertEqual(parse_document_date("21/02/2025"), date(2025, 2, 21))
        self.assertEqual(parse_document_date("21-02/2025"), date(2025, 2, 21))
        self.assertEqual(parse_document_date("21.02.25"), date(2025, 2, 21))
        self.assertEqual(parse_document_date("12 March 2025"), date(2025, 3, 12))
        self.assertEqual(parse_document_date("12th Sept 2025"), date(2025, 9, 12))

    def test_non_dates_are_rejected(self):
        for raw in ("24-25/1264", "31.02.2025", "", "EMAIL"):
            self.assertIsNone(parse_document_date(raw))

    def test_indian_fiscal_year(self):
        self.assertEqual(fiscal_year(date(2025, 6, 23)), "FY2025-26")
        self.assertEqual(fiscal_year(date(2026, 3, 31)), "FY2025-26")
        self.assertEqual(fiscal_year(date(2026, 4, 1)), "FY2026-27")
        self.assertEqual(fiscal_year(None), "")

    def test_units(self):
        self.assertEqual(standardize_unit("EACH"), "NOS")
        self.assertEqual(standardize_unit("MANDAYS"), "MAN-DAY")
        self.assertEqual(standardize_unit("MDY"), "MAN-DAY")
        self.assertEqual(standardize_unit("VISITS"), "VISIT")
        self.assertEqual(standardize_unit(""), "")

    def test_state_and_regions(self):
        self.assertEqual(standardize_state("Maharashtra (IN)"), "Maharashtra")
        self.assertEqual(standardize_state("Chattisgarh (IN)"), "Chhattisgarh")
        self.assertEqual(standardize_regions("West 1, North,West 1"), "North|West 1")

    def test_numbers_from_text(self):
        self.assertEqual(parse_percent("75%"), 75.0)
        self.assertIsNone(parse_percent("Unknown"))
        self.assertEqual(parse_days("111.5 days"), 111.5)
        self.assertEqual(parse_days("1 day"), 1.0)
        self.assertIsNone(parse_days("NA"))

    def test_expected_finalization(self):
        self.assertEqual(expected_finalization("September", "2026"), "2026-09")
        self.assertEqual(expected_finalization("Unknown", "2026"), "")
        self.assertEqual(expected_finalization("March", "Unknown"), "")

    def test_match_quality(self):
        self.assertEqual(match_quality("RFQ_MATCH", "12"), "MATCHED")
        self.assertEqual(match_quality("LOW", "12"), "REVIEW")
        self.assertEqual(match_quality("RFQ_MATCH", ""), "UNMATCHED")

    def test_doc_id_is_stable_and_short(self):
        self.assertEqual(doc_id_for("a/b.txt"), doc_id_for("a/b.txt"))
        self.assertNotEqual(doc_id_for("a/b.txt"), doc_id_for("a/c.txt"))
        self.assertRegex(doc_id_for("a/b.txt"), r"^D[0-9A-F]{8}$")


def classify(rows):
    """rows: (level, has_qty, price_status, name, item_no, parent_item_no)."""
    columns = list(zip(*rows))
    return classify_lines(*[list(c) for c in columns])


class HierarchyTest(unittest.TestCase):

    def test_line_types(self):
        types, _, _ = classify([
            (0, False, "MISSING", "SECTION A - ANALYSERS", "", ""),
            (1, True, "NUMERIC", "Analyser", "1", ""),
            (0, True, "NUMERIC", "Installation", "", ""),
            (2, True, "NUMERIC", "Probe", "1.1", "1"),
            (0, False, "NUMERIC", "Grand Total", "", ""),
        ])
        self.assertEqual(types, ["HEADER", "LINE", "LINE", "SUB_LINE", "TOTAL"])

    def test_sub_item_links_to_parent_by_item_no_not_position(self):
        # 5A/5B follow item 4 but belong to an unprinted item 5.
        _, parents, bundles = classify([
            (1, True, "NUMERIC", "x", "4", ""),
            (2, True, "NUMERIC", "x", "5A", "5"),
            (2, True, "NUMERIC", "x", "5B", "5"),
            (1, True, "NUMERIC", "x", "6", ""),
            (2, True, "NUMERIC", "x", "6.1", "6"),
        ])
        self.assertEqual(parents, [None, None, None, None, 4])
        self.assertEqual(bundles, [1, 2, 2, 4, 4])

    def test_parent_is_the_nearest_earlier_match(self):
        _, parents, _ = classify([
            (1, True, "NUMERIC", "x", "1", ""),
            (1, True, "NUMERIC", "x", "1.", ""),   # schedule restart, trailing dot
            (2, True, "NUMERIC", "x", "1.1", "1"),
        ])
        self.assertEqual(parents, [None, None, 2])

    def test_priced_parent_excludes_children_from_total(self):
        flags = include_in_total(["LINE", "SUB_LINE", "SUB_LINE"], [None, 1, 1], [True, True, True])
        self.assertEqual(flags, ["Y", "N", "N"])

    def test_unpriced_parent_lets_children_count(self):
        flags = include_in_total(["LINE", "SUB_LINE", "SUB_LINE"], [None, 1, 1], [False, True, True])
        self.assertEqual(flags, ["N", "Y", "Y"])

    def test_total_rows_never_count(self):
        self.assertEqual(include_in_total(["LINE", "TOTAL"], [None, None], [True, True]), ["Y", "N"])


if __name__ == "__main__":
    unittest.main()
