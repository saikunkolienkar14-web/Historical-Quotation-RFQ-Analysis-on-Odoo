"""
Quotation Text Validation and Profiling
=======================================

Purpose:
    Validate and profile the cleaned quotation TXT dataset before
    structured data extraction.

Input:
    Quotation_Data/02_clean_text/

Output:
    Quotation_Data/04_validation/
        preprocessing_report.csv
        duplicates.csv
        validation_summary.txt

The script DOES NOT modify the input TXT files.
"""

import csv
import hashlib
import re
from pathlib import Path
from collections import Counter


# ============================================================
# CONFIGURATION
# ============================================================

# Change this only if your folder structure is different.
INPUT_FOLDER = Path("Quotation_Data/02_clean_text")

OUTPUT_FOLDER = Path("Quotation_Data/04_validation")

REPORT_FILE = OUTPUT_FOLDER / "preprocessing_report.csv"
DUPLICATES_FILE = OUTPUT_FOLDER / "duplicates.csv"
SUMMARY_FILE = OUTPUT_FOLDER / "validation_summary.txt"


# ============================================================
# THRESHOLDS
# ============================================================

# Documents below this number of characters will be flagged.
VERY_SHORT_CHAR_THRESHOLD = 100

# Documents below this number of words will be flagged.
VERY_SHORT_WORD_THRESHOLD = 20

# Extremely large documents will be flagged for inspection.
VERY_LARGE_CHAR_THRESHOLD = 500_000

# OCR suspicion thresholds.
MIN_TEXT_FOR_OCR_CHECK = 100

# Percentage of non-alphanumeric characters considered suspicious.
SUSPICIOUS_SYMBOL_RATIO = 0.35


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def calculate_md5(text):
    """
    Generate an MD5 hash of normalized text.

    Used only to detect exact duplicate TXT documents.
    """

    normalized = text.strip().replace("\r\n", "\n").replace("\r", "\n")

    return hashlib.md5(
        normalized.encode("utf-8", errors="ignore")
    ).hexdigest()


def count_words(text):
    """
    Count words using a conservative regex.
    """

    words = re.findall(r"\b\w+\b", text, flags=re.UNICODE)

    return len(words)


def count_lines(text):
    """
    Count non-empty lines.
    """

    return sum(
        1
        for line in text.splitlines()
        if line.strip()
    )


def count_pages(text):
    """
    Estimate page count from common page separators.

    This does not assume a particular exact separator format.
    """

    patterns = [
        r"---\s*PAGE\s*\d+\s*---",
        r"===\s*PAGE\s*\d+\s*===",
        r"\[\s*PAGE\s*\d+\s*\]",
        r"PAGE\s+\d+",
    ]

    matches = []

    for pattern in patterns:

        found = re.findall(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        matches.extend(found)

    if matches:

        return len(matches)

    # If no explicit page separator exists,
    # treat the document as at least one page.
    if text.strip():

        return 1

    return 0


def calculate_symbol_ratio(text):
    """
    Calculate the proportion of unusual symbols.

    This is only a warning signal and should NOT be treated
    as proof that OCR is bad.
    """

    if not text.strip():

        return 0.0

    relevant_chars = [
        char
        for char in text
        if not char.isspace()
    ]

    if not relevant_chars:

        return 0.0

    suspicious = 0

    for char in relevant_chars:

        if (
            not char.isalnum()
            and char not in
            ".,;:!?+-*/%()[]{}<>_=|&@#$₹€£$'\"/\\"
        ):
            suspicious += 1

    return suspicious / len(relevant_chars)


def calculate_alpha_ratio(text):
    """
    Percentage of characters that are alphabetic.
    """

    characters = [
        char
        for char in text
        if not char.isspace()
    ]

    if not characters:

        return 0.0

    alpha = sum(
        1
        for char in characters
        if char.isalpha()
    )

    return alpha / len(characters)


def detect_ocr_warning(text):
    """
    Detect simple suspicious OCR characteristics.

    This is deliberately conservative.
    """

    warnings = []

    stripped = text.strip()

    if not stripped:

        return "EMPTY"

    if len(stripped) < MIN_TEXT_FOR_OCR_CHECK:

        return "VERY_SHORT"

    symbol_ratio = calculate_symbol_ratio(stripped)

    if symbol_ratio >= SUSPICIOUS_SYMBOL_RATIO:

        warnings.append("HIGH_SYMBOL_RATIO")

    alpha_ratio = calculate_alpha_ratio(stripped)

    if alpha_ratio < 0.15:

        warnings.append("LOW_ALPHABETIC_RATIO")

    # Repeated identical characters can indicate OCR corruption.
    repeated_pattern = re.search(
        r"(.)\1{7,}",
        stripped
    )

    if repeated_pattern:

        warnings.append("REPEATED_CHARACTER")

    # Large sequences of random-looking single characters.
    single_char_tokens = re.findall(
        r"\b[A-Za-z]\b",
        stripped
    )

    total_words = count_words(stripped)

    if total_words > 100:

        single_ratio = (
            len(single_char_tokens) / total_words
        )

        if single_ratio > 0.25:

            warnings.append(
                "HIGH_SINGLE_CHARACTER_RATIO"
            )

    if warnings:

        return "; ".join(warnings)

    return ""


def detect_suspicious_patterns(text):
    """
    Detect common extraction problems.

    These are warnings only.
    """

    warnings = []

    if not text.strip():

        warnings.append("EMPTY_DOCUMENT")

        return warnings

    if len(text.strip()) < VERY_SHORT_CHAR_THRESHOLD:

        warnings.append("VERY_SHORT")

    if count_words(text) < VERY_SHORT_WORD_THRESHOLD:

        warnings.append("VERY_FEW_WORDS")

    if len(text) > VERY_LARGE_CHAR_THRESHOLD:

        warnings.append("VERY_LARGE")

    # Null characters should normally have been removed
    # during cleaning.
    if "\x00" in text:

        warnings.append("NULL_CHARACTER")

    # Excessive repeated blank lines.
    if re.search(r"\n\s*\n\s*\n\s*\n", text):

        warnings.append("EXCESSIVE_BLANK_LINES")

    # Suspicious OCR warning.
    ocr_warning = detect_ocr_warning(text)

    if ocr_warning:

        warnings.append(
            f"OCR_WARNING:{ocr_warning}"
        )

    return warnings


# ============================================================
# READ TXT FILES
# ============================================================

def find_txt_files():

    if not INPUT_FOLDER.exists():

        raise FileNotFoundError(
            f"\nInput folder not found:\n"
            f"{INPUT_FOLDER.resolve()}\n\n"
            f"Please check INPUT_FOLDER in the script."
        )

    txt_files = sorted(
        INPUT_FOLDER.rglob("*.txt")
    )

    return txt_files


# ============================================================
# MAIN VALIDATION
# ============================================================

def main():

    print("=" * 70)
    print("QUOTATION TEXT VALIDATION & PROFILING")
    print("=" * 70)

    print(
        f"\nInput folder:\n"
        f"{INPUT_FOLDER.resolve()}"
    )

    print(
        f"\nOutput folder:\n"
        f"{OUTPUT_FOLDER.resolve()}"
    )

    # --------------------------------------------------------
    # CREATE OUTPUT FOLDER
    # --------------------------------------------------------

    OUTPUT_FOLDER.mkdir(
        parents=True,
        exist_ok=True
    )

    # --------------------------------------------------------
    # FIND FILES
    # --------------------------------------------------------

    txt_files = find_txt_files()

    print(
        f"\nTXT files found : {len(txt_files)}"
    )

    if not txt_files:

        print(
            "\nNo TXT files were found."
        )

        return

    # --------------------------------------------------------
    # DATA COLLECTION
    # --------------------------------------------------------

    report_rows = []

    hash_map = {}

    total_chars = 0
    total_words = 0
    total_lines = 0
    total_pages = 0

    empty_files = 0
    very_short_files = 0
    very_large_files = 0
    suspicious_files = 0
    unreadable_files = 0

    char_counts = []
    word_counts = []
    page_counts = []

    # --------------------------------------------------------
    # PROCESS EVERY TXT
    # --------------------------------------------------------

    for index, txt_file in enumerate(
        txt_files,
        start=1
    ):

        print(
            f"[{index}/{len(txt_files)}] "
            f"{txt_file.name}"
        )

        try:

            text = txt_file.read_text(
                encoding="utf-8",
                errors="replace"
            )

        except Exception as e:

            unreadable_files += 1

            report_rows.append({
                "Filename": txt_file.name,
                "Relative_Path": str(
                    txt_file.relative_to(INPUT_FOLDER)
                ),
                "Status": "READ_ERROR",
                "Characters": "",
                "Words": "",
                "Lines": "",
                "Estimated_Pages": "",
                "MD5": "",
                "Symbol_Ratio": "",
                "Alpha_Ratio": "",
                "Warnings": f"READ_ERROR: {e}",
            })

            continue

        # ----------------------------------------------------
        # BASIC STATISTICS
        # ----------------------------------------------------

        stripped_text = text.strip()

        characters = len(stripped_text)

        words = count_words(stripped_text)

        lines = count_lines(stripped_text)

        pages = count_pages(stripped_text)

        md5_hash = calculate_md5(
            stripped_text
        )

        symbol_ratio = calculate_symbol_ratio(
            stripped_text
        )

        alpha_ratio = calculate_alpha_ratio(
            stripped_text
        )

        warnings = detect_suspicious_patterns(
            stripped_text
        )

        # ----------------------------------------------------
        # STATUS
        # ----------------------------------------------------

        if not stripped_text:

            status = "EMPTY"

            empty_files += 1

        elif warnings:

            status = "WARNING"

            suspicious_files += 1

        else:

            status = "OK"

        if characters < VERY_SHORT_CHAR_THRESHOLD:

            very_short_files += 1

        if characters > VERY_LARGE_CHAR_THRESHOLD:

            very_large_files += 1

        # ----------------------------------------------------
        # TOTALS
        # ----------------------------------------------------

        total_chars += characters
        total_words += words
        total_lines += lines
        total_pages += pages

        char_counts.append(characters)
        word_counts.append(words)
        page_counts.append(pages)

        # ----------------------------------------------------
        # DUPLICATE TRACKING
        # ----------------------------------------------------

        if md5_hash:

            hash_map.setdefault(
                md5_hash,
                []
            ).append(
                txt_file
            )

        # ----------------------------------------------------
        # REPORT ROW
        # ----------------------------------------------------

        report_rows.append({

            "Filename":
                txt_file.name,

            "Relative_Path":
                str(
                    txt_file.relative_to(
                        INPUT_FOLDER
                    )
                ),

            "Status":
                status,

            "Characters":
                characters,

            "Words":
                words,

            "Lines":
                lines,

            "Estimated_Pages":
                pages,

            "MD5":
                md5_hash,

            "Symbol_Ratio":
                round(
                    symbol_ratio,
                    4
                ),

            "Alpha_Ratio":
                round(
                    alpha_ratio,
                    4
                ),

            "Warnings":
                "; ".join(warnings),
        })

    # ========================================================
    # WRITE MAIN REPORT
    # ========================================================

    fieldnames = [

        "Filename",
        "Relative_Path",
        "Status",
        "Characters",
        "Words",
        "Lines",
        "Estimated_Pages",
        "MD5",
        "Symbol_Ratio",
        "Alpha_Ratio",
        "Warnings",
    ]

    with open(
        REPORT_FILE,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames
        )

        writer.writeheader()

        writer.writerows(
            report_rows
        )

    # ========================================================
    # FIND DUPLICATES
    # ========================================================

    duplicate_groups = {

        file_hash: paths

        for file_hash, paths
        in hash_map.items()

        if len(paths) > 1
    }

    duplicate_rows = []

    for file_hash, paths in duplicate_groups.items():

        for path in paths:

            duplicate_rows.append({

                "MD5":
                    file_hash,

                "Filename":
                    path.name,

                "Relative_Path":
                    str(
                        path.relative_to(
                            INPUT_FOLDER
                        )
                    ),

                "Duplicate_Count":
                    len(paths),
            })

    with open(
        DUPLICATES_FILE,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        duplicate_fieldnames = [

            "MD5",
            "Filename",
            "Relative_Path",
            "Duplicate_Count",
        ]

        writer = csv.DictWriter(
            file,
            fieldnames=duplicate_fieldnames
        )

        writer.writeheader()

        writer.writerows(
            duplicate_rows
        )

    # ========================================================
    # STATISTICS
    # ========================================================

    def average(values):

        if not values:

            return 0

        return sum(values) / len(values)

    def median(values):

        if not values:

            return 0

        sorted_values = sorted(values)

        n = len(sorted_values)

        middle = n // 2

        if n % 2:

            return sorted_values[middle]

        return (
            sorted_values[middle - 1]
            +
            sorted_values[middle]
        ) / 2

    # ========================================================
    # WARNING COUNTS
    # ========================================================

    warning_counter = Counter()

    for row in report_rows:

        warning_text = row.get(
            "Warnings",
            ""
        )

        if warning_text:

            for warning in warning_text.split(";"):

                warning = warning.strip()

                if warning:

                    warning_counter[
                        warning
                    ] += 1

    # ========================================================
    # WRITE SUMMARY
    # ========================================================

    with open(
        SUMMARY_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        file.write(
            "QUOTATION TEXT VALIDATION SUMMARY\n"
        )

        file.write(
            "=" * 70
            + "\n\n"
        )

        file.write(
            "INPUT\n"
        )

        file.write(
            f"Input folder: "
            f"{INPUT_FOLDER.resolve()}\n\n"
        )

        file.write(
            "OUTPUT\n"
        )

        file.write(
            f"Output folder: "
            f"{OUTPUT_FOLDER.resolve()}\n\n"
        )

        file.write(
            "FILE COUNTS\n"
        )

        file.write(
            f"Total TXT files       : "
            f"{len(txt_files)}\n"
        )

        file.write(
            f"Empty files           : "
            f"{empty_files}\n"
        )

        file.write(
            f"Very short files     : "
            f"{very_short_files}\n"
        )

        file.write(
            f"Very large files     : "
            f"{very_large_files}\n"
        )

        file.write(
            f"Suspicious files     : "
            f"{suspicious_files}\n"
        )

        file.write(
            f"Unreadable files     : "
            f"{unreadable_files}\n"
        )

        file.write(
            f"Duplicate groups     : "
            f"{len(duplicate_groups)}\n"
        )

        file.write("\n")

        file.write(
            "TEXT STATISTICS\n"
        )

        file.write(
            f"Total characters     : "
            f"{total_chars:,}\n"
        )

        file.write(
            f"Total words          : "
            f"{total_words:,}\n"
        )

        file.write(
            f"Total non-empty lines: "
            f"{total_lines:,}\n"
        )

        file.write(
            f"Estimated pages      : "
            f"{total_pages:,}\n"
        )

        file.write(
            f"Average characters   : "
            f"{average(char_counts):,.2f}\n"
        )

        file.write(
            f"Median characters    : "
            f"{median(char_counts):,.2f}\n"
        )

        file.write(
            f"Average words        : "
            f"{average(word_counts):,.2f}\n"
        )

        file.write(
            f"Median words         : "
            f"{median(word_counts):,.2f}\n"
        )

        file.write(
            f"Average pages        : "
            f"{average(page_counts):,.2f}\n"
        )

        file.write(
            f"Median pages         : "
            f"{median(page_counts):,.2f}\n"
        )

        file.write("\n")

        file.write(
            "WARNING TYPES\n"
        )

        if warning_counter:

            for warning, count in (
                warning_counter.most_common()
            ):

                file.write(
                    f"{warning}: {count}\n"
                )

        else:

            file.write(
                "No warnings detected.\n"
            )

        file.write("\n")

        file.write(
            "INTERPRETATION\n"
        )

        file.write(
            "This report is a profiling tool, not a final "
            "data-quality judgment.\n"
        )

        file.write(
            "Warnings identify documents that should be "
            "reviewed before structured extraction.\n"
        )

        file.write(
            "The cleaning process has not been applied "
            "again by this script.\n"
        )

        file.write(
            "The input TXT files were read only and were "
            "not modified.\n"
        )

    # ========================================================
    # FINAL CONSOLE SUMMARY
    # ========================================================

    print("\n" + "=" * 70)
    print("VALIDATION COMPLETE")
    print("=" * 70)

    print(
        f"\nTotal TXT files       : "
        f"{len(txt_files)}"
    )

    print(
        f"Empty files           : "
        f"{empty_files}"
    )

    print(
        f"Very short files      : "
        f"{very_short_files}"
    )

    print(
        f"Very large files      : "
        f"{very_large_files}"
    )

    print(
        f"Suspicious files      : "
        f"{suspicious_files}"
    )

    print(
        f"Unreadable files      : "
        f"{unreadable_files}"
    )

    print(
        f"Duplicate groups      : "
        f"{len(duplicate_groups)}"
    )

    print("\nReports created:")
    print(
        f"  ✓ {REPORT_FILE}"
    )

    print(
        f"  ✓ {DUPLICATES_FILE}"
    )

    print(
        f"  ✓ {SUMMARY_FILE}"
    )

    print("\n" + "=" * 70)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()