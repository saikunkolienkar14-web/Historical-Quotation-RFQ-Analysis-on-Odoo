# Odoo Quotation PDF Extraction Pipeline

![tests](https://github.com/saikunkolienkar14-web/Historical-Quotation-RFQ-Analysis-on-Odoo/actions/workflows/tests.yml/badge.svg)

Converts quotation PDFs downloaded from Odoo into structured, analysable
datasets — from raw PDF through OCR, cleaning, and structured line-item
extraction, to an item-level knowledge bank enriched with live Odoo
customer and industry data.

Two independent extraction engines exist side by side:

- **`quotation_parser_v1.py`** — the original, regex/heuristic parser over
  flattened page text. Parser of record; feeds the full knowledge-bank
  pipeline below (customer matching, industry proxy, item-level joins).
- **`boq_coords/`** — a coordinate-aware extractor built to fix a specific
  failure class in v1 (prose text silently turned into a fabricated
  price) by reading each PDF's own word-level geometry instead of
  flattened text. See [`docs/COORDS_EXTRACTOR.md`](docs/COORDS_EXTRACTOR.md)
  for its architecture, algorithm, and how to run/test it.

This file covers **installation and how to run the pipeline**.
For current project status, known issues, and planned work, see
[`PROJECT_NOTES.md`](PROJECT_NOTES.md).


## Pipeline

    Downloads/ (PDFs)
    ↓  quotation_pdf_preprocessor.py
    Quotation_Preprocessed/text/            .txt + quotation_documents.csv
    ↓  preprocess_quotation_text.py
    Quotation_Data/02_clean_text/
    ↓  remove_repeated _messagev2.py
    Quotation_Data/03_preprocessed_text_2/
    ↓  quotation_parser_v1.py
    Quotation_Data/03_structured_current/   quotations.csv, quotation_items.csv
    ↓  odoo_export/export_customers.py      (live Odoo XML-RPC, read-only)
    Quotation_Data/05_odoo_export/          sale_orders.csv, res_partners.csv
    ↓  odoo_export/customer_industry_proxy.py
    Quotation_Data/05_odoo_export/customer_industry_proxy.csv
    ↓  odoo_match_customer/match_customers.py
    Quotation_Data/06_customer_matching/    customer_enriched.csv, customer_review.csv,
                                            customer_knowledge_bank.csv
    ↓  knowledge_bank/build_knowledge_bank.py
    Quotation_Data/07_knowledge_bank/       knowledge_bank_items.csv,
                                            knowledge_bank_review.csv,
                                            knowledge_bank_summary.txt
    ↓  knowledge_bank/build_quotation_bank.py
    Quotation_Data/07_knowledge_bank/       knowledge_bank_quotations.csv,
                                            knowledge_bank_quotations_summary.txt

`validate_quotations.py` runs alongside stage 2's output as a
profiling/QA step rather than a pipeline stage, producing
`Quotation_Data/04_validation/`.

No stage modifies its input files. `odoo_export/` only ever reads from
Odoo — no create/write/unlink calls exist anywhere in it.


## Installation

Create and activate a virtual environment:

    python -m venv venv
    .\venv\Scripts\activate

Install dependencies:

    python -m pip install -r requirements.txt

### Tesseract OCR

Tesseract is not a Python package and must be installed separately. The
pipeline expects it at:

    C:\Users\Sai\AppData\Local\Tesseract-OCR\tesseract.exe

This path is machine-specific — change `TESSERACT_PATH` in
`quotation_pdf_preprocessor.py` if yours differs. Verify the install:

    & "C:\Users\Sai\AppData\Local\Tesseract-OCR\tesseract.exe" --version

### Odoo credentials

The live export stage reads credentials from `odoo_export/.env`, which is
gitignored and never committed. Copy the template and fill it in:

    copy odoo_export\.env.example odoo_export\.env

Confirm the connection and inspect a model's real field names with:

    cd odoo_export
    python apicheck.py sale.order


## Running the pipeline

Run the stages in order from the project root:

    python quotation_pdf_preprocessor.py
    python preprocess_quotation_text.py
    python "remove_repeated _messagev2.py"
    python validate_quotations.py            # optional QA pass
    python quotation_parser_v1.py

    cd odoo_export
    python export_customers.py
    python customer_industry_proxy.py
    cd ..

    python odoo_match_customer\match_customers.py > run_match.log 2>&1
    python knowledge_bank\build_knowledge_bank.py > run_kb.log 2>&1
    python knowledge_bank\build_quotation_bank.py > run_qb.log 2>&1

Each script prints its own input/output paths and a summary when run.
The last three stages can print customer names in their progress output,
so their stdout is redirected to a log file rather than a shared
terminal/chat, per the confidentiality convention in `CLAUDE.md`.

### Testing on a subset

To validate extraction before committing to the full corpus, set the
limit in `quotation_pdf_preprocessor.py`:

    MAX_PDFS = 10      # process only the first 10 PDFs
    MAX_PDFS = None    # process the complete dataset (default)

Note that a limited run writes to the same output CSV as a full run, so
re-run without the limit before continuing down the pipeline.


## Coordinate-aware extraction (`boq_coords/`)

An independent, second extraction path — see
[`docs/COORDS_EXTRACTOR.md`](docs/COORDS_EXTRACTOR.md) for why it exists
and how it works. Quick start:

    python -m boq_coords --limit 50          # smoke test on 50 documents
    python -m boq_coords                     # full corpus
    python -m unittest discover -s tests -v  # test suite

Writes to `Quotation_Data/03f_structured_coords/` — never touches
`03_structured_current/` (v1's output).


## Extraction methods

The first stage auto-detects each PDF's type and picks a method.

**Text-based PDFs** — direct extraction via PyMuPDF. Faster, and it
preserves the original digital text better than OCR.

**Image-based / scanned PDFs** — if fewer than `MIN_TEXT_LENGTH` (50)
characters are recovered directly, pages are rendered at 300 DPI and
passed through Tesseract OCR.

OCR quality depends on scan resolution, clarity, orientation, font,
table structure, and handwriting. Text from scanned PDFs should be
validated before downstream use — `validate_quotations.py` exists for
exactly this.


## Input and output

Input is a `Downloads/` directory searched recursively; individual PDF
paths are never required. It lives inside the separate downloader
project that sits alongside this one:

    Odoo_RFQ_Attachment_Downloader-main/          <- repo root
    ├── Odoo_RFQ_Attachment_Downloader-main/      <- downloader project
    │   └── Downloads/
    │       ├── 25AKAI001/
    │       │   ├── quotation1.pdf
    │       │   └── quotation2.pdf
    │       └── 25AKAI002/
    │           └── quotation3.pdf
    └── Odoo_RFQ_PDF_Extracter/                   <- this project

The nested repeat of the folder name is intentional, not a typo. Both
paths are anchored to the script's own location, so the pipeline can be
run from any working directory. Change `INPUT_FOLDER` in
`quotation_pdf_preprocessor.py` if your PDFs live elsewhere.

The output preserves the quotation folder structure:

    Quotation_Preprocessed/
    ├── text/
    │   ├── 25AKAI001/
    │   │   ├── quotation1.txt
    │   │   └── quotation2.txt
    │   └── 25AKAI002/
    │       └── quotation3.txt
    └── quotation_documents.csv

The TXT files hold the document text; the CSV is a metadata index that
lets the corpus be loaded and analysed programmatically. Original PDFs
are never modified.

Full schemas for every CSV the pipeline produces are in
[`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md).


## Dependencies

`PyMuPDF`, `pytesseract`, and `Pillow` for extraction and OCR;
`python-dotenv` for Odoo credentials; `pandas` for the knowledge bank
stage. Every other stage uses the standard library's `csv` module alone.


## Documentation

| Document | Contents |
|---|---|
| [`PROJECT_NOTES.md`](PROJECT_NOTES.md) | Project status, known issues, future work |
| [`CHANGELOG.md`](CHANGELOG.md) | Version history and fix rationale |
| [`docs/COORDS_EXTRACTOR.md`](docs/COORDS_EXTRACTOR.md) | `boq_coords/` architecture, algorithm, schema, known limitations |
| [`docs/EXTRACTION_WORKFLOW.md`](docs/EXTRACTION_WORKFLOW.md) | Stage-by-stage workflow detail (v1 pipeline) |
| [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md) | Column-level schema for every output CSV |
| [`docs/OUTPUT_FORMAT.md`](docs/OUTPUT_FORMAT.md) | Output directory layout and file formats |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Common errors and parser warning codes |
| [`docs/FEWSHOT_EXAMPLE_SELECTION.md`](docs/FEWSHOT_EXAMPLE_SELECTION.md) | How to pick good/bad parser examples for LLM few-shot prompting |

## Note on data

Real client quotation PDFs, hand-labelled test fixtures derived from
them, and any Odoo customer export are **not included in this
repository** — they're commercially sensitive and excluded via
`.gitignore`. Every script here is written to point at that data via a
local path (`Downloads/`, `Quotation PDFs/`, `odoo_export/.env`); bring
your own corpus to run the pipeline end to end. `boq_coords`'s pure-logic
unit tests (`tests/test_money.py`) run with no data present at all.
