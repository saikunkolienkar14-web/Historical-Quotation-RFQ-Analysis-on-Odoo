"""
Driver: reads the PDF manifest (Quotation PDFs/quotation_pdfs.csv), runs the
coordinate-aware extractor over each document, and writes output to
Quotation_Data/03f_structured_coords/ (never touches 03_structured_current/,
per constraint).

Usage:
    python -m boq_coords --limit 50
    python -m boq_coords --paths "Quotation PDFs/raw/SQ2507E254/OFFER-....pdf"
    python -m boq_coords            # full corpus (3,607 docs, ~0.4s/doc measured)
"""
from __future__ import annotations

import argparse
import csv
import sys
import traceback
from pathlib import Path

import pymupdf

csv.field_size_limit(10_000_000)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boq_coords.banded import is_clean_item_no  # noqa: E402
from boq_coords.emit import write_documents_csv, write_items_csv, write_review_csv  # noqa: E402
from boq_coords.fields import (  # noqa: E402
    derive_item_hierarchy,
    extract_heading,
    extract_labeled_fields,
    promote_part_no_to_model,
)
from boq_coords.money import canonical_raw, combine_price_status, parse_price, resolve_quantity_and_unit  # noqa: E402
from boq_coords.pagetext import write_pages_jsonl  # noqa: E402
from boq_coords.rows import extract_document_tables  # noqa: E402
from boq_coords.validate import apply_validation  # noqa: E402

MANIFEST = ROOT / "Quotation PDFs" / "quotation_pdfs.csv"
PDF_ROOT = ROOT / "Quotation PDFs"
OUT_DIR = ROOT / "Quotation_Data" / "03f_structured_coords"

# OCR-classified documents per Quotation_Preprocessed/quotation_documents.csv
# - skipped per plan decision (0.83% of corpus, sampled OCR docs are
# screenshots, not quotation tables; see boq_coords design notes).
OCR_DOCS_CSV = ROOT / "Quotation_Preprocessed" / "quotation_documents.csv"


def _load_ocr_stems() -> set[str]:
    if not OCR_DOCS_CSV.exists():
        return set()
    stems = set()
    with open(OCR_DOCS_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("extraction_method", "").strip().upper() == "OCR":
                name = row.get("source_file") or row.get("filename") or ""
                if name:
                    stems.add(Path(name).stem)
    return stems


def process_document(pdf_path: Path, quotation_number: str, ocr_stems: set[str]) -> tuple[list[dict], dict]:
    stem = pdf_path.stem
    source_file = f"{stem}.txt"
    source_path = str(pdf_path.relative_to(ROOT)) if pdf_path.is_relative_to(ROOT) else str(pdf_path)

    doc_summary = {
        "source_file": source_file, "source_path": source_path,
        "n_pages": 0, "n_regions": 0, "path_taken": "", "n_rows": 0, "confidence": "",
    }

    if stem in ocr_stems:
        doc_summary["path_taken"] = "OCR_SKIPPED"
        return [], doc_summary

    try:
        doc = pymupdf.open(pdf_path)
    except Exception as e:  # noqa: BLE001
        doc_summary["path_taken"] = f"OPEN_FAILED:{e.__class__.__name__}"
        return [], doc_summary

    try:
        doc_summary["n_pages"] = doc.page_count
        tables = extract_document_tables(doc)
        doc_summary["n_regions"] = len(tables)

        if not tables:
            doc_summary["path_taken"] = "NO_BOQ_TABLE"
            return [], doc_summary

        doc_summary["path_taken"] = "RULED"

        out_rows: list[dict] = []
        row_seq = 0
        for table in tables:
            for row in table.rows:
                row_seq += 1
                description_lines = row.text("description").split("\n") if row.text("description") else []
                part_no = row.text("part_no").strip()
                lines_for_fields = list(description_lines)
                if part_no:
                    lines_for_fields.append(f"PART NO: {part_no}")
                fields, remaining_desc = extract_labeled_fields(lines_for_fields)
                fields["model"] = promote_part_no_to_model(lines_for_fields, fields["model"])
                # Never emit the synthetic "PART NO: X" line itself into
                # the description column - it was added only so
                # extract_labeled_fields/promote_part_no_to_model could see it.
                remaining_desc = [ln for ln in remaining_desc if not ln.startswith("PART NO: ")]

                # A dedicated MAKE table column (vocab.py "make" field) beats
                # an inline "Make:" label in description - it's the more
                # explicit signal when both happen to be present.
                column_make = row.text("make").strip()
                make_value = column_make or fields["make"]

                qty_raw = row.text("quantity")
                pq = resolve_quantity_and_unit(qty_raw, row.text("unit"))

                unit_price_raw = row.text("unit_price")
                total_price_raw = row.text("total_price")
                up = parse_price(unit_price_raw)
                tp = parse_price(total_price_raw)

                # Precedence: a total the source document states directly is
                # never overwritten by quantity x unit_price - some items are
                # lump-sum ("1 Lot") with no per-unit breakdown, and some
                # documents' own stated totals legitimately don't equal
                # qty x unit_price (rounding, discounts). Only fall back to
                # the derived figure when the source total is genuinely
                # absent. validate.py's PRICE_ARITHMETIC_MISMATCH rule still
                # flags a stated total that disagrees with qty x unit_price -
                # it's surfaced for review, never silently corrected.
                if tp.value is not None:
                    total_price_value = tp.value
                    total_price_source = "stated"
                elif pq.value is not None and up.value is not None:
                    total_price_value = pq.value * up.value
                    total_price_source = "derived"
                else:
                    total_price_value = None
                    total_price_source = ""

                price_status = combine_price_status(up.status, tp.status)
                currency = up.currency or tp.currency or ""

                # A price cell that visibly spans several physical rows in
                # the source table (ruled._spanned_price_ranges) still has
                # its raw text attributed to whichever row segment_rows
                # bucketed it into by y-centre - not necessarily the right
                # item (see that function's docstring). Withhold the
                # derived numeric value here rather than let a plausible-
                # but-wrong number reach unit_price/total_price; the raw
                # text is untouched (unit_price_raw/total_price_raw below)
                # so it's never lost, just not attributed to this specific
                # item. No redistribution across the spanned group is
                # attempted - no precedent for that in this codebase, and
                # splitting a stated price would fabricate a number the
                # document never wrote.
                is_spanned_price = "PRICE_CELL_SPANS_MULTIPLE_ROWS" in row.flags
                unit_price_value = None if is_spanned_price else up.value
                if is_spanned_price:
                    total_price_value = None
                    total_price_source = ""

                # An item_no that isn't a single well-formed anchor (e.g.
                # unsplit sub-items concatenated into "2 .1 .2 .3", or a
                # stray non-numeric token swept in) is never emitted
                # verbatim - blank it out and let validate.py's
                # ITEM_NO_MALFORMED rule flag it (when item_no_raw was
                # non-empty), rather than passing garbage through.
                #
                # A genuinely blank item_no_raw (the row carries no printed
                # number at all - e.g. a continuation/bundled-lot row) is
                # left BLANK here, never backfilled with the row's own
                # sequential position - confirmed real-corpus bug
                # (Q25X10031): the sequential-index fallback fabricated a
                # different item number than the one actually printed
                # (or than none at all) purely because this row happened to
                # be the Nth one extracted, which is only meaningful within
                # a single call to this loop, not the source document.
                # row_seq (below) carries that same positional information
                # explicitly, so nothing is lost - it's just no longer
                # confused with a real printed item number.
                item_no_raw = row.text("item_no").strip()
                item_no = item_no_raw if is_clean_item_no(item_no_raw) else ""
                parent_item_no, item_level = derive_item_hierarchy(item_no)

                description_full = "\n".join(remaining_desc).strip()

                out_row = {
                    "source_file": source_file,
                    "source_path": source_path,
                    "quotation_number": quotation_number,
                    # Row's own position in this document's extraction order
                    # (1-based, across all of the document's tables) -
                    # unique within source_path even when item_no is blank
                    # (see the item_no comment above), unlike item_no itself.
                    "row_seq": row_seq,
                    "item_no": item_no,
                    "item_no_raw": item_no_raw,
                    "parent_item_no": parent_item_no,
                    "item_level": item_level,
                    "product_name": extract_heading(description_full),
                    "description_full": description_full,
                    "make": make_value,
                    "model": fields["model"],
                    "quantity": pq.value if pq.value is not None else "",
                    "quantity_raw": qty_raw,
                    "unit": pq.unit or "",
                    "unit_price_raw": canonical_raw(up),
                    "unit_price": unit_price_value if unit_price_value is not None else "",
                    "total_price_raw": canonical_raw(tp),
                    "total_price": total_price_value if total_price_value is not None else "",
                    "total_price_source": total_price_source,
                    "price_status": price_status,
                    "currency": currency,
                    "raw_row_text": row.raw_row_text(),
                    "confidence": "HIGH",
                    # Private inputs to validate.py's rules 7/8 (see
                    # ruled._spanned_price_ranges and
                    # rows._unheaded_continuation_rows) - dropped from the
                    # written CSV by emit.py's extrasaction="ignore", never
                    # real output columns.
                    "_price_cell_spans_multiple_rows": is_spanned_price,
                    "_continuation_bands_reinferred":
                        "CONTINUATION_BANDS_REINFERRED" in row.flags,
                }
                out_row = apply_validation(out_row)
                out_rows.append(out_row)

        doc_summary["n_rows"] = len(out_rows)
        low = sum(1 for r in out_rows if r["confidence"] == "LOW")
        doc_summary["confidence"] = "MIXED" if 0 < low < len(out_rows) else ("LOW" if low else "HIGH")
        return out_rows, doc_summary

    except Exception:  # noqa: BLE001
        doc_summary["path_taken"] = "EXTRACT_ERROR"
        traceback.print_exc(file=sys.stderr)
        return [], doc_summary
    finally:
        doc.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--paths", nargs="*", default=None, help="explicit PDF paths, skip manifest")
    ap.add_argument("--keep-pages", action="store_true", help="also write raw page-text sidecars")
    ap.add_argument("--out-dir", type=Path, default=None)
    args = ap.parse_args()

    global OUT_DIR
    if args.out_dir:
        OUT_DIR = args.out_dir

    ocr_stems = _load_ocr_stems()

    if args.paths:
        jobs = [(Path(p), Path(p).stem) for p in args.paths]
    else:
        # utf-8-sig strips the leading BOM pymupdf's manifest export leaves
        # on the first header ("﻿RFQ") - without this, r.get("RFQ")
        # silently returns None for every row.
        with open(MANIFEST, encoding="utf-8-sig") as f:
            manifest_rows = list(csv.DictReader(f))
        if args.limit:
            manifest_rows = manifest_rows[: args.limit]

        jobs = []
        seen_revisions: set[tuple[str, str]] = set()
        for r in manifest_rows:
            if not r.get("Revision_Path"):
                continue

            # quotation_number: Revision_Number is often already the full
            # id ("Q24W10129R1"), but for a real subset of manifest rows
            # it's just the bare revision code ("R0", "R1"...) with no RFQ
            # prefix at all - confirmed 9 unrelated PDFs all collapsing to
            # quotation_number="R0" before this fix. Always combine RFQ +
            # Revision_Number rather than trusting Revision_Number alone.
            rfq = (r.get("RFQ") or "").strip()
            revision_number = (r.get("Revision_Number") or "").strip()
            if revision_number.startswith(rfq) and rfq:
                quotation_number = revision_number
            else:
                quotation_number = f"{rfq}{revision_number}"
            if not quotation_number:
                quotation_number = rfq or revision_number

            # Two distinct source PDFs can share one manifest (RFQ,
            # Revision_Number) pair - confirmed a real manifest data-entry
            # duplicate (Q24S10070R1_AMADAS_IOCLPARADEEP_Commercial.pdf and
            # "MEIL - AMADAS final price offer.pdf" both stamped
            # "Q24S10070R1"), which double-counted the same ~17 items.
            # Keep the first file encountered in manifest order, skip later
            # duplicates rather than silently emitting the same content twice.
            dedup_key = (rfq, revision_number)
            if dedup_key in seen_revisions:
                print(
                    f"  skipping duplicate manifest entry for {quotation_number}: "
                    f"{r.get('Revision_Filename', '')}",
                    file=sys.stderr,
                )
                continue
            seen_revisions.add(dedup_key)

            jobs.append((PDF_ROOT / r["Revision_Path"], quotation_number))

    all_items: list[dict] = []
    all_docs: list[dict] = []

    for i, (pdf_path, quotation_number) in enumerate(jobs):
        if not pdf_path.exists():
            continue
        items, summary = process_document(pdf_path, quotation_number, ocr_stems)
        all_items.extend(items)
        all_docs.append(summary)

        if args.keep_pages:
            try:
                doc = pymupdf.open(pdf_path)
                stem = pdf_path.stem
                write_pages_jsonl(OUT_DIR / "pages" / stem / f"{stem}.pages.jsonl", doc)
                doc.close()
            except Exception:  # noqa: BLE001
                pass

        if (i + 1) % 100 == 0:
            print(f"  processed {i + 1}/{len(jobs)}...", file=sys.stderr)

    write_items_csv(OUT_DIR / "quotation_items.csv", all_items)
    write_review_csv(
        OUT_DIR / "coords_review.csv",
        [r for r in all_items if r.get("confidence") == "LOW"],
    )
    write_documents_csv(OUT_DIR / "coords_documents.csv", all_docs)

    print(f"\n{len(jobs)} documents processed, {len(all_items)} rows written -> {OUT_DIR}", file=sys.stderr)
    path_taken_counts = {}
    for d in all_docs:
        path_taken_counts[d["path_taken"]] = path_taken_counts.get(d["path_taken"], 0) + 1
    for k, v in sorted(path_taken_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
