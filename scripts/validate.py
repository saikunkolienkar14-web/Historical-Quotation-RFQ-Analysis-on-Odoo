"""
Step 4 CLI: run boq_coords.validate over an extracted items CSV.

Usage:
    python scripts/validate.py Quotation_Data/03f_structured_coords/quotation_items.csv

Prints a summary (rows passing / rows flagged, broken down by rule) and
writes <input>_validated.csv with confidence/validation_error filled in.
Never drops a row - every row is emitted either way.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boq_coords.validate import apply_validation, validate_row  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/validate.py <items.csv>", file=sys.stderr)
        return 2

    in_path = Path(sys.argv[1])
    rows = list(csv.DictReader(open(in_path, encoding="utf-8")))

    rule_counts: Counter[str] = Counter()
    n_failed = 0
    out_rows = []
    for row in rows:
        errors = validate_row(row)
        if errors:
            n_failed += 1
            for e in errors:
                rule_counts[e.split(":")[0]] += 1
        out_rows.append(apply_validation(row))

    n = len(rows)
    print(f"Rows: {n}")
    print(f"  passing: {n - n_failed} ({(n - n_failed) / n * 100:.1f}%)" if n else "  passing: 0")
    print(f"  flagged LOW: {n_failed} ({n_failed / n * 100:.1f}%)" if n else "  flagged LOW: 0")
    print("\nBy rule:")
    for rule, count in rule_counts.most_common():
        print(f"  {rule}: {count}")

    neg_prices = sum(
        1 for r in rows
        for f in ("unit_price", "total_price")
        if r.get(f) not in (None, "") and _to_float(r[f]) is not None and _to_float(r[f]) < 0
    )
    print(f"\nNegative prices: {neg_prices} (must be 0 by construction)")

    out_path = in_path.with_name(in_path.stem + "_validated.csv")
    fieldnames = list(out_rows[0].keys()) if out_rows else []
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(out_rows)
    print(f"\nWrote {out_path}")

    return 0


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
