"""
Product Family Classification
==============================

Purpose:
    Add a `product_family` dimension for "commonly quoted modules and
    product bundles" analysis (CLAUDE.md's Objective). Unlike make/model
    canonicalization (canonicalize.py), there is no existing signal to
    consolidate here - it has to be built from scratch out of free text.

Input (optional; a missing file is an empty table, not an error):
    Quotation_Data/07_knowledge_bank/model_family_rules.csv
    Quotation_Data/07_knowledge_bank/description_family_rules.csv

Design:
    Two deterministic rule tables, checked in order:

        model_canonical  -> MODEL_FAMILY_CSV (prefix match, precise but
                             only ~5.5% of items have a recognized model)
        description      -> DESCRIPTION_FAMILY_CSV (substring/phrase
                             match, first match wins - broader coverage,
                             noisier signal)

    Model lookup runs first (more precise when it's available); the
    description table is the fallback. Neither matching returns blank,
    never a guess - the same "flag/blank rather than guess" philosophy
    as canonicalize.py's alias tables. `product_family_basis` records
    which tier produced the value (MODEL_RULE / DESCRIPTION_RULE),
    mirroring `make_canonical_basis`.

    The DESCRIPTION_FAMILY_CSV row order matters: it is evaluated
    top-to-bottom and the first matching pattern wins, so a more
    specific rule (e.g. "OXYGEN ANALYSER") must be listed before a more
    generic one that could also match the same description
    (e.g. "GAS ANALYSER") whenever the two overlap.

    Starter rule tables ship with this module's git history / docs, not
    hardcoded here - see knowledge_bank/suggest_product_families.py and
    docs/DATA_DICTIONARY.md. The family names and model->family mappings
    in the starter tables are drawn from general product-line knowledge,
    not confirmed in this repo, and need domain review before being
    trusted - exactly like make_aliases.csv/model_aliases.csv did.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

RULES_FOLDER = PROJECT_ROOT / "Quotation_Data" / "07_knowledge_bank"

MODEL_FAMILY_CSV = RULES_FOLDER / "model_family_rules.csv"
DESCRIPTION_FAMILY_CSV = RULES_FOLDER / "description_family_rules.csv"

CSV_ENCODING = "utf-8-sig"


# ============================================================
# RULE LOADING
# ============================================================

def load_model_rules(path: Path) -> list[tuple[str, str]]:
    """
    model_family_rules.csv (pattern, family, note) -> ordered list of
    (pattern_upper, family). `pattern` matches model_canonical by
    PREFIX ("ULTRAMAT" matches "ULTRAMAT 23", "ULTRAMAT 6E", ...).
    Row order matters (first match wins), same as the description table.
    """

    if not path.exists():
        return []

    rules = []

    with open(path, "r", encoding=CSV_ENCODING, newline="") as file:

        for row in csv.DictReader(file):

            pattern = (row.get("pattern", "") or "").strip().upper()
            family = (row.get("family", "") or "").strip().upper()

            if pattern and family:
                rules.append((pattern, family))

    return rules


def load_description_rules(path: Path) -> list[tuple[re.Pattern, str]]:
    """
    description_family_rules.csv (pattern, family, note) -> ordered list
    of (compiled_regex, family). `pattern` is a case-insensitive regex
    matched anywhere in the description (re.search, not anchored) - a
    plain phrase like "GAS CHROMATOGRAPH" works as-is; alternation like
    "OXYGEN ANALY(S|Z)ER" is supported for spelling variants.
    """

    if not path.exists():
        return []

    rules = []

    with open(path, "r", encoding=CSV_ENCODING, newline="") as file:

        for row in csv.DictReader(file):

            pattern = (row.get("pattern", "") or "").strip()
            family = (row.get("family", "") or "").strip().upper()

            if not pattern or not family:
                continue

            try:
                compiled = re.compile(pattern, re.IGNORECASE)

            except re.error:
                continue

            rules.append((compiled, family))

    return rules


# ============================================================
# CLASSIFICATION
# ============================================================

def classify_product_family(
    description,
    model_canonical,
    model_rules: list[tuple[str, str]],
    description_rules: list[tuple[re.Pattern, str]],
) -> tuple[str, str]:
    """
    Returns (product_family, product_family_basis). ("", "") when
    neither the model nor the description matches any rule - never a
    guess. A multi-make-style value in model_canonical ("A|B") is
    checked part-by-part, same convention as canonicalize.py.
    """

    model_value = str(model_canonical or "").upper()

    if model_value:

        for part in model_value.split("|"):

            part = part.strip()

            for pattern, family in model_rules:

                if part.startswith(pattern):
                    return (family, "MODEL_RULE")

    description_value = str(description or "")

    if description_value:

        for compiled, family in description_rules:

            if compiled.search(description_value):
                return (family, "DESCRIPTION_RULE")

    return ("", "")


def apply_product_family_columns(rows, model_rules, description_rules) -> None:
    """
    Fills product_family / product_family_basis in place on knowledge-
    bank row dicts that already carry description and model_canonical.
    Rows with neither field populated get blank family columns, same as
    canonicalize.apply_canonical_columns's treatment of blank make/model.
    """

    for row in rows:

        family, basis = classify_product_family(
            row.get("description", ""),
            row.get("model_canonical", ""),
            model_rules,
            description_rules,
        )

        row["product_family"] = family
        row["product_family_basis"] = basis
