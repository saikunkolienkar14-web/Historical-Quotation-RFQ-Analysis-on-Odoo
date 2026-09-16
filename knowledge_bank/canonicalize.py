"""
Make / Model / Unit Canonicalization
====================================

Purpose:
    Consolidate spelling variants of the same make or model into one
    canonical value, and classify units so unit prices are only compared
    like-for-like. Shared by build_knowledge_bank.py,
    build_knowledge_bank_coords.py and suggest_aliases.py.

Input:
    Quotation_Data/07_knowledge_bank/make_aliases.csv   (optional)
    Quotation_Data/07_knowledge_bank/model_aliases.csv  (optional)

    Columns: alias, canonical, note. Human-curated - normally seeded from
    suggest_aliases.py's alias_suggestions.csv after review. A missing
    file is an empty table, not an error.

Design:
    Three tiers, each kept beside the previous one in the output:

        raw make              "SIEMENS AG, GERMANY"
        make_normalized       cosmetic only (build_knowledge_bank.py)
        make_canonical        this module

    Deterministic rules first (split alternates, strip country/legal-form
    noise, separator-insensitive grouping key), then the alias table.
    Fuzzy similarity is NEVER applied here - it only proposes alias rows
    in suggest_aliases.py, because it cannot tell "OXYMAT 61" from
    "OXYMAT 64". `*_canonical_basis` records which tier produced the
    value: COSMETIC (unchanged), RULE (rules only), ALIAS (alias table).

    Multi-make alternates ("FORBES MARSHALL/ EMERSON/ E&H") stay one
    value: each part canonicalized, deduplicated, sorted, "|"-joined.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path


# ============================================================
# CONFIGURATION
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ALIAS_FOLDER = PROJECT_ROOT / "Quotation_Data" / "07_knowledge_bank"

MAKE_ALIASES_CSV = ALIAS_FOLDER / "make_aliases.csv"
MODEL_ALIASES_CSV = ALIAS_FOLDER / "model_aliases.csv"

CSV_ENCODING = "utf-8-sig"

MULTI_MAKE_SEPARATOR = "|"

# Parts made up only of these words are "or equivalent" filler, not a
# make ("OR EQUIVALENT", "APPROVED MAKE").
FILLER_WORDS = {
    "EQUIVALENT", "EQUIV", "EQUAL", "SIMILAR", "ANY",
    "APPROVED", "MAKE", "OR", "AND", "OTHER", "OTHERS",
}

COUNTRY_WORDS = [
    "GERMANY", "USA", "UK", "JAPAN", "INDIA", "ITALY", "FRANCE",
    "SWITZERLAND", "CHINA", "KOREA", "SWEDEN", "FINLAND", "NETHERLANDS",
    "HOLLAND", "DENMARK", "AUSTRIA", "BELGIUM", "SPAIN", "CANADA",
    "SINGAPORE", "ISRAEL", "NORWAY", "EUROPE",
]

LEGAL_FORM_WORDS = [
    "AG", "GMBH", "LTD", "LIMITED", "PVT", "PRIVATE", "INC", "CORP",
    "CORPORATION", "LLC", "PLC", "SA", "BV",
]

COMPANY_NOISE_PATTERN = re.compile(
    r"\bU\.S\.A\b\.?|\b(?:"
    + "|".join(COUNTRY_WORDS + LEGAL_FORM_WORDS)
    + r")\b\.?"
)

MAKE_SPLIT_PATTERN = re.compile(r"/|\bOR\b")

EDGE_PUNCTUATION = " .,:;-|/\\\"'()[]&"

UNIT_CLASS = {
    "NOS": "COUNT", "PCS": "COUNT", "EACH": "COUNT", "UNIT": "COUNT",
    "SET": "BUNDLE", "LOT": "BUNDLE", "JOB": "BUNDLE",
    "METER": "LENGTH", "MM": "LENGTH", "CM": "LENGTH", "KM": "LENGTH",
    "HOUR": "TIME", "DAY": "TIME", "MONTH": "TIME", "YEAR": "TIME",
    "KG": "MASS", "GRAM": "MASS", "MG": "MASS", "TON": "MASS",
    "LTR": "VOLUME",
    "SQM": "AREA", "SQFT": "AREA",
}


# ============================================================
# TEXT HELPERS
# ============================================================

def normalize_spaces(text) -> str:

    return re.sub(r"\s+", " ", str(text)).strip()


def collapse_key(text) -> str:
    """
    Separator-insensitive grouping key: "ULTRAMAT 23" and "ULTRAMAT23"
    collide, "OXYMAT 61" and "OXYMAT 64" never do.
    """

    return re.sub(r"[^A-Z0-9]", "", str(text).upper())


def clean_part(text) -> str:

    text = re.sub(r"\(\s*\)", " ", str(text))
    text = re.sub(r"[,;]+", " ", text)

    return normalize_spaces(normalize_spaces(text).strip(EDGE_PUNCTUATION))


# ============================================================
# MAKES
# ============================================================

def split_make_parts(value) -> list[str]:
    """
    "FORBES MARSHALL/ EMERSON/ E&H" -> ["FORBES MARSHALL", "EMERSON", "E&H"].
    Splits on "/" and a standalone "OR" only - never "&" or "," - and
    drops pure filler parts ("EQUIVALENT", "OR EQUIVALENT").
    """

    parts = []

    for raw_part in MAKE_SPLIT_PATTERN.split(str(value).upper()):

        part = clean_part(raw_part)

        if not part:
            continue

        words = re.findall(r"[A-Z0-9&]+", part)

        if words and all(word in FILLER_WORDS for word in words):
            continue

        parts.append(part)

    return parts


def strip_company_noise(part) -> str:
    """
    "SIEMENS AG, GERMANY" -> "SIEMENS". Falls back to the input if
    stripping would leave nothing (a make that is itself e.g. "USA").
    """

    stripped = clean_part(COMPANY_NOISE_PATTERN.sub(" ", str(part).upper()))

    return stripped or clean_part(part)


def make_parts(make_normalized) -> list[str]:

    return [
        strip_company_noise(part)
        for part in split_make_parts(make_normalized)
    ]


# ============================================================
# ALIAS TABLES / DISPLAY SPELLINGS
# ============================================================

def load_aliases(path: Path) -> dict[str, str]:
    """
    alias,canonical,note CSV -> {collapse_key(alias): CANONICAL}.
    """

    if not path.exists():
        return {}

    aliases = {}

    with open(path, "r", encoding=CSV_ENCODING, newline="") as file:

        for row in csv.DictReader(file):

            key = collapse_key(row.get("alias", "") or "")
            canonical = normalize_spaces(row.get("canonical", "") or "").upper()

            if key and canonical:
                aliases[key] = canonical

    return aliases


def build_display_map(spellings) -> dict[str, str]:
    """
    Per collapse key, the most frequent real spelling (ties broken
    alphabetically) - so a canonical value reads "ULTRAMAT 23", not
    "ULTRAMAT23", and is stable for a given corpus.
    """

    counts = Counter(
        spelling
        for spelling in spellings
        if collapse_key(spelling)
    )

    best = {}

    for spelling, count in counts.items():

        key = collapse_key(spelling)
        rank = (-count, spelling)

        if key not in best or rank < best[key][0]:
            best[key] = (rank, spelling)

    return {key: spelling for key, (_, spelling) in best.items()}


# ============================================================
# CANONICALIZATION
# ============================================================

def canonicalize_make(make_normalized, aliases, display_map):
    """
    Returns (make_canonical, basis). Blank input -> ("", "").
    """

    value = str(make_normalized or "")

    if not value:
        return ("", "")

    canonical_parts = set()
    used_alias = False

    for part in make_parts(value):

        key = collapse_key(part)

        if key in aliases:
            canonical_parts.add(aliases[key])
            used_alias = True

        else:
            canonical_parts.add(display_map.get(key, part))

    canonical = MULTI_MAKE_SEPARATOR.join(sorted(canonical_parts))

    if used_alias:
        return (canonical, "ALIAS")

    return (canonical, "COSMETIC" if canonical == value else "RULE")


def canonicalize_model(model_normalized, aliases, display_map):
    """
    Returns (model_canonical, basis). Blank input -> ("", "").
    """

    value = str(model_normalized or "")

    if not value:
        return ("", "")

    key = collapse_key(value)

    if key in aliases:
        return (aliases[key], "ALIAS")

    canonical = display_map.get(key, value)

    return (canonical, "COSMETIC" if canonical == value else "RULE")


def unit_class(unit_normalized) -> str:

    return UNIT_CLASS.get(str(unit_normalized or ""), "")


def apply_canonical_columns(rows, make_aliases, model_aliases) -> None:
    """
    Fill make_canonical / make_canonical_basis / model_canonical /
    model_canonical_basis / unit_class in place on knowledge-bank row
    dicts that already carry *_normalized. Display spellings are chosen
    across all rows, which is why this runs after every row is built.
    """

    make_display = build_display_map(
        part
        for row in rows
        for part in make_parts(row.get("make_normalized", ""))
    )

    model_display = build_display_map(
        row.get("model_normalized", "")
        for row in rows
    )

    for row in rows:

        row["make_canonical"], row["make_canonical_basis"] = canonicalize_make(
            row.get("make_normalized", ""), make_aliases, make_display
        )

        row["model_canonical"], row["model_canonical_basis"] = canonicalize_model(
            row.get("model_normalized", ""), model_aliases, model_display
        )

        row["unit_class"] = unit_class(row.get("unit_normalized", ""))
