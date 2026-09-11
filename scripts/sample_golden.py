"""
Layout-stratified sampler for tests/golden.csv (Step 1).

Classifies every PDF in the corpus by table structure (ruled vs. unruled,
column count, header-keyword signature) and writes a proportional sample to
tests/golden_candidates.csv for hand-labelling. Deterministic and re-runnable
so the golden-set selection is auditable, not a hand-picked list.

Usage:
    python scripts/sample_golden.py --n 15 --out tests/golden_candidates_stage1.csv
    python scripts/sample_golden.py --n 35 --exclude tests/golden_candidates_stage1.csv --out tests/golden_candidates_stage2.csv
"""
from __future__ import annotations

import argparse
import csv
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "Quotation PDFs" / "quotation_pdfs.csv"
PDF_ROOT = ROOT / "Quotation PDFs"

HEADER_WORDS = [
    "sl", "sr", "s.no", "item", "part no", "description", "qty", "quantity",
    "unit price", "rate", "amount", "total", "value", "uom", "unit",
]


def normalize_header_signature(words: list[str]) -> str:
    hits = []
    joined = " ".join(w.lower() for w in words)
    for hw in HEADER_WORDS:
        if hw in joined:
            hits.append(hw.replace(" ", "_").replace(".", ""))
    return "+".join(sorted(set(hits)))


def classify_pdf(path: Path) -> dict:
    """Cheap structural classification: sample up to 3 pages for tables."""
    info = {
        "n_pages": 0,
        "has_ruled_table": False,
        "max_cols": 0,
        "header_signature": "",
        "error": "",
    }
    try:
        doc = pymupdf.open(path)
    except Exception as e:  # noqa: BLE001
        info["error"] = f"open_failed:{e.__class__.__name__}"
        return info

    info["n_pages"] = doc.page_count
    sample_pages = list(range(min(doc.page_count, 6)))
    best_sig = ""
    max_cols = 0
    has_ruled = False
    try:
        for pno in sample_pages:
            page = doc[pno]
            try:
                tabs = page.find_tables()
            except Exception:  # noqa: BLE001
                continue
            for t in tabs.tables:
                ncols = len(t.header.names) if t.header and t.header.names else (
                    len(t.cells[0]) if t.cells else 0
                )
                if ncols >= 3:
                    has_ruled = True
                    max_cols = max(max_cols, ncols)
                    hdr_words = [w for w in (t.header.names or []) if w]
                    sig = normalize_header_signature(hdr_words)
                    if len(sig) > len(best_sig):
                        best_sig = sig
    finally:
        doc.close()

    info["has_ruled_table"] = has_ruled
    info["max_cols"] = max_cols
    info["header_signature"] = best_sig or "NONE"
    return info


def layout_family(info: dict) -> str:
    if info["error"]:
        return "ERROR"
    if not info["has_ruled_table"]:
        return "UNRULED_OR_NO_TABLE"
    sig = info["header_signature"]
    if "unit_price" in sig and "total" in sig:
        return "RULED_SPLIT_PRICE"
    if "amount" in sig or "total" in sig or "value" in sig:
        return "RULED_SINGLE_PRICE"
    cols = info["max_cols"]
    bucket = "3-5COL" if cols <= 5 else "6-9COL" if cols <= 9 else "10+COL"
    return f"RULED_OTHER_{bucket}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--exclude", type=Path, action="append", default=[])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit-scan", type=int, default=0, help="cap docs scanned, 0=all")
    args = ap.parse_args()

    excluded_paths: set[str] = set()
    for ex in args.exclude:
        if ex.exists():
            with open(ex, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    excluded_paths.add(row["Revision_Path"])

    rows = []
    with open(MANIFEST, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if args.limit_scan:
        rows = rows[: args.limit_scan]

    print(f"Scanning {len(rows)} manifest rows (excluding {len(excluded_paths)} already sampled)...", file=sys.stderr)

    families: dict[str, list[dict]] = defaultdict(list)
    scanned = 0
    for row in rows:
        rel = row["Revision_Path"]
        if rel in excluded_paths:
            continue
        pdf_path = PDF_ROOT / rel
        if not pdf_path.exists():
            continue
        info = classify_pdf(pdf_path)
        fam = layout_family(info)
        row2 = dict(row)
        row2["layout_family"] = fam
        row2["header_signature"] = info["header_signature"]
        row2["n_pages"] = info["n_pages"]
        families[fam].append(row2)
        scanned += 1
        if scanned % 200 == 0:
            print(f"  scanned {scanned}...", file=sys.stderr)

    print("\nLayout family distribution:", file=sys.stderr)
    for fam, items in sorted(families.items(), key=lambda kv: -len(kv[1])):
        print(f"  {fam}: {len(items)}", file=sys.stderr)

    total = sum(len(v) for v in families.values())
    rng = random.Random(args.seed)
    selected: list[dict] = []
    remaining = args.n
    fam_list = sorted(families.items(), key=lambda kv: -len(kv[1]))
    for i, (fam, items) in enumerate(fam_list):
        is_last = i == len(fam_list) - 1
        if is_last:
            take = remaining
        else:
            take = max(1, round(args.n * len(items) / total))
            take = min(take, remaining - (len(fam_list) - i - 1))  # leave >=1 for rest
        take = max(0, min(take, len(items), remaining))
        rng.shuffle(items)
        selected.extend(items[:take])
        remaining -= take
        if remaining <= 0:
            break

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) + ["layout_family", "header_signature", "n_pages"]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in selected:
            w.writerow(r)

    print(f"\nSelected {len(selected)} documents -> {args.out}", file=sys.stderr)
    fam_count = Counter(r["layout_family"] for r in selected)
    for fam, c in fam_count.most_common():
        print(f"  {fam}: {c}", file=sys.stderr)


if __name__ == "__main__":
    main()
