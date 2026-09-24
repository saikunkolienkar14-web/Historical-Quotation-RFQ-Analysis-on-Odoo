"""
Freeze boq_coords' current row extraction for a few real documents as a
JSON snapshot, so a later change to row segmentation can be checked
against output a human has already verified as correct
(tests/test_boq_snapshots.py compares against it).

Only run this when the current output HAS been manually verified - the
snapshot is a record of "known good", not of "whatever it does today".

The snapshot holds real item descriptions and prices, so it lives under
tests/snapshots/, which is gitignored (same policy as tests/golden.csv).
Prints aggregate counts only.

Usage:
    python scripts/snapshot_rows.py --out tests/snapshots/boq_rows_snapshot.json \
        "Quotation PDFs/raw/Q24X10030/Q24X10030 EM Singapore Jurong.pdf" ...
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boq_coords.rows import extract_document_tables  # noqa: E402

# ==========================================================================
# CONFIGURATION
# ==========================================================================

SNAPSHOT_FIELDS = (
    "item_no", "description", "quantity", "unit",
    "unit_price", "total_price", "part_no", "make",
)


# ==========================================================================
# EXTRACTION
# ==========================================================================

def snapshot_document(pdf_path: Path) -> list[list[dict[str, str]]]:
    """One list per extracted table, one dict of field -> text per row."""
    doc = pymupdf.open(pdf_path)
    try:
        tables = extract_document_tables(doc)
        return [
            [{f: row.text(f) for f in SNAPSHOT_FIELDS} for row in table.rows]
            for table in tables
        ]
    finally:
        doc.close()


def snapshot_key(pdf_path: Path) -> str:
    """Path relative to the project root, forward slashes - stable across
    machines and what the test looks the PDF up by."""
    return pdf_path.resolve().relative_to(ROOT).as_posix()


# ==========================================================================
# MAIN
# ==========================================================================

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    existing = {}
    if args.out.exists():
        existing = json.loads(args.out.read_text(encoding="utf-8"))

    for pdf in args.pdfs:
        tables = snapshot_document(pdf)
        existing[snapshot_key(pdf)] = tables
        print(f"snapshotted 1 document: {len(tables)} tables, "
              f"{sum(len(t) for t in tables)} rows")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(existing, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(existing)} documents in snapshot file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
