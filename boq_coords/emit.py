"""
CSV writer for the coords extractor. 16-column schema: the original 17
production columns plus `currency` (plan decision: non-INR rows such as SAR
or USD offers get a currency tag instead of being silently mixed into INR
totals or blanked), minus `product`/`version` (dropped - never populated in
this corpus, see fields.py).
"""
from __future__ import annotations

import csv
from pathlib import Path

FIELDNAMES = [
    "source_file", "source_path", "quotation_number", "item_no",
    "description", "make", "model",
    "quantity", "unit", "unit_price_raw", "unit_price",
    "total_price_raw", "total_price", "currency",
    "raw_row_text", "confidence", "validation_error",
]

REVIEW_FIELDNAMES = FIELDNAMES + ["path_taken"]


def write_items_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_review_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_documents_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = ["source_file", "source_path", "n_pages", "n_regions", "path_taken", "n_rows", "confidence"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
