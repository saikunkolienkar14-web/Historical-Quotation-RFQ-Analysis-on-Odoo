"""
Tests for knowledge_bank/suggest_aliases.py's proposal rules - the guards
that keep genuinely different products/vendors from being suggested as
aliases. Synthetic public vendor/product-line names only.

Run: venv\\Scripts\\python.exe -m unittest tests.test_suggest_aliases -v
"""
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "knowledge_bank"))

from suggest_aliases import (  # noqa: E402
    abbreviates,
    is_truncated,
    plural_of,
    suggest,
    variant_markers,
)


class RuleTest(unittest.TestCase):

    def test_variant_markers_separate_numbered_and_roman_variants(self):
        self.assertNotEqual(variant_markers("OXYMAT 61"), variant_markers("OXYMAT 64"))
        self.assertNotEqual(variant_markers("MAXUM II"), variant_markers("MAXUM III"))
        self.assertEqual(variant_markers("ULTRAMAT 23"), variant_markers("ULTRAMAT23"))

    def test_abbreviation(self):
        self.assertTrue(abbreviates("MAXUM ED II", "MAXUM EDITION II"))
        self.assertTrue(abbreviates("FORBES MARSHAL", "FORBES MARSHALL"))
        self.assertFalse(abbreviates("THERMO", "THERMON"))
        self.assertFalse(abbreviates("SMARTPRO 8967EX", "SMARTPRO 8967"))
        self.assertFalse(abbreviates("SITRANS F", "SITRANS FM"))  # single-letter token
        self.assertFalse(abbreviates("MAXUM II", "MAXUM II"))

    def test_plural(self):
        self.assertTrue(plural_of("AIROPTICS", "AIROPTIC"))
        self.assertFalse(plural_of("ABBS", "ABB"))

    def test_truncated(self):
        self.assertTrue(is_truncated("VALMET(SIEMENS"))
        self.assertTrue(is_truncated("SIEMENS [ADAGE"))
        self.assertFalse(is_truncated("SIEMENS (INDIA)"))


class SuggestTest(unittest.TestCase):

    def test_never_proposes_across_variant_numbers(self):
        spellings = ["OXYMAT 61"] * 5 + ["OXYMAT 64"] * 3
        rows, _ = suggest("MODEL", spellings, {}, allow_containment=False)
        self.assertEqual(rows, [])

    def test_less_frequent_points_to_more_frequent_and_chains_resolve(self):
        spellings = ["MICHELL"] * 10 + ["MICHELL INSTRUMENTS"] * 5 + ["MITCHELL INSTRUMENTS"] * 2
        rows, _ = suggest("MAKE", spellings, {}, allow_containment=True)
        proposals = {row["alias"]: row["proposed_canonical"] for row in rows}
        self.assertEqual(proposals["MICHELL INSTRUMENTS"], "MICHELL")
        self.assertEqual(proposals["MITCHELL INSTRUMENTS"], "MICHELL")
        self.assertNotIn("MICHELL", proposals)

    def test_truncated_values_listed_not_proposed(self):
        spellings = ["SIEMENS"] * 10 + ["VALMET(SIEMENS"] * 3
        rows, truncated = suggest("MAKE", spellings, {}, allow_containment=True)
        self.assertEqual(rows, [])
        self.assertEqual([row["alias"] for row in truncated], ["VALMET(SIEMENS"])

    def test_already_aliased_values_skipped(self):
        spellings = ["AIROPTIC"] * 10 + ["AIROPTICS"] * 3
        rows, _ = suggest("MAKE", spellings, {"AIROPTICS": "AIROPTIC"}, allow_containment=True)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
