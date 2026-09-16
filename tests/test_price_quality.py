"""
Tests for build_knowledge_bank_coords.py's price_quality /
arithmetic_check rollup (2026-09-16 addition, coords side only).

Run: venv\\Scripts\\python.exe -m unittest tests.test_price_quality -v
"""
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "knowledge_bank"))

from build_knowledge_bank_coords import (  # noqa: E402
    check_arithmetic,
    classify_price_quality,
)


class ClassifyPriceQualityTest(unittest.TestCase):

    def test_trusted(self):
        self.assertEqual(classify_price_quality("REPORTED", "NUMERIC", ""), "TRUSTED")
        self.assertEqual(classify_price_quality("DERIVED_FROM_TOTAL", "NUMERIC", ""), "TRUSTED")

    def test_flagged_wins_over_everything(self):
        self.assertEqual(
            classify_price_quality("REPORTED", "NUMERIC", "PRICE_OUT_OF_RANGE:unit_price"),
            "FLAGGED",
        )
        self.assertEqual(
            classify_price_quality("NONE", "MISSING", "PRICE_ABSENT;PRICE_CELL_SPANS_MULTIPLE_ROWS"),
            "FLAGGED",
        )
        # A non-PRICE_ validation code must not trigger FLAGGED.
        self.assertNotEqual(
            classify_price_quality("REPORTED", "NUMERIC", "ITEM_NO_MALFORMED"),
            "FLAGGED",
        )

    def test_non_numeric(self):
        self.assertEqual(classify_price_quality("NONE", "QUOTED_SEPARATELY", ""), "NON_NUMERIC")
        self.assertEqual(classify_price_quality("NONE", "INCLUDED", ""), "NON_NUMERIC")

    def test_no_price(self):
        self.assertEqual(classify_price_quality("NONE", "MISSING", ""), "NO_PRICE")


class CheckArithmeticTest(unittest.TestCase):

    def test_ok(self):
        self.assertEqual(check_arithmetic(100.0, 500.0, "5"), "OK")

    def test_mismatch(self):
        self.assertEqual(check_arithmetic(100.0, 5000.0, "5"), "MISMATCH")

    def test_blank_when_any_value_missing(self):
        self.assertEqual(check_arithmetic(None, 500.0, "5"), "")
        self.assertEqual(check_arithmetic(100.0, None, "5"), "")
        self.assertEqual(check_arithmetic(100.0, 500.0, ""), "")


if __name__ == "__main__":
    unittest.main()
