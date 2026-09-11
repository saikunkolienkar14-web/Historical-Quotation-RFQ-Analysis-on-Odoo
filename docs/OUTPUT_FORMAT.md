# Output Format

## Directory

    Quotation_Preprocessed/

Contains all generated preprocessing outputs.


## Text Directory

    Quotation_Preprocessed/text/

Contains one `.txt` file corresponding to each processed PDF.


## TXT Files

Each TXT file contains machine-readable text extracted from the corresponding quotation PDF.

For example:

    quotation.pdf

becomes:

    quotation.txt


## Page Markers

The extractor adds page markers:

    --- PAGE 1 ---

    [page text]


    --- PAGE 2 ---

    [page text]


This allows page-level analysis later.


## CSV

    quotation_documents.csv

The CSV is an index of all processed quotation PDFs.


## CSV Fields

### Quotation_Folder

Original quotation/RFQ folder containing the PDF.


### Filename

Original PDF filename.


### Original_Path

Path to the original PDF.


### Text_Path

Path to the generated TXT file.


### PDF_Type

Indicates whether the document was classified as:

TEXT_BASED

or:

IMAGE_BASED


### Extraction_Method

Indicates the method used:

DIRECT_TEXT

or:

OCR


### Pages

Number of pages in the PDF.


### Characters

Number of characters extracted.


### Status

Processing result.

Example:

SUCCESS


### Text

The extracted text.

This field allows the CSV to be directly loaded into Pandas for analysis.

For very large datasets, the TXT files should be treated as the primary document storage and the CSV as the metadata/index.


## Downstream Pipeline Output (Quotation_Data/)

Everything below `Quotation_Data/` is produced by the preprocessing
pipeline (`preprocess_quotation_text.py` onward) and never modifies the
`Quotation_Preprocessed/` output above it.

    Quotation_Data/
    ├── 02_clean_text/            cleaned TXT (preprocess_quotation_text.py)
    ├── 03_preprocessed_text_2/   boilerplate stripped (remove_repeated _messagev2.py)
    ├── 03_structured_current/    current parser output (quotation_parser_v1.py):
    │   ├── quotations.csv
    │   ├── quotation_items.csv
    │   ├── parser_review.csv
    │   └── parser_summary.txt
    ├── 04_validation/            profiling/QA report (validate_quotations.py):
    │   ├── preprocessing_report.csv
    │   ├── duplicates.csv
    │   └── validation_summary.txt
    ├── 05_odoo_export/           live, read-only Odoo export (odoo_export/):
    │   ├── sale_orders.csv
    │   ├── res_partners.csv
    │   └── customer_industry_proxy.csv
    ├── 06_customer_matching/     Odoo customer match + enrichment
    │                              (odoo_match_customer/match_customers.py):
    │   ├── customer_enriched.csv
    │   ├── customer_review.csv
    │   ├── customer_knowledge_bank.csv
    │   └── customer_name_overrides.csv  (human corrections, if any)
    └── 07_knowledge_bank/        item-level knowledge bank
                                   (knowledge_bank/build_knowledge_bank.py):
        ├── knowledge_bank_items.csv
        ├── knowledge_bank_review.csv
        └── knowledge_bank_summary.txt

`03_structured_current` is the canonical, always-current parser output -
when the parser is re-run after a fix, this folder is what gets updated
(older numbered/test variants of this folder are scratch output from
development and are not part of the pipeline). See `docs/DATA_DICTIONARY.md`
for every CSV's exact schema and `docs/EXTRACTION_WORKFLOW.md` for what
each stage does.

`05_odoo_export/sale_orders.csv` carries
`x_studio_internal_rfq_assignment_number` - the field the customer
matcher exact-matches `quotation_number` against, which is the primary
matching method as of `CHANGELOG.md` v1.7.0 (fuzzy name-matching is the
fallback). If this column is missing or stale, matching silently degrades
to fuzzy-only, so re-run `odoo_export/export_customers.py` after any
Odoo-side change.

`06_customer_matching` is the customer-level "knowledge bank" (see
`CHANGELOG.md` v1.6.0-v1.7.0) - `customer_knowledge_bank.csv` is one row
per matched Odoo customer, not per quotation.

`07_knowledge_bank` is the **item-level** knowledge bank (`CHANGELOG.md`
v1.8.0) - one row per historical quotation line item (38,487), plus one row
per Odoo order that no parsed document resolved to (3,186), separated by a
`data_source` column. This is the table the analytics layer will run on;
always filter on `data_source` and check `price_basis` / `date_source`
before treating a price or date as reported rather than derived.