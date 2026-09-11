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

Every row is checked against six rules; a failing row is never dropped —
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


## Output schema

`Quotation_Data/03f_structured_coords/quotation_items.csv` — 16 columns:

`source_file, source_path, quotation_number, item_no, description, make,
model, quantity, unit, unit_price_raw, unit_price, total_price_raw,
total_price, currency, raw_row_text, confidence, validation_error`

`*_raw` columns preserve the exact source text; the parsed numeric
columns are blank (not guessed) whenever the form doesn't hold. `product`
and `version` were considered and dropped — this corpus never labels
either field inline (only `"Make:"`/`"Model:"`), so both columns would be
permanently empty.

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
