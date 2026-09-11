"""
Quotation PDF Preprocessor
===========================

Processes all quotation PDFs inside the Downloads folder.

For every PDF:

    1. Try normal PDF text extraction.
    2. Measure how much readable text was extracted.
    3. If sufficient text exists:
           classify as TEXT_BASED
           save extracted text.

    4. If little/no text exists:
           classify as IMAGE_BASED
           render pages as images
           run OCR using Tesseract
           save OCR text.

The original quotation PDFs are NEVER modified.

Example input:

Downloads/
├── Q24W10126/
│   ├── quotation1.pdf
│   └── quotation2.pdf
├── Q24W10127/
│   └── quotation.pdf
└── Q24W10128/
    └── revision.pdf

Example output:

Quotation_Preprocessed/
├── text/
│   ├── Q24W10126/
│   │   ├── quotation1.txt
│   │   └── quotation2.txt
│   └── ...
│
└── quotation_documents.csv
"""

import os
import csv
import re
from pathlib import Path

import pymupdf as fitz
import pytesseract

from PIL import Image


# ============================================================
# CONFIGURATION
# ============================================================

# Paths are anchored to this file's own location rather than to
# the working directory, so input and output always land in the
# same place regardless of where the script is run from.
#
# Actual layout — note that the source PDFs live inside the
# separate downloader project, which sits alongside this one:
#
#     Odoo_RFQ_Attachment_Downloader-main/          <- PROJECT_ROOT
#     ├── Odoo_RFQ_Attachment_Downloader-main/      <- downloader project
#     │   └── Downloads/
#     │       ├── Q24W10126/
#     │       └── Q24W10127/
#     └── Odoo_RFQ_PDF_Extracter/                   <- SCRIPT_DIR
#         ├── quotation_pdf_preprocessor.py
#         └── Quotation_Preprocessed/               <- output

SCRIPT_DIR = Path(__file__).resolve().parent

PROJECT_ROOT = SCRIPT_DIR.parent

# Change this if your quotation Downloads folder is elsewhere.
INPUT_FOLDER = str(
    PROJECT_ROOT
    / "Odoo_RFQ_Attachment_Downloader-main"
    / "Downloads"
)

# Output folder for processed quotation data
OUTPUT_FOLDER = str(SCRIPT_DIR / "Quotation_Preprocessed")

# Text files
TEXT_FOLDER = os.path.join(
    OUTPUT_FOLDER,
    "text"
)

# Master CSV
OUTPUT_CSV = os.path.join(
    OUTPUT_FOLDER,
    "quotation_documents.csv"
)

# ------------------------------------------------------------
# PROCESSING LIMIT
#
# Set to a number (e.g. 10) to process only the first N PDFs
# when testing. Set to None to process the complete dataset.
# ------------------------------------------------------------

MAX_PDFS = None

# Tesseract path
TESSERACT_PATH = (
    r"C:\Users\Sai\AppData\Local\Tesseract-OCR\tesseract.exe"
)

# ------------------------------------------------------------
# Detection threshold
#
# If a PDF produces fewer than this many characters
# from normal PDF extraction, OCR will be used.
# ------------------------------------------------------------

MIN_TEXT_LENGTH = 50

# OCR resolution
OCR_DPI = 300


# ============================================================
# TESSERACT CONFIGURATION
# ============================================================

if os.path.exists(TESSERACT_PATH):

    pytesseract.pytesseract.tesseract_cmd = (
        TESSERACT_PATH
    )

else:

    print("\nWARNING")
    print("-" * 60)
    print(
        "Tesseract was not found at:"
    )
    print(
        TESSERACT_PATH
    )
    print(
        "\nOCR will not work until "
        "TESSERACT_PATH is corrected."
    )
    print("-" * 60)


# ============================================================
# CREATE OUTPUT DIRECTORIES
# ============================================================

os.makedirs(
    TEXT_FOLDER,
    exist_ok=True
)


# ============================================================
# CLEAN TEXT
# ============================================================

def clean_text(text):
    """
    Basic cleanup of extracted text.

    We deliberately preserve:

        numbers
        units
        model numbers
        standards
        engineering symbols

    because quotation data can contain things like:

        16 bar
        250 °C
        SS316L
        ASME B16.5
        4-20 mA
    """

    if not text:
        return ""

    # Normalize line endings
    text = text.replace(
        "\r\n",
        "\n"
    )

    text = text.replace(
        "\r",
        "\n"
    )

    # Replace tabs with spaces
    text = text.replace(
        "\t",
        " "
    )

    # Remove excessive spaces
    text = re.sub(
        r"[ ]{2,}",
        " ",
        text
    )

    # Remove excessive blank lines
    text = re.sub(
        r"\n[ \t]*\n[ \t]*\n+",
        "\n\n",
        text
    )

    return text.strip()


# ============================================================
# EXTRACT TEXT FROM NORMAL PDF
# ============================================================

def extract_pdf_text(pdf_path):
    """
    Extract selectable text from a PDF.

    Returns:

        text
        page_count
    """

    document = fitz.open(
        pdf_path
    )

    page_text = []

    for page in document:

        text = page.get_text(
            "text"
        )

        if text:
            page_text.append(
                text
            )

    page_count = len(
        document
    )

    document.close()

    combined_text = "\n\n".join(
        page_text
    )

    combined_text = clean_text(
        combined_text
    )

    return (
        combined_text,
        page_count
    )


# ============================================================
# OCR ONE PAGE
# ============================================================

def ocr_page(page):
    """
    Convert a PDF page into an image
    and run Tesseract OCR.
    """

    zoom = OCR_DPI / 72

    matrix = fitz.Matrix(
        zoom,
        zoom
    )

    pixmap = page.get_pixmap(
        matrix=matrix,
        alpha=False
    )

    image = Image.frombytes(
        "RGB",
        [
            pixmap.width,
            pixmap.height
        ],
        pixmap.samples
    )

    text = pytesseract.image_to_string(
        image,
        config="--psm 6"
    )

    return text


# ============================================================
# OCR COMPLETE PDF
# ============================================================

def extract_ocr_text(pdf_path):
    """
    OCR every page of a scanned/image PDF.
    """

    document = fitz.open(
        pdf_path
    )

    page_count = len(
        document
    )

    pages = []

    for page_number, page in enumerate(
        document,
        start=1
    ):

        print(
            f"      OCR page "
            f"{page_number}/{page_count}"
        )

        try:

            text = ocr_page(
                page
            )

            if text:

                pages.append(
                    f"\n--- PAGE "
                    f"{page_number} ---\n"
                    f"{text}"
                )

        except Exception as e:

            print(
                f"      OCR error on page "
                f"{page_number}: {e}"
            )

    document.close()

    combined_text = "\n".join(
        pages
    )

    combined_text = clean_text(
        combined_text
    )

    return (
        combined_text,
        page_count
    )


# ============================================================
# CREATE OUTPUT TXT PATH
# ============================================================

def create_text_output_path(
    pdf_path
):
    """
    Preserve the quotation/RFQ folder structure.

    Example:

        Downloads/Q24W10126/quote1.pdf

    becomes:

        Quotation_Preprocessed/text/
        Q24W10126/quote1.txt
    """

    relative_path = os.path.relpath(
        pdf_path,
        INPUT_FOLDER
    )

    relative_without_extension = (
        os.path.splitext(
            relative_path
        )[0]
    )

    output_path = os.path.join(
        TEXT_FOLDER,
        relative_without_extension + ".txt"
    )

    output_directory = os.path.dirname(
        output_path
    )

    os.makedirs(
        output_directory,
        exist_ok=True
    )

    return output_path


# ============================================================
# GET QUOTATION / RFQ FOLDER
# ============================================================

def get_quotation_folder(
    pdf_path
):
    """
    Gets the first folder below Downloads.

    Example:

        Downloads/Q24W10126/quote.pdf

    returns:

        Q24W10126
    """

    relative_path = os.path.relpath(
        pdf_path,
        INPUT_FOLDER
    )

    parts = relative_path.split(
        os.sep
    )

    if len(parts) >= 2:

        return parts[0]

    return ""


# ============================================================
# PROCESS ONE PDF
# ============================================================

def process_pdf(
    pdf_path
):

    print("\n" + "=" * 70)

    print(
        f"PDF: {pdf_path}"
    )

    quotation_folder = (
        get_quotation_folder(
            pdf_path
        )
    )

    filename = os.path.basename(
        pdf_path
    )

    # --------------------------------------------------------
    # STEP 1
    # TRY NORMAL TEXT EXTRACTION
    # --------------------------------------------------------

    try:

        text, page_count = (
            extract_pdf_text(
                pdf_path
            )
        )

    except Exception as e:

        print(
            f"Normal extraction error: {e}"
        )

        text = ""
        page_count = 0

    # --------------------------------------------------------
    # STEP 2
    # DETECT PDF TYPE
    # --------------------------------------------------------

    if len(text) >= MIN_TEXT_LENGTH:

        pdf_type = (
            "TEXT_BASED"
        )

        extraction_method = (
            "DIRECT_TEXT"
        )

        status = "SUCCESS"

        print(
            "\nPDF TYPE: TEXT BASED"
        )

        print(
            "Using direct PDF text extraction."
        )

    else:

        pdf_type = (
            "IMAGE_BASED"
        )

        extraction_method = (
            "OCR"
        )

        print(
            "\nPDF TYPE: IMAGE / SCANNED"
        )

        print(
            "Using OCR..."
        )

        try:

            text, page_count = (
                extract_ocr_text(
                    pdf_path
                )
            )

            if text.strip():

                status = "SUCCESS"

            else:

                status = (
                    "OCR_NO_TEXT"
                )

        except Exception as e:

            print(
                f"OCR error: {e}"
            )

            text = ""

            status = (
                f"OCR_FAILED: {e}"
            )

    # --------------------------------------------------------
    # CLEAN TEXT
    # --------------------------------------------------------

    text = clean_text(
        text
    )

    # --------------------------------------------------------
    # SAVE TXT
    # --------------------------------------------------------

    text_path = create_text_output_path(
        pdf_path
    )

    try:

        with open(
            text_path,
            "w",
            encoding="utf-8"
        ) as file:

            file.write(
                text
            )

    except Exception as e:

        print(
            f"Could not save text file: {e}"
        )

        text_path = ""

    # --------------------------------------------------------
    # RESULT
    # --------------------------------------------------------

    print(
        f"\nQuotation Folder : "
        f"{quotation_folder}"
    )

    print(
        f"PDF Type         : "
        f"{pdf_type}"
    )

    print(
        f"Method           : "
        f"{extraction_method}"
    )

    print(
        f"Pages            : "
        f"{page_count}"
    )

    print(
        f"Characters       : "
        f"{len(text)}"
    )

    print(
        f"Status           : "
        f"{status}"
    )

    print(
        f"Text saved       : "
        f"{text_path}"
    )

    return {

        "Quotation_Folder":
            quotation_folder,

        "Filename":
            filename,

        "Original_Path":
            pdf_path,

        "Text_Path":
            text_path,

        "PDF_Type":
            pdf_type,

        "Extraction_Method":
            extraction_method,

        "Pages":
            page_count,

        "Characters":
            len(text),

        "Status":
            status,

        "Text":
            text,
    }


# ============================================================
# FIND ALL QUOTATION PDFs
# ============================================================

def find_all_pdfs():
    """
    Recursively search Downloads for PDFs.
    """

    pdf_files = []

    for root, dirs, files in os.walk(
        INPUT_FOLDER
    ):

        for filename in files:

            if filename.lower().endswith(
                ".pdf"
            ):

                pdf_files.append(
                    os.path.join(
                        root,
                        filename
                    )
                )

    return sorted(
        pdf_files
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("QUOTATION PDF PREPROCESSOR")
    print("=" * 70)

    print(
        f"\nInput:"
        f"\n{INPUT_FOLDER}"
    )

    print(
        f"\nOutput:"
        f"\n{OUTPUT_FOLDER}"
    )

    # --------------------------------------------------------
    # FIND PDFs
    # --------------------------------------------------------

    pdf_files = find_all_pdfs()

    print(
        f"\nFound {len(pdf_files)} quotation PDFs."
    )

    if not pdf_files:

        print(
            "\nNo PDF files found."
        )

        print(
            "\nCheck this folder:"
        )

        print(
            os.path.abspath(
                INPUT_FOLDER
            )
        )

        return

    # --------------------------------------------------------
    # APPLY TEST LIMIT
    # --------------------------------------------------------

    if MAX_PDFS is not None:

        pdf_files = pdf_files[:MAX_PDFS]

        print(
            f"\nTEST MODE: processing only the first "
            f"{len(pdf_files)} PDFs (MAX_PDFS={MAX_PDFS})."
        )

        print(
            "Note: this overwrites the same output CSV as a "
            "full run."
        )

    # --------------------------------------------------------
    # PROCESS
    # --------------------------------------------------------

    results = []

    for index, pdf_path in enumerate(
        pdf_files,
        start=1
    ):

        print(
            f"\n[{index}/{len(pdf_files)}]"
        )

        try:

            result = process_pdf(
                pdf_path
            )

            results.append(
                result
            )

        except Exception as e:

            print(
                f"FATAL ERROR: {e}"
            )

            results.append({

                "Quotation_Folder":
                    get_quotation_folder(
                        pdf_path
                    ),

                "Filename":
                    os.path.basename(
                        pdf_path
                    ),

                "Original_Path":
                    pdf_path,

                "Text_Path":
                    "",

                "PDF_Type":
                    "UNKNOWN",

                "Extraction_Method":
                    "FAILED",

                "Pages":
                    0,

                "Characters":
                    0,

                "Status":
                    str(e),

                "Text":
                    "",
            })

    # --------------------------------------------------------
    # WRITE MASTER CSV
    # --------------------------------------------------------

    print(
        "\nWriting quotation_documents.csv..."
    )

    csv_columns = [

        "Quotation_Folder",

        "Filename",

        "Original_Path",

        "Text_Path",

        "PDF_Type",

        "Extraction_Method",

        "Pages",

        "Characters",

        "Status",

        "Text",
    ]

    with open(
        OUTPUT_CSV,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=csv_columns
        )

        writer.writeheader()

        for result in results:

            writer.writerow(
                result
            )

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    text_based = sum(
        1
        for result in results
        if result["PDF_Type"]
        == "TEXT_BASED"
    )

    image_based = sum(
        1
        for result in results
        if result["PDF_Type"]
        == "IMAGE_BASED"
    )

    successful = sum(
        1
        for result in results
        if result["Status"]
        == "SUCCESS"
    )

    failed = len(results) - successful

    print("\n" + "=" * 70)
    print("QUOTATION PREPROCESSING COMPLETE")
    print("=" * 70)

    print(
        f"Total PDFs       : "
        f"{len(results)}"
    )

    print(
        f"Text-based PDFs  : "
        f"{text_based}"
    )

    print(
        f"Image-based PDFs : "
        f"{image_based}"
    )

    print(
        f"Successful       : "
        f"{successful}"
    )

    print(
        f"Failed           : "
        f"{failed}"
    )

    print(
        "\nMaster CSV:"
    )

    print(
        os.path.abspath(
            OUTPUT_CSV
        )
    )

    print(
        "\nExtracted text:"
    )

    print(
        os.path.abspath(
            TEXT_FOLDER
        )
    )

    print(
        "\nOriginal quotation PDFs "
        "were not modified."
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()