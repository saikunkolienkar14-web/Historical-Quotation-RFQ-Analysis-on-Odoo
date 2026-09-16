"""
Tests for knowledge_bank/canonicalize.py. Synthetic strings using public
manufacturer/product-line names only - no quotation or customer data.

Run: venv\\Scripts\\python.exe -m unittest tests.test_canonicalize -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "knowledge_bank"))

from canonicalize import (  # noqa: E402
    apply_canonical_columns,
    build_display_map,
    canonicalize_make,
    canonicalize_model,
    collapse_key,
    load_aliases,
    make_parts,
    unit_class,
)


class MakeRulesTest(unittest.TestCase):

    def test_country_and_legal_form_stripped(self):
        for raw in ("SIEMENS AG, GERMANY", "SIEMENS AG GERMANY", "SIEMENS, GERMANY", "SIEMENS AG"):
            self.assertEqual(canonicalize_make(raw, {}, {}), ("SIEMENS", "RULE"))

    def test_unchanged_make_is_cosmetic(self):
        self.assertEqual(canonicalize_make("SIEMENS", {}, {}), ("SIEMENS", "COSMETIC"))

    def test_alternates_split_sorted_joined(self):
        self.assertEqual(
            canonicalize_make("FORBES MARSHALL/ EMERSON/ E&H", {}, {})[0],
            "E&H|EMERSON|FORBES MARSHALL",
        )

    def test_equivalent_filler_dropped(self):
        self.assertEqual(canonicalize_make("HP/LENOVO/EQUIVALENT", {}, {})[0], "HP|LENOVO")
        self.assertEqual(make_parts("ENDRESS HAUSER OR EQUIVALENT"), ["ENDRESS HAUSER"])

    def test_or_inside_a_word_is_not_a_separator(self):
        self.assertEqual(make_parts("ROTORK"), ["ROTORK"])

    def test_alias_applies_per_part(self):
        aliases = {collapse_key("MICHELL INSTRUMENTS"): "MICHELL"}
        self.assertEqual(
            canonicalize_make("AMETEK/MICHELL INSTRUMENTS", aliases, {}),
            ("AMETEK|MICHELL", "ALIAS"),
        )

    def test_separator_variants_share_display_spelling(self):
        display = build_display_map(["AIROPTIC", "AIROPTIC", "AIR OPTIC"])
        self.assertEqual(canonicalize_make("AIR OPTIC", {}, display), ("AIROPTIC", "RULE"))


class ModelRulesTest(unittest.TestCase):

    def test_spacing_variants_collapse(self):
        display = build_display_map(["ULTRAMAT 23", "ULTRAMAT 23", "ULTRAMAT23"])
        self.assertEqual(canonicalize_model("ULTRAMAT23", {}, display), ("ULTRAMAT 23", "RULE"))
        self.assertEqual(canonicalize_model("ULTRAMAT 23", {}, display), ("ULTRAMAT 23", "COSMETIC"))

    def test_different_variant_numbers_never_merge(self):
        display = build_display_map(["OXYMAT 61", "OXYMAT 64"])
        self.assertNotEqual(
            canonicalize_model("OXYMAT 61", {}, display)[0],
            canonicalize_model("OXYMAT 64", {}, display)[0],
        )

    def test_alias_hit(self):
        aliases = {collapse_key("MAXUM ED II"): "MAXUM EDITION II"}
        self.assertEqual(canonicalize_model("MAXUM ED II", aliases, {}), ("MAXUM EDITION II", "ALIAS"))

    def test_blank(self):
        self.assertEqual(canonicalize_model("", {}, {}), ("", ""))
        self.assertEqual(canonicalize_make("", {}, {}), ("", ""))


class AliasFileTest(unittest.TestCase):

    def test_missing_file_is_empty(self):
        self.assertEqual(load_aliases(Path(tempfile.gettempdir()) / "no_such_alias_file.csv"), {})

    def test_loader_keys_on_collapse_key_and_uppercases(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "aliases.csv"
            path.write_text("alias,canonical,note\nMaxum Ed. II,maxum edition ii,\n", encoding="utf-8")
            self.assertEqual(load_aliases(path), {"MAXUMEDII": "MAXUM EDITION II"})


class UnitAndRowsTest(unittest.TestCase):

    def test_unit_class(self):
        self.assertEqual(unit_class("NOS"), "COUNT")
        self.assertEqual(unit_class("SET"), "BUNDLE")
        self.assertEqual(unit_class("FURLONG"), "")
        self.assertEqual(unit_class(""), "")

    def test_apply_leaves_blank_rows_blank(self):
        rows = [
            {"make_normalized": "SIEMENS AG", "model_normalized": "GAS EYE", "unit_normalized": "NOS"},
            {"make_normalized": "", "model_normalized": "", "unit_normalized": ""},
        ]
        apply_canonical_columns(rows, {}, {})
        self.assertEqual(rows[0]["make_canonical"], "SIEMENS")
        self.assertEqual(rows[0]["unit_class"], "COUNT")
        self.assertEqual(
            [rows[1][k] for k in ("make_canonical", "make_canonical_basis", "model_canonical", "unit_class")],
            ["", "", "", ""],
        )


if __name__ == "__main__":
    unittest.main()
