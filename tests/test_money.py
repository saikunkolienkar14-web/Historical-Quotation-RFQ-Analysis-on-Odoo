"""
Tests for boq_coords/money.py, built from the exact failure cases that
motivated this whole project (see plan §Context). Each case is a full-line
string fed through parse_price(); the anchored grammar must refuse to find
a price inside it at all, since the real fix is that these tokens never
belong to a price band in the first place. That upstream guarantee lives in
banded.py; this test only proves the number parser itself can't be tricked
if the guarantee ever fails.

stdlib-only (unittest), matching the rest of the project (no pytest installed;
not adding it as a new dependency for one test file).

Run: venv\\Scripts\\python.exe -m unittest tests.test_money -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boq_coords.money import (
    arithmetic_ok,
    parse_price,
    parse_quantity,
    price_in_bounds,
)


class TestMoney(unittest.TestCase):

    # --- The three bugs cited in the plan's Context section ---

    def test_capacity_qty_fragment_is_not_a_price(self):
        # v1 parse_number: "CAPACITY) QTY-2" -> -2
        self.assertIsNone(parse_price("CAPACITY) QTY-2").value)

    def test_size_fragment_is_not_a_price(self):
        # v1 parse_number: "Size-6x4 with real time data" -> -64
        self.assertIsNone(parse_price("Size-6x4 with real time data").value)

    def test_power_supply_fragment_is_not_a_price(self):
        # v1 parse_number: "Power Supply: 230 VAC, 50 Hz" -> 23050
        self.assertIsNone(parse_price("Power Supply: 230 VAC, 50 Hz").value)

    def test_no_minus_sign_ever_parses(self):
        for s in ["-2", "-64", "ANNEXURE - 1", "Cement Line -1", "and Line 2"]:
            v = parse_price(s).value
            self.assertTrue(v is None or v >= 0, s)

    def test_address_fragment_is_not_a_price(self):
        self.assertIsNone(parse_price("Satra Plaza, Office No: 506, Sector 16 D,").value)
        self.assertIsNone(parse_price(
            "Palm Beach Road, Vashi, Navi Mumbai - 400703, Maharashtra, INDIA").value)

    # --- Values that MUST parse correctly ---

    def test_indian_grouping(self):
        p = parse_price("45,00,000/-")
        self.assertEqual(p.value, 4_500_000)
        self.assertEqual(p.currency, "INR")

    def test_indian_grouping_with_decimals(self):
        p = parse_price("9,75,000.00")
        self.assertEqual(p.value, 975_000.0)

    def test_western_grouping(self):
        p = parse_price("4,500,000")
        self.assertEqual(p.value, 4_500_000)

    def test_rs_prefix(self):
        p = parse_price("Rs 65,00,000")
        self.assertEqual(p.value, 6_500_000)

    def test_lakhs_and_crores(self):
        self.assertEqual(parse_price("93 Lakhs").value, 9_300_000)
        self.assertEqual(parse_price("4 lakhs").value, 400_000)
        self.assertEqual(parse_price("1 Lakh").value, 100_000)
        self.assertEqual(parse_price("107 Lakhs").value, 10_700_000)

    def test_placeholders(self):
        for s in ["QUOTED", "Inclusive", "Existing", "Not used in a system", "TBD"]:
            p = parse_price(s)
            self.assertIsNone(p.value, s)
            self.assertTrue(p.is_placeholder, s)

    def test_bare_number_still_parses_if_isolated(self):
        # A pure token like "93" (already isolated to a price band) does parse -
        # protection against prose is banded.py's job (line-content gate), not
        # the grammar's. This documents that boundary.
        self.assertEqual(parse_price("93").value, 93)

    def test_currency_detection(self):
        self.assertEqual(parse_price("$1,500").currency, "USD")
        self.assertEqual(parse_price("SAR 45,000").currency, "SAR")
        self.assertEqual(parse_price("45,00,000/-").currency, "INR")

    def test_malformed_grouping_rejected(self):
        # "1,00,00" is not valid Indian OR Western grouping - spill detector.
        self.assertIsNone(parse_price("1,00,00").value)

    # --- Plausibility bounds (v1 has none of these for price) ---

    def test_price_bounds(self):
        self.assertFalse(price_in_bounds(500, is_unit=False))   # below 1000
        self.assertTrue(price_in_bounds(1000, is_unit=False))
        self.assertFalse(price_in_bounds(-2, is_unit=False))
        self.assertFalse(price_in_bounds(6_000_000_000, is_unit=False))
        self.assertTrue(price_in_bounds(None, is_unit=False))

    # --- Quantity: bare number with no unit is not a quantity ---

    def test_quantity_requires_unit(self):
        q = parse_quantity("450 Meters")
        self.assertEqual(q.value, 450)
        self.assertEqual(q.unit, "Meters")
        q2 = parse_quantity("2")
        self.assertIsNone(q2.value)  # bare number, no unit word

    def test_quantity_units_from_vocab(self):
        for s in ["1 Set", "2 Nos", "5 Sets", "1 Lot", "10 Man-days"]:
            q = parse_quantity(s)
            self.assertIsNotNone(q.value, s)

    # --- Arithmetic cross-check ---

    def test_arithmetic_ok(self):
        self.assertTrue(arithmetic_ok(2, 9_200_000, 18_400_000))
        self.assertFalse(arithmetic_ok(2, 9_200_000, 999_999))
        self.assertTrue(arithmetic_ok(None, None, None))  # nothing to check


class TestHeaderClassification(unittest.TestCase):
    """Regression guard for a real corpus bug: 'Item Description' was
    misclassified as item_no (the bare alias 'item' is a prefix of 'item
    description') because field-priority order beat alias specificity.
    That caused a genuinely priced table (Q24W10129R4 commercial offer,
    86,10,000/- line items) to be rejected outright as having no BOQ
    table, since 'description' never made it into the matched fields."""

    def test_item_description_is_description_not_item_no(self):
        from boq_coords.vocab import classify_header_word
        self.assertEqual(classify_header_word("Item Description"), "description")

    def test_bare_item_still_classifies_as_item_no(self):
        from boq_coords.vocab import classify_header_word
        self.assertEqual(classify_header_word("Item"), "item_no")

    def test_total_price_wins_over_unit_price_on_tie(self):
        from boq_coords.vocab import classify_header_word
        self.assertEqual(classify_header_word("Total Price"), "total_price")


if __name__ == "__main__":
    unittest.main()
