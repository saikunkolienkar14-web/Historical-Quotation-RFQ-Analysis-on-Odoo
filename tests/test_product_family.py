"""
Tests for knowledge_bank/product_family.py. Synthetic strings using
public product-line/category names only - no quotation or customer data.

Run: venv\\Scripts\\python.exe -m unittest tests.test_product_family -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "knowledge_bank"))

from product_family import (  # noqa: E402
    apply_product_family_columns,
    classify_product_family,
    load_description_rules,
    load_model_rules,
)


class ModelRuleTest(unittest.TestCase):

    def test_prefix_match(self):
        rules = [("ULTRAMAT", "GAS ANALYZER")]
        self.assertEqual(
            classify_product_family("", "ULTRAMAT 23", rules, []),
            ("GAS ANALYZER", "MODEL_RULE"),
        )

    def test_no_match_is_blank(self):
        rules = [("ULTRAMAT", "GAS ANALYZER")]
        self.assertEqual(classify_product_family("", "OXYMAT 61", rules, []), ("", ""))

    def test_multi_model_checked_part_by_part(self):
        rules = [("MICHELL", "MOISTURE ANALYZER")]
        self.assertEqual(
            classify_product_family("", "AMETEK|MICHELL", rules, []),
            ("MOISTURE ANALYZER", "MODEL_RULE"),
        )

    def test_first_matching_rule_wins(self):
        rules = [("ULTRAMAT 23", "SPECIFIC"), ("ULTRAMAT", "GENERIC")]
        self.assertEqual(
            classify_product_family("", "ULTRAMAT 23", rules, [])[0], "SPECIFIC"
        )


class DescriptionRuleTest(unittest.TestCase):

    def test_phrase_match(self):
        rules = load_description_rules_from_pairs([("GAS CHROMATOGRAPH", "GAS CHROMATOGRAPH")])
        self.assertEqual(
            classify_product_family("Gas Chromatograph with SHS", "", [], rules),
            ("GAS CHROMATOGRAPH", "DESCRIPTION_RULE"),
        )

    def test_case_insensitive(self):
        rules = load_description_rules_from_pairs([("sample probe", "SAMPLING SYSTEM")])
        self.assertEqual(
            classify_product_family("SAMPLE PROBE (RIL Standard)", "", [], rules)[0],
            "SAMPLING SYSTEM",
        )

    def test_more_specific_rule_must_precede_generic_one(self):
        # "OXYGEN ANALYSER" and "GAS ANALYSER" both appear in the text -
        # ordering the specific rule first is what makes it win.
        rules = load_description_rules_from_pairs([
            ("OXYGEN ANALY(S|Z)ER", "OXYGEN ANALYZER"),
            ("GAS ANALY(S|Z)ER", "GAS ANALYZER"),
        ])
        text = "Oxygen Analyser, a type of Gas Analyser"
        self.assertEqual(classify_product_family(text, "", [], rules)[0], "OXYGEN ANALYZER")

        # Reversed order: the generic rule now wins, since it's checked
        # first and the text also matches it - demonstrates order
        # dependence rather than any smarter disambiguation.
        reversed_rules = load_description_rules_from_pairs([
            ("GAS ANALY(S|Z)ER", "GAS ANALYZER"),
            ("OXYGEN ANALY(S|Z)ER", "OXYGEN ANALYZER"),
        ])
        self.assertEqual(classify_product_family(text, "", [], reversed_rules)[0], "GAS ANALYZER")

    def test_no_match_is_blank(self):
        rules = load_description_rules_from_pairs([("GAS CHROMATOGRAPH", "GAS CHROMATOGRAPH")])
        self.assertEqual(classify_product_family("Solenoid Valve 2/2 Way", "", [], rules), ("", ""))

    def test_model_rule_checked_before_description_rule(self):
        model_rules = [("ULTRAMAT", "GAS ANALYZER (FROM MODEL)")]
        desc_rules = load_description_rules_from_pairs([("GAS", "GENERIC (FROM DESCRIPTION)")])
        result = classify_product_family("Gas Analyser", "ULTRAMAT 23", model_rules, desc_rules)
        self.assertEqual(result, ("GAS ANALYZER (FROM MODEL)", "MODEL_RULE"))


class LoadRulesFromFileTest(unittest.TestCase):

    def test_missing_file_is_empty(self):
        self.assertEqual(load_model_rules(Path(tempfile.gettempdir()) / "no_such_rules.csv"), [])
        self.assertEqual(load_description_rules(Path(tempfile.gettempdir()) / "no_such_rules.csv"), [])

    def test_model_rules_preserve_order_and_uppercase(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model_rules.csv"
            path.write_text(
                "pattern,family,note\nultramat,gas analyzer,test\noxymat,oxygen analyzer,test\n",
                encoding="utf-8",
            )
            rules = load_model_rules(path)
            self.assertEqual(rules, [("ULTRAMAT", "GAS ANALYZER"), ("OXYMAT", "OXYGEN ANALYZER")])

    def test_description_rules_skip_invalid_regex(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "desc_rules.csv"
            path.write_text(
                "pattern,family,note\n(unclosed,BAD,\nGAS CHROMATOGRAPH,GOOD,\n",
                encoding="utf-8",
            )
            rules = load_description_rules(path)
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0][1], "GOOD")


class ApplyColumnsTest(unittest.TestCase):

    def test_applies_in_place_and_leaves_blank_rows_blank(self):
        model_rules = [("ULTRAMAT", "GAS ANALYZER")]
        desc_rules = load_description_rules_from_pairs([("SAMPLE PROBE", "SAMPLING SYSTEM")])
        rows = [
            {"description": "", "model_canonical": "ULTRAMAT 23"},
            {"description": "Sample Probe (RIL Standard)", "model_canonical": ""},
            {"description": "", "model_canonical": ""},
        ]
        apply_product_family_columns(rows, model_rules, desc_rules)
        self.assertEqual(rows[0]["product_family"], "GAS ANALYZER")
        self.assertEqual(rows[0]["product_family_basis"], "MODEL_RULE")
        self.assertEqual(rows[1]["product_family"], "SAMPLING SYSTEM")
        self.assertEqual(rows[1]["product_family_basis"], "DESCRIPTION_RULE")
        self.assertEqual(rows[2]["product_family"], "")
        self.assertEqual(rows[2]["product_family_basis"], "")


def load_description_rules_from_pairs(pairs):
    """Test helper: build description rules without going through a CSV
    file, using the same compile step load_description_rules uses."""
    import re
    return [(re.compile(pattern, re.IGNORECASE), family) for pattern, family in pairs]


if __name__ == "__main__":
    unittest.main()
