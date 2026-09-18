"""
Step 2 scorer. Two independent modes, both usable on any items CSV that
carries the production schema (v1's quotation_items.csv or the coords
extractor's quotation_items.csv):

1. --self-consistency: corpus-wide oracles that need no golden set at all
   (negative-price rate, arithmetic sanity, raw-text traceability). Runs
   over the full CSV, however many rows that is.

2. --golden tests/golden.csv: per-column exact/wrong/missing match rate
   against hand-labelled rows, joined on (source_file, item_no). This is
   the number everything is ultimately measured against, but it only means
   something once the golden set exists (plan Step 1).

Usage:
    python scripts/score.py --self-consistency Quotation_Data/03_structured_current/quotation_items.csv
    python scripts/score.py --self-consistency Quotation_Data/03f_structured_coords/quotation_items.csv
    python scripts/score.py --golden tests/golden.csv --against Quotation_Data/03f_structured_coords/quotation_items.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
csv.field_size_limit(10_000_000)

NUMERIC_FIELDS = ["quantity", "unit_price", "total_price"]
TEXT_FIELDS = ["item_no", "description", "make", "model", "unit"]
ALL_FIELDS = ["item_no", "description", "make", "model",
              "quantity", "unit", "unit_price", "total_price"]


def _to_float(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def _norm_text(v: str) -> str:
    return " ".join((v or "").split()).lower().strip()


def self_consistency(rows: list[dict], label: str) -> None:
    n = len(rows)
    print(f"\n=== Self-consistency: {label} ({n} rows) ===")
    if n == 0:
        print("  (no rows)")
        return

    neg = sum(
        1 for r in rows for f in ("unit_price", "total_price")
        if (v := _to_float(r.get(f))) is not None and v < 0
    )
    print(f"  negative price:          {neg} ({neg/n*100:.2f}%)")

    below_1000 = sum(
        1 for r in rows for f in ("unit_price", "total_price")
        if (v := _to_float(r.get(f))) is not None and 0 <= v < 1000
    )
    print(f"  price in [0, 1000):      {below_1000} ({below_1000/n*100:.2f}%)")

    no_price = sum(
        1 for r in rows
        if not r.get("unit_price") and not r.get("total_price")
    )
    print(f"  no price at all:         {no_price} ({no_price/n*100:.2f}%)")

    arith_checked = 0
    arith_ok = 0
    for r in rows:
        q, up, tp = _to_float(r.get("quantity")), _to_float(r.get("unit_price")), _to_float(r.get("total_price"))
        if q is None or up is None or tp is None:
            continue
        arith_checked += 1
        if abs(q * up - tp) <= max(1.0, 0.02 * tp):
            arith_ok += 1
    if arith_checked:
        print(f"  arithmetic ok (of {arith_checked} checkable): {arith_ok} ({arith_ok/arith_checked*100:.2f}%)")
    else:
        print("  arithmetic: no rows have all three fields to check")

    verbatim_checked = 0
    verbatim_ok = 0
    for r in rows:
        raw = r.get("raw_row_text", "") or ""
        raw_digits = "".join(c for c in raw if c.isdigit())
        # A "derived" total_price (quantity x unit_price, used only when
        # the source states no total of its own - see money fix
        # requirement 1) was never itself written anywhere in the source
        # text, so it can never legitimately appear verbatim - the same
        # exemption validate.py's own Rule 1 already makes. Without this,
        # every derived total in the corpus is a guaranteed false
        # "non-verbatim" flag here, unrelated to extraction quality.
        total_is_derived = r.get("total_price_source") == "derived"
        for f in ("unit_price", "total_price", "quantity"):
            if f == "total_price" and total_is_derived:
                continue
            v = r.get(f)
            if v in (None, ""):
                continue
            digits = "".join(c for c in str(v).split(".")[0] if c.isdigit())
            if not digits:
                continue
            verbatim_checked += 1
            if digits in raw_digits:
                verbatim_ok += 1
    if verbatim_checked:
        print(f"  verbatim in raw_row_text (of {verbatim_checked}): {verbatim_ok} ({verbatim_ok/verbatim_checked*100:.2f}%)")
    else:
        print("  verbatim: no raw_row_text / numeric fields to check")


def golden_score(golden_rows: list[dict], candidate_rows: list[dict]) -> None:
    by_key: dict[tuple, dict] = {
        (r.get("source_file", ""), str(r.get("item_no", ""))): r for r in candidate_rows
    }

    print(f"\n=== Golden-set comparison ({len(golden_rows)} labelled rows) ===")

    exact = Counter()
    wrong = Counter()
    missing = Counter()
    total = Counter()

    rows_found = 0
    rows_missing = 0

    for g in golden_rows:
        key = (g.get("source_file", ""), str(g.get("item_no", "")))
        cand = by_key.get(key)
        if cand is None:
            rows_missing += 1
            for f in ALL_FIELDS:
                if g.get(f):
                    total[f] += 1
                    missing[f] += 1
            continue
        rows_found += 1

        for f in ALL_FIELDS:
            gv = g.get(f, "")
            cv = cand.get(f, "")
            if not gv:
                continue  # golden null - nothing to score this field on
            total[f] += 1
            if f in NUMERIC_FIELDS:
                gf, cf = _to_float(gv), _to_float(cv)
                if cf is None:
                    missing[f] += 1
                elif gf is not None and abs(gf - cf) <= 1.0:
                    exact[f] += 1
                else:
                    wrong[f] += 1
            else:
                if not cv:
                    missing[f] += 1
                elif _norm_text(gv) == _norm_text(cv):
                    exact[f] += 1
                else:
                    wrong[f] += 1

    print(f"  rows matched by (source_file, item_no): {rows_found}")
    print(f"  golden rows with no candidate row at all: {rows_missing}")
    print(f"\n  {'field':<14}{'exact':>8}{'wrong':>8}{'missing':>10}{'n':>8}")
    for f in ALL_FIELDS:
        n = total[f]
        if n == 0:
            continue
        print(f"  {f:<14}{exact[f]/n*100:>7.1f}%{wrong[f]/n*100:>7.1f}%{missing[f]/n*100:>9.1f}%{n:>8}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-consistency", type=Path, help="items CSV to run corpus-wide oracles over")
    ap.add_argument("--golden", type=Path, help="tests/golden.csv")
    ap.add_argument("--against", type=Path, help="candidate items CSV to score against --golden")
    args = ap.parse_args()

    # utf-8-sig on read: strips the BOM boq_coords/emit.py writes (for
    # Excel's benefit) so the first column stays "source_file" rather than
    # "﻿source_file"; harmless on a BOM-less file.
    if args.self_consistency:
        rows = list(csv.DictReader(open(args.self_consistency, encoding="utf-8-sig")))
        self_consistency(rows, str(args.self_consistency))

    if args.golden:
        if not args.against:
            print("--golden requires --against <items.csv>", file=sys.stderr)
            return 2
        golden_rows = list(csv.DictReader(open(args.golden, encoding="utf-8-sig")))
        candidate_rows = list(csv.DictReader(open(args.against, encoding="utf-8-sig")))
        golden_score(golden_rows, candidate_rows)

    if not args.self_consistency and not args.golden:
        ap.print_help()
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
