"""
Alias Suggester
===============

Purpose:
    Propose make/model alias-table rows for human review. Deterministic
    rules in canonicalize.py already merge separator/country/legal-form
    variants; what's left ("MICHELL INSTRUMENTS" vs "MICHELL", "MAXUM ED
    II" vs "MAXUM EDITION II") needs judgement, so this script only
    SUGGESTS - nothing here changes the knowledge bank.

Input:
    Quotation_Data/07_knowledge_bank/knowledge_bank_items.csv
    Quotation_Data/07_knowledge_bank/make_aliases.csv   (optional)
    Quotation_Data/07_knowledge_bank/model_aliases.csv  (optional)

Output:
    Quotation_Data/07_knowledge_bank/alias_suggestions.csv

    field, alias, proposed_canonical, alias_rows, canonical_rows, score,
    reason

Workflow:
    Review alias_suggestions.csv, copy the rows you accept into
    make_aliases.csv / model_aliases.csv (alias, canonical, note), then
    re-run build_knowledge_bank.py. Same loop as customer_review.csv ->
    customer_name_overrides.csv.

Design:
    - Scores pairs with odoo_match_customer.match_customers.similarity_score
      (the same fuzzy scorer used for customer names).
    - Makes also propose on whole-token containment ("MICHELL
      INSTRUMENTS" contains "MICHELL"); models do not, since "SITRANS F"
      and "SITRANS SL" are different products.
    - Abbreviation rule (token-by-token prefix, "MAXUM ED II" / "MAXUM
      EDITION II") outranks a fuzzy score, which outranks containment.
    - Variant guard: a pair whose digit runs or standalone roman numerals
      differ is never proposed ("OXYMAT 61"/"OXYMAT 64", "MAXUM II"/
      "MAXUM III").
    - Always proposes the less frequent spelling -> the more frequent one,
      then follows proposals to their final target so accepted rows never
      need chaining.
    - Values with an unbalanced "(" (parser truncation, e.g.
      "VALMET(SIEMENS") are listed with reason TRUNCATED and no proposal.

Important:
    Output holds make/model names only - no customer data - but console
    output stays counts-only per project convention.

This script does NOT modify any of its input files.
"""

from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(PROJECT_ROOT))

from canonicalize import (  # noqa: E402
    MAKE_ALIASES_CSV,
    MODEL_ALIASES_CSV,
    build_display_map,
    collapse_key,
    load_aliases,
    make_parts,
)
from odoo_match_customer.match_customers import similarity_score  # noqa: E402


# ============================================================
# CONFIGURATION
# ============================================================

ITEMS_CSV = (
    PROJECT_ROOT
    / "Quotation_Data"
    / "07_knowledge_bank"
    / "knowledge_bank_items.csv"
)

SUGGESTIONS_CSV = (
    PROJECT_ROOT
    / "Quotation_Data"
    / "07_knowledge_bank"
    / "alias_suggestions.csv"
)

CSV_ENCODING = "utf-8-sig"

SCORE_THRESHOLD = 0.75

# Shortest token that may justify a containment proposal on its own -
# stops e.g. "E" or "PH" from matching everything that contains it.
MIN_CONTAINMENT_TOKEN_LENGTH = 3

ROMAN_NUMERALS = {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"}

SUGGESTION_COLUMNS = [
    "field",
    "alias",
    "proposed_canonical",
    "alias_rows",
    "canonical_rows",
    "score",
    "reason",
]


# ============================================================
# HELPERS
# ============================================================

def variant_markers(text) -> tuple:
    """
    Digit runs plus standalone roman numerals, in order. Two spellings
    with different markers are treated as different products.
    """

    text = str(text).upper()

    return tuple(re.findall(r"\d+", text)) + tuple(
        token
        for token in re.findall(r"[A-Z]+", text)
        if token in ROMAN_NUMERALS
    )


def tokens(text) -> set[str]:

    return set(re.findall(r"[A-Z0-9&]+", str(text).upper()))


def contains_whole_tokens(longer, shorter) -> bool:

    shorter_tokens = tokens(shorter)
    longer_tokens = tokens(longer)

    return (
        bool(shorter_tokens)
        and shorter_tokens < longer_tokens
        and max(len(token) for token in shorter_tokens) >= MIN_CONTAINMENT_TOKEN_LENGTH
    )


def abbreviates(first, second) -> bool:
    """
    "MAXUM ED II" / "MAXUM EDITION II": at least two tokens, same token
    count, at least one identical anchor token, and every other pair a
    word prefix of the other. Tokens containing digits must match exactly
    ("8967EX" is a different variant from "8967"), and a lone token never
    qualifies ("THERMO" is not "THERMON").
    """

    first_tokens = re.findall(r"[A-Z0-9&]+", str(first).upper())
    second_tokens = re.findall(r"[A-Z0-9&]+", str(second).upper())

    if (
        len(first_tokens) < 2
        or len(first_tokens) != len(second_tokens)
        or first_tokens == second_tokens
    ):
        return False

    pairs = list(zip(first_tokens, second_tokens))

    if not any(a == b for a, b in pairs):
        return False

    return all(
        a == b
        or (
            not re.search(r"\d", a + b)
            and min(len(a), len(b)) >= 2
            and (a.startswith(b) or b.startswith(a))
        )
        for a, b in pairs
    )


def plural_of(first, second) -> bool:
    """
    "AIROPTICS" / "AIROPTIC" - keys differ only by a trailing S.
    """

    a, b = collapse_key(first), collapse_key(second)

    return min(len(a), len(b)) >= 4 and (a + "S" == b or b + "S" == a)


def is_truncated(text) -> bool:

    text = str(text)

    return text.count("(") > text.count(")") or text.count("[") > text.count("]")


# ============================================================
# SUGGESTION ENGINE
# ============================================================

def suggest(field, spellings, aliases, allow_containment):
    """
    `spellings` - one entry per row occurrence (so counts are row counts).

    Returns (suggestion_rows, truncated_rows).
    """

    display = build_display_map(spellings)

    key_counts = Counter(
        collapse_key(spelling)
        for spelling in spellings
        if collapse_key(spelling)
    )

    # Most frequent first; ties alphabetical. A proposal always points to
    # an earlier key in this order, so following proposals can't cycle.
    ordered = sorted(
        key_counts,
        key=lambda key: (-key_counts[key], display[key]),
    )

    rank = {key: index for index, key in enumerate(ordered)}

    # Truncated values are ambiguous ("VALMET(SIEMENS") - they are listed
    # for a manual decision below, never proposed or used as a target,
    # so a guess can't propagate through a chain.
    truncated = {key for key in ordered if is_truncated(display[key])}

    best_target = {}

    for alias_key in ordered:

        if alias_key in aliases or alias_key in truncated:
            continue

        alias_text = display[alias_key]
        best = None

        for target_key in ordered[: rank[alias_key]]:

            if target_key in truncated:
                continue

            target_text = display[target_key]

            if variant_markers(alias_text) != variant_markers(target_text):
                continue

            score = similarity_score(alias_text, target_text)

            # Strongest structural evidence wins over a higher fuzzy score.
            if abbreviates(alias_text, target_text):
                strength, reason = 3, "ABBREVIATION"

            elif plural_of(alias_text, target_text):
                strength, reason = 3, "PLURAL"

            elif score >= SCORE_THRESHOLD:
                strength, reason = 2, "SIMILAR"

            elif allow_containment and (
                contains_whole_tokens(alias_text, target_text)
                or contains_whole_tokens(target_text, alias_text)
            ):
                strength, reason = 1, "CONTAINS"

            else:
                continue

            candidate = (strength, score, -rank[target_key], target_key, reason)

            if best is None or candidate > best:
                best = candidate

        if best is not None:
            best_target[alias_key] = best

    suggestion_rows = []

    for alias_key, (_, score, _, target_key, reason) in best_target.items():

        final_key = target_key

        while final_key in best_target:
            final_key = best_target[final_key][3]

        suggestion_rows.append({
            "field": field,
            "alias": display[alias_key],
            "proposed_canonical": display[final_key],
            "alias_rows": key_counts[alias_key],
            "canonical_rows": key_counts[final_key],
            "score": score,
            "reason": reason if final_key == target_key else f"{reason}_VIA_CHAIN",
        })

    truncated_rows = [
        {
            "field": field,
            "alias": display[key],
            "proposed_canonical": "",
            "alias_rows": key_counts[key],
            "canonical_rows": "",
            "score": "",
            "reason": "TRUNCATED",
        }
        for key in ordered
        if key in truncated and key not in aliases
    ]

    return suggestion_rows, truncated_rows


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("ALIAS SUGGESTER")
    print("=" * 70)

    if not ITEMS_CSV.exists():

        print(f"\nERROR: {ITEMS_CSV} not found - run build_knowledge_bank.py first.")
        return 1

    items = pd.read_csv(
        ITEMS_CSV,
        dtype=str,
        keep_default_na=False,
        encoding=CSV_ENCODING,
    )

    items = items[items["data_source"] == "ATTACHMENT_ITEM"]

    make_aliases = load_aliases(MAKE_ALIASES_CSV)
    model_aliases = load_aliases(MODEL_ALIASES_CSV)

    make_spellings = [
        part
        for value in items["make_normalized"]
        for part in make_parts(value)
    ]

    model_spellings = [
        value
        for value in items["model_normalized"]
        if value
    ]

    make_suggestions, make_truncated = suggest(
        "MAKE", make_spellings, make_aliases, allow_containment=True
    )

    model_suggestions, model_truncated = suggest(
        "MODEL", model_spellings, model_aliases, allow_containment=False
    )

    rows = sorted(
        make_suggestions + model_suggestions,
        key=lambda row: (row["field"], -int(row["canonical_rows"]), row["proposed_canonical"], -int(row["alias_rows"])),
    ) + make_truncated + model_truncated

    SUGGESTIONS_CSV.parent.mkdir(parents=True, exist_ok=True)

    with open(SUGGESTIONS_CSV, "w", newline="", encoding=CSV_ENCODING) as file:

        writer = csv.DictWriter(file, fieldnames=SUGGESTION_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nMake aliases already in table  : {len(make_aliases)}")
    print(f"Model aliases already in table : {len(model_aliases)}")
    print(f"Distinct make parts (post-rule): {len(set(map(collapse_key, make_spellings)))}")
    print(f"Distinct model keys (post-rule): {len(set(map(collapse_key, model_spellings)))}")
    print(f"\nMake suggestions   : {len(make_suggestions)}")
    print(f"Model suggestions  : {len(model_suggestions)}")
    print(f"Truncated values   : {len(make_truncated) + len(model_truncated)}")
    print(f"\nOutput:\n  {SUGGESTIONS_CSV}")
    print("\nInput files were not modified.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
