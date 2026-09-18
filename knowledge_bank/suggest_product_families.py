"""
Product Family Suggester
=========================

Purpose:
    Unlike suggest_aliases.py (which had a clear fuzzy-similarity signal
    to propose make/model merges), there's no algorithmic way to invent
    a product family name from scratch - this can only rank candidates
    by frequency for a human to name. Reads the knowledge bank, restricts
    to rows product_family.classify_product_family() currently leaves
    blank, and reports what's most common among them.

Input:
    Quotation_Data/07_knowledge_bank/knowledge_bank_items.csv
    Quotation_Data/07_knowledge_bank/model_family_rules.csv   (optional)
    Quotation_Data/07_knowledge_bank/description_family_rules.csv (optional)

Output:
    Quotation_Data/07_knowledge_bank/product_family_suggestions.csv

    Two sections in one file (a `kind` column distinguishes them):
    - MODEL: distinct model_canonical values with no model-rule match,
      by row count - candidates to add to model_family_rules.csv.
    - DESCRIPTION_PHRASE: the most common description bigrams/trigrams
      among still-uncategorized rows, by row count - candidates to turn
      into description_family_rules.csv patterns.

Workflow:
    Review product_family_suggestions.csv, add accepted rows to
    model_family_rules.csv / description_family_rules.csv (remembering
    that description rule ORDER matters - a specific rule must precede a
    more generic one it could overlap with), then re-run
    build_knowledge_bank.py / build_knowledge_bank_coords.py.

This script does NOT modify any of its input files.
"""

from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(PROJECT_ROOT))

from product_family import (  # noqa: E402
    DESCRIPTION_FAMILY_CSV,
    MODEL_FAMILY_CSV,
    classify_product_family,
    load_description_rules,
    load_model_rules,
)


# ============================================================
# CONFIGURATION
# ============================================================

ROOT = PROJECT_ROOT.parent

ITEMS_CSV = (
    ROOT
    / "Quotation_Data"
    / "07_knowledge_bank"
    / "knowledge_bank_items.csv"
)

SUGGESTIONS_CSV = (
    ROOT
    / "Quotation_Data"
    / "07_knowledge_bank"
    / "product_family_suggestions.csv"
)

CSV_ENCODING = "utf-8-sig"

TOP_N = 60

# Purely count-driven stop list - words too generic on their own to seed
# a phrase rule (spec/unit words, connectors, boilerplate terms that
# appear in almost every description regardless of product type).
STOP_WORDS = {
    "a", "an", "the", "of", "for", "with", "and", "to", "in", "on", "at",
    "by", "is", "are", "as", "per", "this", "that", "shall", "be", "or",
    "which", "mm", "nos", "no", "yes", "type", "range", "output", "scope",
    "at-", "customer", "quoted", "charges", "area", "location", "tag",
    "moc", "inside",
}

SUGGESTION_COLUMNS = ["kind", "candidate", "rows", "note"]


# ============================================================
# CANDIDATE EXTRACTION
# ============================================================

def uncategorized_rows(items: pd.DataFrame, model_rules, description_rules) -> pd.DataFrame:

    mask = items.apply(
        lambda row: classify_product_family(
            row.get("description", ""),
            row.get("model_canonical", ""),
            model_rules,
            description_rules,
        )
        == ("", ""),
        axis=1,
    )

    return items[mask]


def model_candidates(uncategorized: pd.DataFrame) -> list[tuple[str, int]]:

    values = uncategorized.loc[uncategorized["model_canonical"] != "", "model_canonical"]

    return values.value_counts().head(TOP_N).items()


def description_phrase_candidates(uncategorized: pd.DataFrame) -> list[tuple[str, int]]:
    """
    Counts each row's distinct bigrams/trigrams once per row (not once
    per occurrence within a row) - a phrase repeated inside one long
    description shouldn't outweigh the same phrase appearing across many
    different rows, since the goal is "how many items would this rule
    cover", not "how many times does this phrase occur in the corpus".
    """

    counts = Counter()

    for description in uncategorized["description"]:

        if not description:
            continue

        words = [
            w for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", description.upper())
            if w.lower() not in STOP_WORDS
        ]

        phrases = set()

        for n in (2, 3):

            for i in range(len(words) - n + 1):
                phrases.add(" ".join(words[i:i + n]))

        counts.update(phrases)

    return counts.most_common(TOP_N)


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("PRODUCT FAMILY SUGGESTER")
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

    model_rules = load_model_rules(MODEL_FAMILY_CSV)
    description_rules = load_description_rules(DESCRIPTION_FAMILY_CSV)

    print(f"\nModel family rules loaded       : {len(model_rules)}")
    print(f"Description family rules loaded : {len(description_rules)}")
    print(f"ATTACHMENT_ITEM rows            : {len(items)}")

    uncategorized = uncategorized_rows(items, model_rules, description_rules)

    print(f"Still uncategorized             : {len(uncategorized)}")

    rows = []

    for model, count in model_candidates(uncategorized):

        rows.append({
            "kind": "MODEL",
            "candidate": model,
            "rows": count,
            "note": "add a matching pattern to model_family_rules.csv",
        })

    for phrase, count in description_phrase_candidates(uncategorized):

        rows.append({
            "kind": "DESCRIPTION_PHRASE",
            "candidate": phrase,
            "rows": count,
            "note": "add as a pattern to description_family_rules.csv - check row order",
        })

    SUGGESTIONS_CSV.parent.mkdir(parents=True, exist_ok=True)

    with open(SUGGESTIONS_CSV, "w", newline="", encoding=CSV_ENCODING) as file:

        writer = csv.DictWriter(file, fieldnames=SUGGESTION_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nMODEL candidates             : {sum(1 for r in rows if r['kind'] == 'MODEL')}")
    print(f"DESCRIPTION_PHRASE candidates : {sum(1 for r in rows if r['kind'] == 'DESCRIPTION_PHRASE')}")
    print(f"\nOutput:\n  {SUGGESTIONS_CSV}")
    print("\nInput files were not modified.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
