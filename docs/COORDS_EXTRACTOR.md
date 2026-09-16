# Coordinate-Aware BOQ Extractor (`boq_coords/`)

A second, independent extraction path for quotation line items, built
alongside `quotation_parser_v1.py` (which stays frozen — see
[`PROJECT_NOTES.md`](../PROJECT_NOTES.md)). Where v1 reconstructs table
columns from **flattened, linearised text** with regex, `boq_coords`
reads each PDF's own **word-level coordinates** directly, so column
assignment is a geometric question — "which x-range does this token sit
in" — rather than a guess.

This closes the specific failure class that motivated it: v1's
`parse_number()` stripped everything but digits/commas/hyphens out of a
price cell, so ordinary prose text like `"Power Supply: 230 VAC, 50 Hz"`
or `"Size-6x4"` silently became a fabricated negative price. `boq_coords`
never applies a number parser to prose in the first place — a word only
joins a price field if it sits inside a *located* price column, and even
then only if the whole line's content in that column parses as money or a
recognized placeholder.


## Why a second pipeline instead of fixing v1

`quotation_pdf_preprocessor.py` linearises each page with
`page.get_text("text")`, which destroys column geometry before v1 ever
sees the document — the information needed to assign columns correctly is
already gone by the time v1's regex runs. Fixing this requires reading
coordinates, which means re-extracting from the PDF, not patching v1's
regex layer. `boq_coords` reads `page.get_text("words")` (word-level
bounding boxes) and `page.find_tables()` (ruled-table geometry) directly,
and is deliberately built as a new package rather than a v1 rewrite so v1
stays available, unmodified, as the parser of record until `boq_coords`
is validated against it.


## Architecture

```
boq_coords/
├── vocab.py       Header-keyword vocabulary, copied (not imported) from
│                   quotation_parser_v1.py so v1 stays frozen.
├── geometry.py     Word / Line / ColumnBand primitives, line clustering.
├── money.py        Anchored money/quantity grammar + plausibility bounds
│                   (replaces v1's strip-non-digits parse_number()).
├── locate.py       Finds BOQ table regions on each page — both ruled
│                   (find_tables()-backed) and unruled/borderless tables.
├── columns.py      Header detection + column-band construction for the
│                   ruled path.
├── ruled.py        Ties a located region to real page words, runs row
│                   segmentation.
├── banded.py        Row segmentation — the core algorithm shared by the
│                   ruled and unruled paths alike.
├── rows.py         Multi-page table stitching (headered continuation +
│                   unheaded continuation).
├── fields.py       make/model extraction from labeled text
│                   ("Make: Siemens") within a row's description.
├── pagetext.py     Verbatim per-page text sidecar (JSONL), independent
│                   of the structured extraction.
├── emit.py         CSV schema + writers.
├── validate.py     Six-rule row validator (see below).
└── __main__.py     CLI driver: `python -m boq_coords [--limit N]`
```

### Locating a BOQ region (`locate.py`)

Every `page.find_tables()` result is a header candidate; `columns.find_header`
slides a window over the first few physical rows, merges them column-wise
(a header frequently spans more than one physical row — e.g. `"UNIT
PRICE"` / `"(INR)"` on separate lines), and accepts the window whose
merged text matches `description` + at least one price field. Rejects
apply at multiple points: a candidate window containing a money token
(it swallowed a real data row, not a header), a 2-column Price
Basis/GST/Freight table (commercial terms, not a BOQ), and a
table-of-contents page (repeated `SECTION n:` headings with no money).

Not every priced table has ruling lines. When `find_tables()` finds
nothing usable on a page that still carries money tokens, `locate.py`
falls back to an **unruled** path: it clusters words into a single header
line, groups that line's words into column labels by x-gap, and builds
bands from the midpoints between adjacent label clusters.

### Row segmentation (`banded.py`)

Column geometry answers "which field does this word belong to," not
"where does one row end and the next begin" — a ruled table's header is
often correct while its entire body collapsed into one giant cell (rules
exist only around the header). Row boundaries are derived, in priority
order, from: horizontal ruling lines spanning most of the table width,
then item-number anchors (with boundaries taken from gaps in the
*description* column, not the item-number's own y-position — item numbers
are frequently vertically centred in a tall multi-line cell, which drags
the price onto the wrong row if used directly), then money-line positions
as a last resort.

**The structural fix for the known bug class**: a word only stays in a
price band if that band's *entire line* parses as money or a recognized
placeholder (`QUOTED`, `Inclusive`, `TBD`, ...) — otherwise the whole line
reverts to description. This is what stops prose from ever reaching a
price field, independent of what a number parser alone would do with it.

A row with a description but no item number and no price merges into the
preceding row (bundled-lot convention), capped and stopped at any
`STOP_SECTION_MARKERS` line so a merge can't run into an unrelated
document section.

### Money grammar (`money.py`)

Fully anchored (`^...$`, never `re.search` over prose), no minus sign in
the grammar at all, Indian (`45,00,000`) and Western (`4,500,000`) comma
grouping both validated, Lakh/Crore scale words, currency detection
(INR/USD/SAR/EUR/GBP), and plausibility bounds v1 never applied to price
(`1,000 ≤ total ≤ 5,000,000,000`, `1,000 ≤ unit ≤ 500,000,000`) plus an
arithmetic cross-check (`qty × unit_price ≈ total_price`).

### Validation (`validate.py`)

Every row is checked against seven rules; a failing row is never dropped —
it gets `confidence=LOW` and the failed rule name(s) in
`validation_error`:

| Rule | Checks |
|---|---|
| Traceability | Every number in `unit_price`/`total_price`/`quantity` appears verbatim in `raw_row_text` |
| Arithmetic | `quantity × unit_price ≈ total_price` (2% tolerance) when all three are present |
| Price form | Numeric only with recognized comma grouping, Lakhs/Cr, or a placeholder sentinel |
| Quantity form | A number needs a recognized unit word — a bare number is not a quantity |
| Price bounds | Never negative, never below 1,000 |
| item_no shape | A single well-formed anchor (`"1"`, `"2.3"`) — never multiple concatenated anchors or unrelated swept-in text |
| Price cell span | `PRICE_CELL_SPANS_MULTIPLE_ROWS` — the row's price came from a table cell that visibly spans more than one physical row (`ruled._spanned_price_ranges`, see Known Limitations) |


## Output schema

`Quotation_Data/03f_structured_coords/quotation_items.csv` — 22 columns:

`source_file, source_path, quotation_number, item_no, parent_item_no,
item_level, product_name, description_full, make, model, quantity, unit,
unit_price_raw, unit_price, total_price_raw, total_price,
total_price_source, price_status, currency, raw_row_text, confidence,
validation_error`

`item_no` is **text**, exactly as printed in the source (`"1"`, `"1.1"`,
`"4a"`) — `"1.1"` is a two-level item marker, not the number 1.1.
`parent_item_no` (text, blank at top level) and `item_level` (1 = top
level, 2 = sub-item, 0 = no item number at all) expose that hierarchy
explicitly, via `fields.derive_item_hierarchy`. CSV carries no types, so
**read `item_no`/`parent_item_no` as strings** (e.g. pandas
`read_csv(dtype=str)`); otherwise they are coerced to floats and `"1.10"`
collapses onto `"1.1"`.

`product_name` is a short heading (≤ 15 words) derived from
`description_full` by `fields.extract_heading`: it accumulates lines until
a labelled field (`"Service:"`), a `"With "` clause, or a bullet — where
"bullet" includes the Private-Use-Area glyphs (U+F0B7/F06C/F0A7) that
Wingdings/Symbol fonts emit in these PDFs, not just `"•"`. When the very
first line is already a stop line (accessory/spec-only sub-items), it
falls back to a capped excerpt rather than returning empty, and a word cap
applies either way so an upstream segmentation failure cannot leak a whole
merged table into the field.

`*_raw` columns preserve the exact source text; the parsed numeric
columns are blank (not guessed) whenever the form doesn't hold. `product`
and `version` were considered and dropped — this corpus never labels
either field inline (only `"Make:"`/`"Model:"`), so both columns would be
permanently empty. `description` was split into `product_name` (a short
heading) and `description_full` (the complete, unmodified spec text) so
neither is lost.

`unit_price` and `total_price` are always kept as two independent fields —
`total_price` is never silently replaced by `quantity x unit_price`. When
the source document states its own total, that value is used as-is and
`total_price_source` is `"stated"`; `quantity x unit_price` is only used as
a fallback when the source states no total at all, and in that case
`total_price_source` is `"derived"`. A stated total that disagrees with
`quantity x unit_price` is flagged (`PRICE_ARITHMETIC_MISMATCH`), never
auto-corrected.

`price_status` is one of `NUMERIC`, `QUOTED_SEPARATELY`, `INCLUDED`, or
`MISSING`, covering both price cells together (they carry the same status
in practice). When status is `QUOTED_SEPARATELY` or `INCLUDED`,
`unit_price_raw`/`total_price_raw` are normalized to a single canonical
token (`"QUOTED"` or `"INCLUDED"`) regardless of the original phrasing
("To be Quoted", "TBQ", "Price on Request" all become `"QUOTED"`; "Incl.",
"Bundled", "Part of above" all become `"INCLUDED"`) so they group together
downstream instead of producing near-duplicate raw values. `MISSING` rows
are flagged with one of two distinct `validation_error` values —
`PRICE_ABSENT` (no price text at all, e.g. folded into another line) or
`PRICE_PARSE_FAILURE` (price text present but unrecognized) — so a genuine
parsing gap is never conflated with an intentionally blank cell.

`coords_review.csv` carries only the `confidence=LOW` rows, for a fast
human-review queue. `coords_documents.csv` is one row per source document
(`n_pages`, `n_regions`, `path_taken`, `n_rows`, `confidence`).


## Running it

```
python -m boq_coords                                    # full corpus
python -m boq_coords --limit 50                          # smoke test
python -m boq_coords --paths "Quotation PDFs/raw/.../x.pdf"
python -m boq_coords --out-dir Quotation_Data/03g_scratch # isolated test run
```

Never touches `Quotation_Data/03_structured_current/` (v1's output).


## Testing and scoring

```
python -m unittest discover -s tests -v
```

`tests/test_money.py` is pure-logic (no PDFs needed) and runs anywhere.
`tests/test_boq_coords_integration.py` exercises the full pipeline
against real corpus documents and is gated with
`@unittest.skipUnless(path.exists(), ...)` — it skips cleanly rather than
failing when the (gitignored, not-committed) source PDFs aren't present.

```
python scripts/score.py --self-consistency <items.csv>
python scripts/score.py --golden tests/golden.csv --against <items.csv>
```

`--self-consistency` needs no golden set: negative-price rate, arithmetic
sanity, and raw-text traceability, computed corpus-wide. `--golden`
reports per-field exact/wrong/missing rates against a hand-labelled
sample (`tests/golden.csv` — not committed; see `scripts/sample_golden.py`
for the layout-stratified sampling methodology used to build it).


## Known limitations

- **Broken font encoding.** At least one corpus document's embedded font
  has a missing/broken ToUnicode map — text extraction returns control
  characters even though the PDF renders correctly on screen. Distinct
  from the (separately handled) scanned/OCR-needed document class; not
  yet corpus-scanned for prevalence.
- **Implicit unit scale.** A table can print bare numbers ("168.00") that
  are only actually in Lakhs, resolvable solely via the document's own
  printed grand-total line — there is no per-row signal to catch this.
  Only a grand-total cross-check oracle (not yet built) could catch it.
- **Unpriced parts/make lists.** A table with a genuine `MAKE` column but
  no price column at all (a bill-of-materials appendix, not a priced BOQ)
  correctly doesn't qualify as a region under the current design, so its
  content isn't captured — by design, since the pipeline's job is priced
  line items, but worth naming as a gap if unpriced BOM capture is ever
  wanted.
- **Description misattribution in dense, gapless multi-item rows.** When
  two adjacent items' descriptions are equally dense with no larger gap
  for the row-boundary heuristic to key off, a heading line can land in
  the wrong item's row. In every observed instance this affects only the
  `description` text — item numbers, prices, quantities, make, and model
  have stayed correct on the same documents.
- **Continuation page whose columns physically shift (flagged, not
  silent).** `rows._unheaded_continuation_rows` reuses the ORIGINAL
  region's column bands verbatim on an unheaded continuation page,
  assuming the same x-positions still apply. On documents where a
  "summary" table (page N, e.g. `Sr.No / Description / Qty / Unit Price /
  Total Price`) is followed by a "detailed spec" table (page N+1+) that
  crams the same fields into fewer, differently-positioned columns (e.g.
  a combined `"1 No."` qty+unit cell and a single `"Quoted"` placeholder
  covering both price columns, both physically shifted right of where the
  original bands expect them), the reused bands split the merged cell
  wrong: the leading digit of the qty+unit cell (`"1"`, `"20"`, `"80"`)
  lands in `unit_price` instead, `quantity`/`unit` come out blank, and the
  placeholder still correctly lands in `total_price`. **Confirmed
  root-caused** on `Q24S10074_VOC_GC.pdf` (page 2 → 3) and
  `Q25N10067*_Bechtel_RIL NMD*.pdf` (all 3 revisions) during a 50-document
  smoke test (2026-09-12). Measured prevalence against the existing
  `03f_structured_coords/quotation_items.csv` sample (198 documents): 35
  rows across 15 documents (7.6% of that sample) carry the exact
  signature (`PRICE_OUT_OF_RANGE:unit_price` with both `quantity` and
  `unit` blank). **Not silent** — `validate.py`'s price-bounds rule
  reliably catches the resulting sub-1000 "price" and flags
  `confidence=LOW`, so these rows are already excluded by the
  confidence-segmentation rule in `CLAUDE.md`. Fixing it at the source
  would mean re-detecting column bands per continuation page (e.g. from
  that page's own word-cluster geometry) rather than trusting the
  original region's bands to still apply — a real design change, not a
  one-line patch, and one that should be validated against the golden set
  before landing (see Future work in `PROJECT_NOTES.md`).
- **Merged/rowspan price cell in a "summary" table attributed to the
  wrong item — now flagged, not fixed at the source.** When a ruled
  table's price column is a single cell spanning several item rows (the
  document states one price once, for a small group of items, rather than
  per-row — confirmed via `page.find_tables()`'s own
  `table.rows[i].cells` structure, where the merged cell is non-`None`
  only on the first row of the span and `None` on the rows below it),
  `banded.segment_rows` still assigns the price text to whichever
  row-bucket the text's vertical centre happens to fall in — which is
  rarely the first (correct) row, since the boundary heuristic has no
  awareness that the cell is merged at all. **Confirmed root-caused** on
  `Q24S10074_VOC_GC.pdf`: the source table states one price (₹96,50,000)
  once, spanning the rows for items 1–4 ("Gas Chromatograph with SHS"
  through "Sample heat tracer line"); the pipeline attributes it entirely
  to item 3 ("Sample Probe") instead. This used to be silent
  (`confidence=HIGH`, no `validation_error`) — **fixed by adding a
  detection-only rule, not by correcting the attribution**:
  `ruled._spanned_price_ranges` reads `region.table.rows[i].cells`
  directly and flags a price cell as spanned when it is taller than
  ~1.4× the height of that SAME row's other named columns (item_no,
  description, quantity, ...) — comparing within the row, not against a
  table-wide baseline, so an ordinary row whose price genuinely wraps to
  two lines (matching that row's own equally-tall siblings) is not
  flagged. `rows_from_region` then marks every `LogicalRow` whose own
  y-range falls inside a spanned cell's range, and
  `validate.py`'s new `PRICE_CELL_SPANS_MULTIPLE_ROWS` rule turns that
  into `confidence=LOW` — purely additive, never changes any emitted
  value. Confirmed on a 50-document smoke test (2026-09-12, before/after
  the fix) to catch the known case and, after excluding a phantom
  unnamed border-column from the row baseline (a real false-positive
  found in the same smoke test — see `Q25AKIC10080_Blue
  NH3_LINDE_20012025.pdf`), not to over-flag ordinary two-line-wrapped
  prices. **The wrong-item attribution itself is not corrected** — that
  would mean redesigning row-boundary derivation to recognize spans, a
  bigger change that should wait for golden-set validation; for now the
  row is simply and reliably marked for human review instead of silently
  trusted.
