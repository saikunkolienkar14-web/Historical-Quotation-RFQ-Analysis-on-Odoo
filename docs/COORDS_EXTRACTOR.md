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

Header cells are matched against `vocab.BOQ_HEADER_ALIASES` by longest
alias. A period is read both as a word break and as nothing, so a glued
`SR.NO.` (→ "sr no") and an undotted `SNO` both match the item-number
alias `s.no`; before 2026-09-23 only the second reading existed and ~700
documents with a glued `SR.NO.` header got no item-number column at all.
`No.of Qty` / `No. of Units` match the `quantity` alias `no of`, which
outranks item_no's bare `no`.

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
as a last resort. When that last resort is used, the first row still opens
at the region's own top, so content above the first price line is never
dropped.

**Two layouts.** Item-number anchors are read one of two ways, chosen per
table (`banded.detect_layout`, stored on `TableRegion.layout`):

| Layout | Where the number sits | Example | Boundary rule |
|---|---|---|---|
| `TOP` (default) | on or beside the item's own heading line | Q24X10030 GC table | description-gap boundaries above, plus the two TOP repair passes |
| `CENTRED` | vertically centred with its price in a tall cell; heading lines are *above* it | Q2501N005 | `_derive_boundaries_centred` — one block per anchor, placed so each anchor is nearest its block's centre, preferring cuts at wider gaps |

`CENTRED` is only chosen on positive evidence, on the page carrying the
table's header: at least two description lines above the first anchor,
most anchors carrying their own price on or next to their line, anchors
not mostly opening a paragraph followed by body text, and (with ≥ 2
anchors) a good centred fit. Unheaded continuation pages inherit the
layout, but re-check the last three signals themselves and fall back to
`TOP` when they fail — a document can mix conventions. In `CENTRED`
tables, unnumbered price lines also act as block centres (an unnumbered
sub-item's price is centred in its own cell), page-frame-only ruling
lines are ignored, and neither TOP repair pass runs (both would cut the
heading off an item). Anything classified `TOP` runs exactly the code
that existed before the split.

**The structural fix for the known bug class**: a word only stays in a
price band if that band's *entire line* parses as money or a recognized
placeholder (`QUOTED`, `Inclusive`, `TBD`, ...) — otherwise the whole line
reverts to description. This is what stops prose from ever reaching a
price field, independent of what a number parser alone would do with it.

A row with a description but no item number and no price merges into the
preceding row (bundled-lot convention), capped and stopped at any
`STOP_SECTION_MARKERS` line so a merge can't run into an unrelated
document section.

**Ruling detection (`ruled._ruling_line_ys`)** looks for two drawing
primitives, not just lines: an actual horizontal line segment, and a
filled rectangle no taller than a hairline (some templates — e.g.
Q25X10031's "Price Summary Sheet" — draw every row divider this way,
never as a line). A candidate is grouped with others at the same y first;
several short, disjoint marks there (separate underlines drawn under
separate cell values on one line) are never treated as one continuous
rule, and a mark reaching only the *description* column's own width is
trusted only once it repeats several times on the page — a single such
mark is indistinguishable from a decorative underline under one heading.

When real per-row rulings are found (not just the table's own outer
frame), item-number anchors still take priority as the boundary source
whenever there are ≥ 2 of them: a ruled table's physical rows don't
always match its logical items 1:1 (some templates rule every printed
line), and the anchor/gap path together with the two TOP repair passes is
what reconciles that. Ruling only becomes the direct boundary source when
there are too few reliable anchors — the confirmed real case is an
unheaded continuation page's tail content. A row built directly from a
real ruling is tagged `RULED_ROW_FLAG`, which only exempts it from the
leading-line reattach repair pass (its own top edge is already ground
truth); it still goes through the split and bundled-lot merge passes like
any other row.

**Where an unheaded continuation page's own content ends
(`rows._unheaded_continuation_rows`)**: the generic word-extraction range
runs all the way to the page's own footer margin by default, which two
confirmed real-corpus cases land inside of and corrupt:

- A company address/letterhead footer block sitting inside that generic
  range whose words happen to fall in the gap between two real columns
  (item_no/description), collapsing that gap and folding the whole
  description column into item_no — every row on the page lost its
  description entirely (Q24S10074).
- Narrative text below the real table on the same page (a "Notes &
  Clarifications" / "Exclusions" section, or an entirely different,
  unrelated table) read as more table rows, including that text's own
  numbered lines misread as item numbers (Q25X10031R1; Q24X10030's own
  "Section-3 Clarifications & Deviations" compliance/deviation matrix).

Two independent narrowing passes run before this range is used, in
order: `rows._continuation_table_bbox` shrinks the range to wherever
`page.find_tables()` finds a ruled grid overlapping the parent table's
own column range (picking the *tallest* such grid when there's more than
one — a one-row letterhead banner table can sit inside that column range
purely by x-coincidence, confirmed on Q25AKIC10080); then
`rows._first_heading_y` scans the words directly for the first line that
reads as a post-table heading (`vocab.is_post_table_heading_line`) and
drops everything at or after it, independent of whatever bbox produced
those words — pymupdf's own table detection can itself overshoot past
the real table into trailing narrative (confirmed on Q24X10030, whose
`find_tables()` bbox on the affected page ran hundreds of points past
its last real item).

A numbered heading ("Section 2:", the glued "Section-3" form too) is
**never** trusted as a stop signal by its number alone — only the words
that follow it decide, checked against a short phrase list
(`vocab.POST_TABLE_HEADING_PHRASES`) plus the existing
`STOP_SECTION_MARKERS`. Confirmed real-corpus reason: "SECTION 2:
TECHNICAL LITERATURE" is a running section *title* on some templates
(Q24S10074, and others sharing its layout), with more real priced rows
resuming right after it on the very next page — treating the bare
number as a stop signal there discarded all of them. A continuation page
that returns nothing (no money at all, or its own content is entirely a
post-table heading) ends that document's continuation-page chain
immediately, with no tolerance for "try the next page anyway": a real
per-item divider page (more of the same table resuming right after it)
would benefit from that tolerance, but it was confirmed to also bridge
straight into a *different*, unrelated table occupying the rest of the
document and fold its rows in as if they were more BOQ items — a
worse outcome than losing the legitimate divider case, with no cheap
signal available yet to tell the two apart.

**Fields with nowhere to land (`__main__.process_document`)**: a table
column `find_header()` never mapped to any of the 17 known fields (e.g. a
"Tag No" column) lands in the row's unclassified `other` band and is
otherwise lost. When a row's real description ends up blank because of
this, its `other` text becomes `description_full`/`product_name` instead
— but only when it reads like a real identifier (has both a letter and a
digit; confirmed real-corpus false case: a stray "q )" glyph fragment
landing in the same band on an unrelated row must stay blank, not be
mistaken for a description) — and the row is flagged
`DESCRIPTION_FROM_UNLABELLED_COLUMN` so this is visible downstream. Two
further per-row flags catch content that was never a priced line item at
all: `TOTAL_ROW` (the row's only real content is a bare running-total
label — "TOTAL", "Grand Total" — next to a total figure, checked as an
exact match so a real item whose own heading merely *starts* with
"Total" is never caught) and `NO_ITEM_CONTENT` (no item number, no price
of any kind, and next to nothing else to call content — stray page
noise, e.g. a lone stray glyph). None of these rows are dropped; they
surface with `confidence=LOW` and the matching `validation_error`, same
as every other validation rule.

### Money grammar (`money.py`)

Fully anchored (`^...$`, never `re.search` over prose), no minus sign in
the grammar at all, Indian (`45,00,000`) and Western (`4,500,000`) comma
grouping both validated, Lakh/Crore scale words, currency detection
(INR/USD/SAR/EUR/GBP), and plausibility bounds v1 never applied to price
(`1,000 ≤ total ≤ 5,000,000,000`, `1,000 ≤ unit ≤ 500,000,000`) plus an
arithmetic cross-check (`qty × unit_price ≈ total_price`).

### Validation (`validate.py`)

Every row is checked against these rules; a failing row is never dropped —
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
| Continuation bands reinferred | `CONTINUATION_BANDS_REINFERRED` — the row came from an unheaded continuation page whose column bands were freshly inferred, not reused from the parent table's header |
| Description from unlabelled column | `DESCRIPTION_FROM_UNLABELLED_COLUMN` — `description_full`/`product_name` came from a table column with no known field of its own (`other`), not a real description cell |
| Total row | `TOTAL_ROW` — the row's only real content is a bare running-total label ("TOTAL", "Grand Total") next to a total figure, not a priced line item |
| No item content | `NO_ITEM_CONTENT` — no item number, no price of any kind, and next to nothing else to call content: stray page noise |


## Output schema

`Quotation_Data/03f_structured_coords/quotation_items.csv` — 23 columns:

`source_file, source_path, quotation_number, row_seq, item_no,
parent_item_no, item_level, product_name, description_full, make, model,
quantity, unit, unit_price_raw, unit_price, total_price_raw, total_price,
total_price_source, price_status, currency, raw_row_text, confidence,
validation_error`

`row_seq` is the row's own 1-based position in this document's extraction
order (across all of its tables) — a unique within-`source_path` key even
when `item_no` is blank.

`item_no` is **text**, exactly as printed in the source (`"1"`, `"1.1"`,
`"4a"`) — `"1.1"` is a two-level item marker, not the number 1.1. It is
**blank** when the source row prints no item number of its own (e.g. a
continuation or bundled-lot row) — never backfilled with `row_seq` or any
other position-derived guess (a sequential-index fallback here was
confirmed to fabricate a different item number than the one actually
printed, purely from the row's position in a given extraction run — see
PROJECT_NOTES.md). `parent_item_no` (text, blank at top level) and
`item_level` (1 = top level, 2 = sub-item, 0 = no item number at all)
expose that hierarchy explicitly, via `fields.derive_item_hierarchy`. CSV
carries no types, so **read `item_no`/`parent_item_no` as strings** (e.g.
pandas `read_csv(dtype=str)`); otherwise they are coerced to floats and
`"1.10"` collapses onto `"1.1"`.

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

`tests/test_boq_snapshots.py` re-extracts every document in
`tests/snapshots/boq_rows_snapshot.json` and requires every row and field
to match exactly. Only write a snapshot from output a human has verified:

```
python scripts/snapshot_rows.py --out tests/snapshots/boq_rows_snapshot.json "<pdf>" ...
```

It holds real descriptions and prices, so `tests/snapshots/` is gitignored
(currently: Q24X10030 base/R1/R2, verified 2026-09-23).

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

- **A running section title can glue onto the last real row before it.**
  "SECTION 2: TECHNICAL LITERATURE"-style titles are deliberately never
  treated as a stop signal (see "Where an unheaded continuation page's
  own content ends" above — the bare number is untrustworthy, and this
  exact phrase means "more pricing follows," not "table ended," on the
  templates it's confirmed on). Ordinary bundled-lot merging then folds
  it, as unnumbered trailing text, into the last priced row above it
  (confirmed real-corpus case: `Q24W10129R1_TCE_RIL DMD_Chloro Alkali
  package.pdf`'s item 8 description ends with this title text). Cosmetic
  only — no price, item number, or quantity is affected — but not yet
  cleaned up.
- **A page that contributes nothing ends a document's continuation-page
  chain immediately, with no gap tolerance.** A genuine same-table
  divider page (no rows of its own, more of the SAME table resuming on
  the very next page) is lost entirely rather than bridged over.
  Deliberately not tolerated: doing so was confirmed to also bridge into
  a *different*, unrelated table occupying the rest of a document
  (`Q24X10030 EM Singapore Jurong.pdf`'s own "Section-3 Clarifications &
  Deviations" compliance/deviation matrix) and fold its rows in as if
  they were more BOQ items — a worse outcome (fabricated item numbers,
  wrong prices) than losing the legitimate divider case. No cheap signal
  is available yet to tell the two apart (both open directly on a
  heading with nothing real before it on the page).

- **Item number centred high in its block.** A cell whose number sits
  well above its block's middle (e.g. Q2512E16 item 1) fits neither
  layout rule; it stays `TOP` and can split one fragment row of bullets
  off the item.

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
- **Continuation page whose columns physically shift — fixed 2026-09-18.**
  `rows._unheaded_continuation_rows` used to reuse the ORIGINAL region's
  column bands verbatim on an unheaded continuation page, assuming the
  same x-positions still applied. On documents where a "summary" table
  (page N, e.g. `Sr.No / Description / Qty / Unit Price / Total Price`)
  is followed by a "detailed spec" table (page N+1+) that crams the same
  fields into fewer, differently-positioned columns (e.g. a combined
  `"1 No."` qty+unit cell and a single `"Quoted"` placeholder covering
  both price columns), the reused bands split the merged cell wrong: the
  leading digit landed in `unit_price`, `quantity`/`unit` came out blank.
  **Confirmed root-caused** on `Q24S10074_VOC_GC.pdf` (page 2 → 3) and
  `Q25N10067*_Bechtel_RIL NMD*.pdf` (all 3 revisions).

  **Fix:** `columns.bands_capture_price()` first checks whether the
  reused bands' price field(s) already parse as money on this page's own
  words - if so, they're left alone (confirmed necessary: unconditionally
  reinferring even when the reused bands already work regressed a
  previously-correct document, `Q2501N005` - see that function's
  docstring). Only when the reused bands demonstrably don't fit does
  `columns.infer_bands_from_words()` run: it clusters words by x-gap
  significance (not a fixed column count - the real bug shape is fewer,
  compressed columns, not just shifted ones) and classifies each cluster
  by its own content (money-parsing → a price column;
  `money.parse_quantity`-parsing, which already recognizes an embedded
  unit like `"1 No."` → quantity) rather than by position alone. Returns
  `None` — never a confident-but-wrong guess — whenever no cluster
  classifies as a price column but one was expected; the caller falls
  back to the reused bands exactly as before the fix.

  Measured on a 200-document sample (2026-09-18, before → after this
  fix): `PRICE_OUT_OF_RANGE` rows 110 → 103, documents affected 17 → 11
  (9.2% → 6.0% of documents with a table). The new
  `CONTINUATION_BANDS_REINFERRED` flag fired on 670 rows across 45
  documents (22.7% of the sample) - real quantity/unit/price recovered
  on rows that previously had none, not just fewer flags; total
  extracted rows rose 2,226 → 2,374 as previously merged/dropped items
  split out correctly. Self-consistency held (0% negative price, 100%
  raw-text traceability, arithmetic-ok on checkable rows 98.0%, up from
  ~97%). Confirmed via a real-document regression test,
  `tests/test_price_bug_fixes.py`.
- **Merged/rowspan price cell in a "summary" table attributed to the
  wrong item — detection landed 2026-09-12, corrected 2026-09-18.** When
  a ruled table's price column is a single cell spanning several item
  rows (the document states one price once, for a small group of items,
  rather than per-row — confirmed via `page.find_tables()`'s own
  `table.rows[i].cells` structure, where the merged cell is non-`None`
  only on the first row of the span and `None` on the rows below it),
  `banded.segment_rows` still assigns the price text to whichever
  row-bucket the text's vertical centre happens to fall in — which is
  rarely the first (correct) row, since the boundary heuristic has no
  awareness that the cell is merged at all. **Confirmed root-caused** on
  `Q24S10074_VOC_GC.pdf`: the source table states one price (₹96,50,000)
  once, spanning the rows for items 1–4 ("Gas Chromatograph with SHS"
  through "Sample heat tracer line"); the pipeline attributes it entirely
  to item 3 ("Sample Probe") instead.

  `ruled._spanned_price_ranges` reads `region.table.rows[i].cells`
  directly and flags a price cell as spanned when it is taller than
  ~1.4× the height of that SAME row's other named columns (item_no,
  description, quantity, ...) — comparing within the row, not against a
  table-wide baseline, so an ordinary row whose price genuinely wraps to
  two lines (matching that row's own equally-tall siblings) is not
  flagged. This detection landed 2026-09-12 as flag-only
  (`confidence=LOW`, `PRICE_CELL_SPANS_MULTIPLE_ROWS`) — the wrong-item
  attribution itself was left uncorrected, so a plausible-but-wrong
  number still reached `unit_price`/`total_price`.

  **Fixed 2026-09-18:** `__main__.py` now withholds the numeric
  `unit_price`/`total_price` on any row `_spanned_price_ranges` flags -
  the raw text stays (`unit_price_raw`/`total_price_raw`, per this
  project's "raw stays beside derived" rule) on whichever row the
  y-centre bucketing actually attributed it to, but the derived number is
  never trusted on a row this uncertain. **No redistribution of the
  spanned total across the group's items is attempted** - no precedent
  for that in this codebase, and splitting a stated price would fabricate
  a number the document never wrote; a flagged row is honestly priceless,
  not silently wrong. Confirmed on the 200-document sample (2026-09-18):
  all 31 flagged rows across 6 documents now have both numeric price
  fields blank. See `tests/test_price_bug_fixes.py`.
