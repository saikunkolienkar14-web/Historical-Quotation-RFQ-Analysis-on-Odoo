# Selecting Few-Shot Examples From the Parser Output

## Task

Build a small set of **good** and **bad** extraction examples from the
existing regex/heuristic parser (`quotation_parser_v1.py`) output, to use
as few-shot examples for an LLM-based extraction approach:

1. Pick **good examples** — documents/items the current parser got right
   — to show the LLM the target pattern.
2. Pick **bad examples** — documents/items the parser got wrong because
   the source PDF had a different layout/pattern than the parser expects
   — these are the cases the few-shot examples need to cover.
3. Apply the few-shot prompt (built from the good examples) to the bad
   examples, and check whether the LLM now extracts them correctly.

This only needs the CSVs already produced by
`Quotation_Data/03_structured_current/` — no new script or PDF reading is
required. Column meanings referenced below are documented in full in
[`DATA_DICTIONARY.md`](DATA_DICTIONARY.md).


## Step 1 — Pick GOOD examples

Good = the parser was confident **and** nothing was flagged for review.
Use `Quotation_Data/03_structured_current/quotations.csv` and
`quotation_items.csv`, filtered like this:

**Document-level (customer/subject/number/date/totals):**
- `document_confidence == HIGH`
- `warnings` is blank
- `boq_detected == YES`

**Item-level (BOQ line items):**
- `confidence == HIGH` in `quotation_items.csv`
- description, quantity, unit, and both prices are all non-blank
- the item's `source_file` does **not** appear in `parser_review.csv`

Aim for variety, not just the highest-confidence rows — pick good
examples that cover different **patterns**, since that's what the LLM
needs to generalize from:
- a simple single-page quotation
- a multi-page quotation with a long BOQ table
- a different currency (check the `currency` column — most are INR, grab
  a USD/EUR one if any exist)
- a document where make/model/version were labeled and extracted
  (`product, make, model, version` in `quotation_items.csv`)

For each good example, open its `Text_Path` (from
`Quotation_Preprocessed/text/...` — or the cleaned version under
`Quotation_Data/03_preprocessed_text_2/` for the exact text the parser
actually ran on) alongside its row in `quotations.csv` /
`quotation_items.csv`, and confirm by eye that the extracted values
genuinely match what's in the text. Confidence scores are the parser's
own self-rating, not ground truth — a quick manual check avoids feeding
the LLM an example that "looks right" but isn't.

5–8 good examples covering different patterns is enough to start.


## Step 2 — Pick BAD examples

Bad = a different PDF pattern than the parser expects tripped it up.
`Quotation_Data/03_structured_current/parser_review.csv` is the direct
source for this — every row is something the parser already flagged.
Group by the `reason` column (codes are listed in full in
[`TROUBLESHOOTING.md`](TROUBLESHOOTING.md#10-understanding-quotation_parser_v1py-warning-codes)):

| Pattern to look for | `reason` code |
|---|---|
| No BOQ table detected at all — table header wording differs from what's expected | `BOQ_HEADER_NOT_FOUND` |
| BOQ header found, but no item rows recognized under it | `NO_ITEM_NUMBER_ROWS_FOUND` |
| An item has no price at all | `ITEM_<n>_NO_PRICE_VALUES` |
| An item has only one price value (ambiguous unit vs. total) | `ITEM_<n>_ONLY_ONE_VALUE` |
| Customer/subject line not recognized | "Customer not detected" / "Subject not detected" |
| Item number was a PO/material code or a date, not a real item number | `ITEM_<n>_INVALID_ITEM_NUMBER` |
| Quantity was actually a material code, not a real quantity | `ITEM_<n>_QUANTITY_OUT_OF_RANGE` |
| Unit spelling not in the canonical list | `ITEM_<n>_UNKNOWN_UNIT` |

Pick **one real example per pattern** you want the LLM to handle (don't
need all eight — prioritize whichever reason codes have the most rows,
since fixing those has the biggest impact). For each:

1. Note the `source_file` from `parser_review.csv`.
2. Open that document's actual text (same `Text_Path` lookup as above).
3. Find the exact spot in the text that differs from the "normal"
   pattern — a different header wording, a table with no header row, a
   label spelled differently, etc. **Copy the literal surrounding text**,
   not a paraphrase of the problem — this project's own history
   (`CHANGELOG.md` v1.3.0) shows that fixes aimed at exact wording work
   and fixes aimed at a general description of the problem measure worse.
4. Also check `Quotation_Data/07_knowledge_bank/knowledge_bank_review.csv`
   for the same document if you want downstream-impact context (e.g. did
   this document's bad extraction also cause a blank price, a missing
   customer match, etc.).

Also worth a look: `Quotation_Data/04_validation/preprocessing_report.csv`
— documents with `Warnings` like `OCR_WARNING:HIGH_SYMBOL_RATIO` or
`VERY_SHORT` are frequently the same documents that produced bad
parser output, because the bad *text* (from OCR or a scan) is often the
real root cause, not the parser logic. Worth separating these two
failure classes:
- **Parser pattern miss** — clean text, but the parser's regex/heuristics
  don't match this layout. This is what few-shot examples can fix.
- **Bad input text** — OCR garbage or a scan artifact upstream, so even a
  perfect extractor has nothing good to extract. Flag these separately;
  no amount of few-shot prompting fixes a source problem.


## Step 2.5 — Anonymize before showing anything to an LLM

The documents themselves contain real customer names, contact people,
emails, phone numbers, and commercial prices in plaintext — this is not
hypothetical, it's confirmed in the corpus. None of that should go to an
LLM, local or cloud, as-is.

Run the picked good/bad examples through
[`llm_fewshot/anonymize_for_llm.py`](../llm_fewshot/anonymize_for_llm.py)
first:

    python llm_fewshot/anonymize_for_llm.py --files "<relative path under Quotation_Data/03_preprocessed_text_2>" --with-structured

This masks the customer's name/address/contact/emails/phones and
reformats every price/quantity to a synthetic value of the same shape
(same digit count, comma grouping, `/-` marker) — so the extraction
*pattern* is preserved without the real values. Item descriptions, table
structure, and labels are left untouched (that's what the LLM needs to
learn), and Adage's own contact info is left alone too (it's the
sender's own already-external info, not the confidential part).

Read `llm_fewshot/anonymize_for_llm.py`'s own docstring for the full
list of what's masked vs. left alone, and note:
- Output files are named generically (`example_001.txt`, ...) rather
  than keeping the original filename, since this corpus's filenames
  themselves embed the customer name.
- `llm_fewshot/_local_index_DO_NOT_SHARE.csv` maps those generic names
  back to the real source files — keep that one file local, everything
  else the script writes is safe to share.
- If you use `--with-structured`, any field the script can't confidently
  remap is written as `[REDACTED]` rather than risking the real value —
  check the script's own summary output for how many rows that hit.

**Local vs. cloud LLM**: anonymize either way — a cloud vendor's data
retention policy is not a substitute for not sending the data in the
first place. If you have the option, prototype the few-shot prompt
against a local model first (e.g. via Ollama) while you're still
iterating — it removes the network-exposure question entirely. Move to
a cloud API only once the prompt is stable.


## Step 3 — Build the few-shot prompt

For each good example, pair the anonymized **input** text (or just the
relevant excerpt if the full document is long) with the **expected
output** (the anonymized values from
`llm_fewshot/anonymized_structured/quotations.csv` /
`quotation_items.csv`, in whatever structured format — JSON, table,
etc. — the LLM extraction step will use). The anonymizer maps the same
original value to the same synthetic value across both files, so the
numbers in the input text and the answer key agree with each other.

Keep the excerpt tight — the BOQ table plus a few lines of surrounding
context per example, rather than the whole document, keeps the prompt
focused on the pattern you actually want to demonstrate.


## Step 4 — Apply to bad examples and validate

Run the same prompt (good examples as few-shots) against each bad
example's text and compare the LLM's output against:
- what a human reading the document would extract (ground truth), and
- what the current parser produced (to quantify the improvement)

For each bad example, record: extracted correctly / partially / not at
all, and if not, whether the miss looks like a new pattern that needs
its own few-shot example added.


## Output of this exercise

A short table (or a CSV) with one row per selected example:

| source_file | good/bad | pattern/reason | note |
|---|---|---|---|

Keep the actual text excerpts and expected-output pairs in whatever
format the LLM prompting step needs — this note is about *how to find
the right examples*, not the prompt format itself.
