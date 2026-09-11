"""
Select a Benchmark Test Set for LLM Extraction Model Comparison
=================================================================

Picks a handful of "good" (parser was confident, nothing flagged) and
"bad" (parser flagged a document-level problem) quotations from the
existing pipeline output, copies their raw text and matching ground
truth rows into llm_fewshot/benchmark_set/, ready to zip and upload to
Colab for llm_fewshot/benchmark_models.ipynb.

No anonymization - this benchmark set stays local to the user's own use,
per docs/FEWSHOT_EXAMPLE_SELECTION.md's Step 1/2 selection logic, minus
Step 2.5 (anonymize).

Input:
    Quotation_Data/03_structured_current/quotations.csv
    Quotation_Data/03_structured_current/quotation_items.csv
    Quotation_Data/03_structured_current/parser_review.csv

Output:
    llm_fewshot/benchmark_set/texts/<example_id>.txt
    llm_fewshot/benchmark_set/quotations_ground_truth.csv
    llm_fewshot/benchmark_set/quotation_items_ground_truth.csv
    llm_fewshot/benchmark_set/manifest.csv

Usage:
    python select_benchmark_set.py [--good N] [--bad N]
"""

import argparse
import csv
import sys
from pathlib import Path

csv.field_size_limit(sys.maxsize)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STRUCTURED_DIR = PROJECT_ROOT / "Quotation_Data" / "03_structured_current"
OUTPUT_ROOT = Path(__file__).resolve().parent / "benchmark_set"
TEXTS_DIR = OUTPUT_ROOT / "texts"


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def pick_good(quotations, n):
    candidates = [
        row for row in quotations
        if row["document_confidence"] == "HIGH"
        and not row["warnings"]
        and row["boq_detected"] == "YES"
    ]
    # Spread picks across the list rather than clustering at the top,
    # for some variety in customer/layout without needing real sampling.
    step = max(1, len(candidates) // n)
    return candidates[::step][:n]


def pick_bad(quotations, review_rows, n):
    bad_source_files = []
    seen = set()
    for row in review_rows:
        if row["field"] == "document" and row["source_file"] not in seen:
            seen.add(row["source_file"])
            bad_source_files.append(row["source_file"])
    by_source_file = {row["source_file"]: row for row in quotations}
    picked = []
    for source_file in bad_source_files:
        row = by_source_file.get(source_file)
        if row is not None:
            picked.append(row)
        if len(picked) >= n:
            break
    return picked


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--good", type=int, default=6)
    parser.add_argument("--bad", type=int, default=6)
    args = parser.parse_args()

    quotations = read_csv(STRUCTURED_DIR / "quotations.csv")
    items = read_csv(STRUCTURED_DIR / "quotation_items.csv")
    review_rows = read_csv(STRUCTURED_DIR / "parser_review.csv")

    good = pick_good(quotations, args.good)
    bad = pick_bad(quotations, review_rows, args.bad)
    selected = [("good", row) for row in good] + [("bad", row) for row in bad]

    TEXTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest_rows = []
    quotations_out = []
    items_out = []

    for i, (label, row) in enumerate(selected, start=1):
        example_id = f"{label}_{i:02d}"
        source_path = PROJECT_ROOT / row["source_path"]
        if not source_path.exists():
            print(f"SKIP {example_id}: source text not found at {source_path}")
            continue

        text = source_path.read_text(encoding="utf-8", errors="replace")
        (TEXTS_DIR / f"{example_id}.txt").write_text(text, encoding="utf-8")

        quotations_out.append({**row, "example_id": example_id})
        for item_row in items:
            if item_row["source_path"] == row["source_path"]:
                items_out.append({**item_row, "example_id": example_id})

        manifest_rows.append({
            "example_id": example_id,
            "good_or_bad": label,
            "source_file": row["source_file"],
            "source_path": row["source_path"],
            "quotation_number": row["quotation_number"],
        })

    quotations_fields = ["example_id"] + list(quotations[0].keys())
    with open(OUTPUT_ROOT / "quotations_ground_truth.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=quotations_fields)
        writer.writeheader()
        writer.writerows(quotations_out)

    if items_out:
        items_fields = ["example_id"] + list(items[0].keys())
        with open(OUTPUT_ROOT / "quotation_items_ground_truth.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=items_fields)
            writer.writeheader()
            writer.writerows(items_out)

    with open(OUTPUT_ROOT / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["example_id", "good_or_bad", "source_file", "source_path", "quotation_number"])
        writer.writeheader()
        writer.writerows(manifest_rows)

    print(f"Selected {len(manifest_rows)} examples ({len(good)} good / {len(bad)} bad) -> {OUTPUT_ROOT}")


if __name__ == "__main__":
    main()
