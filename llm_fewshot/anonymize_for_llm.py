"""
Anonymize Quotation Text for LLM Few-Shot Testing
==================================================

Replaces customer-identifying and commercial fields in a handful of
hand-picked quotation text files (see docs/FEWSHOT_EXAMPLE_SELECTION.md)
with synthetic values of the same shape, so they can be safely shown to
an LLM (local or cloud) while testing a few-shot extraction prompt.

Masked: customer/company name + address, the customer's named contact
(person/email/phone), and every price/quantity value (reformatted to
match the original's digit count, comma grouping, decimal places, and
trailing "/-" marker).

Left untouched: item descriptions, table structure, labels, and Adage's
own contact info (staff names, @adage-automation.com emails, phone
numbers, letterhead, own CIN) - that's the sender's own already-external
info, not the confidential part. See docs/FEWSHOT_EXAMPLE_SELECTION.md
and the plan this script was built from for the full reasoning.

Input:
    Quotation_Data/03_preprocessed_text_2/<relative path>.txt
    (optionally) Quotation_Data/03_structured_current/quotations.csv
    (optionally) Quotation_Data/03_structured_current/quotation_items.csv

Output:
    llm_fewshot/anonymized/<relative path>.txt
    llm_fewshot/anonymization_report.csv        (counts only, no values)
    llm_fewshot/anonymized_structured/quotations.csv       (if requested)
    llm_fewshot/anonymized_structured/quotation_items.csv  (if requested)

Usage:
    python anonymize_for_llm.py --files "2425W034R2/UNPRICED OFFER-....txt"
    python anonymize_for_llm.py --list selected_examples.txt --with-structured
"""

import argparse
import csv
import hashlib
import random
import re
import sys
from pathlib import Path

# Sibling project's shared regex/label lists - reused rather than
# reinvented, per docs/DATA_DICTIONARY.md and the parser itself.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quotation_parser_v1 import CUSTOMER_LABELS  # noqa: E402

csv.field_size_limit(sys.maxsize)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_ROOT = PROJECT_ROOT / "Quotation_Data" / "03_preprocessed_text_2"
STRUCTURED_DIR = PROJECT_ROOT / "Quotation_Data" / "03_structured_current"
OUTPUT_ROOT = Path(__file__).resolve().parent
ANONYMIZED_DIR = OUTPUT_ROOT / "anonymized"
STRUCTURED_OUT_DIR = OUTPUT_ROOT / "anonymized_structured"
REPORT_PATH = OUTPUT_ROOT / "anonymization_report.csv"

# Not the confidential part - Adage's own sales contact info is already
# meant to be shared externally on a real quotation. Anything on this
# domain is left untouched.
OWN_EMAIL_DOMAIN = "adage-automation.com"
OWN_COMPANY_MARKERS = ("adage automation", "corporate hq")

CUSTOMER_LABEL_LINE_RE = re.compile(
    r"^\s*(?:" + "|".join(re.escape(lbl) for lbl in CUSTOMER_LABELS) + r")\s*:?\s*$",
    re.IGNORECASE,
)

OTHER_KNOWN_LABEL_RE = re.compile(
    r"^\s*(subject|kind attention|quotation no|quote no|dear sir|dear madam)\b",
    re.IGNORECASE,
)

KIND_ATTENTION_RE = re.compile(r"(Kind Attention:\s*)(.+)", re.IGNORECASE)

PERSON_NAME_LINE_RE = re.compile(
    r"^\s*(Mr\.?|Ms\.?|Mrs\.?)\s+[A-Z][A-Za-z.]*(?:\s+[A-Z][A-Za-z.]*)*\s*$"
)

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")

PHONE_RE = re.compile(r"(?:\+?\s*\d{1,3}[\s-]?)?\d{10}\b")

CIN_GSTIN_RE = re.compile(r"\b(CIN|GIN|GSTIN|PAN)\s*(No\.?|Number)?\s*:?\s*[A-Z0-9]{6,}", re.IGNORECASE)

# Prefixed amounts: a currency marker followed by a number.
_PREFIXED_MONEY_RE = re.compile(
    r"(?P<prefix>₹|\$|€|£|Rs\.?|INR|USD|EUR|GBP)\s*"
    r"(?P<number>\d[\d,]*(?:\.\d+)?)"
    r"(?P<suffix>\s*/-)?",
    re.IGNORECASE,
)

# Bare comma-grouped amounts with no currency marker, e.g. "36,00,000/-".
# Requires at least one comma so plain item numbers/quantities are never
# touched by this pattern.
_BARE_MONEY_RE = re.compile(
    r"(?P<number>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?)"
    r"(?P<suffix>\s*/-)?"
)

SYNTHETIC_COMPANIES = [
    "Meridian Industrial Works",
    "Northfield Process Systems",
    "Coastal Cement & Minerals Ltd",
    "Ashgrove Manufacturing Pvt Ltd",
    "Kestrel Engineering Corporation",
]

SYNTHETIC_ADDRESS_LINES = [
    "Plot 14, Sector 9, Industrial Estate",
    "Near Ring Road, Central District",
    "Unit 3, Riverside Business Park",
]

SYNTHETIC_NAMES = [
    "Mr. A. Kulkarni",
    "Mr. R. Verma",
    "Ms. S. Iyer",
    "Mr. P. Nair",
    "Ms. T. Bhatt",
]

# Single distinctive words used to replace stray mentions of the
# customer's name outside the labeled address block (RFQ references,
# footers, "Major Customers include" lists, etc.).
ALIAS_WORDS = [
    "Meridian",
    "Northfield",
    "Ashgrove",
    "Kestrel",
    "Bellwood",
    "Fenwick",
    "Halden",
    "Corrigan",
]

# Words that are part of the document's normal business vocabulary
# (industry type, legal suffixes) rather than part of a specific
# customer's name - never replaced even when they appear in the
# customer block.
GENERIC_COMPANY_WORDS = {
    "cement", "cements", "industries", "industry", "industrial",
    "works", "limited", "private", "systems", "solutions",
    "engineering", "corporation", "company", "group", "plant",
    "plants", "north", "south", "east", "west", "floor", "wing",
    "sector", "road", "highway", "office", "india",
}


def rng_for(relative_path: str) -> random.Random:
    """Deterministic per-file RNG, so re-runs produce identical output."""
    seed = int(hashlib.sha256(relative_path.encode("utf-8")).hexdigest(), 16) % (2**32)
    return random.Random(seed)


def is_own_company_email(email: str) -> bool:
    return email.lower().endswith("@" + OWN_EMAIL_DOMAIN)


def synth_number(original: str, rng: random.Random) -> str:
    """
    Regenerate a number string with the same shape: same comma-group
    sizes and same decimal-place count as the original.
    """
    if "." in original:
        integer_part, decimal_part = original.split(".", 1)
    else:
        integer_part, decimal_part = original, ""

    groups = integer_part.split(",")
    new_groups = []
    for i, group in enumerate(groups):
        width = len(group)
        if width <= 0:
            new_groups.append(group)
            continue
        if i == 0:
            low = 1 if width == 1 else 10 ** (width - 1)
            high = 10**width - 1
        else:
            low = 0
            high = 10**width - 1
        new_groups.append(str(rng.randint(low, high)).zfill(width))

    result = ",".join(new_groups)

    if decimal_part:
        width = len(decimal_part)
        result += "." + str(rng.randint(0, 10**width - 1)).zfill(width)

    return result


class Anonymizer:
    def __init__(self, relative_path: str):
        self.rng = rng_for(relative_path)
        self.value_map = {}  # original raw string -> synthetic string
        self.sensitive_strings = []  # for the post-hoc leak self-check
        self.name_aliases = {}  # lowercase original token -> alias word
        self.counts = {
            "customer_lines_replaced": 0,
            "contacts_replaced": 0,
            "emails_replaced": 0,
            "phones_replaced": 0,
            "money_values_replaced": 0,
            "registration_numbers_replaced": 0,
            "stray_name_mentions_replaced": 0,
        }

    def _remember(self, original: str, synthetic: str):
        if original and original.strip():
            self.value_map[original.strip()] = synthetic
            self.sensitive_strings.append(original.strip())

    # ------------------------------------------------------------
    # Customer block
    # ------------------------------------------------------------

    def anonymize_customer_block(self, lines):
        out = list(lines)
        i = 0
        while i < len(out):
            if CUSTOMER_LABEL_LINE_RE.match(out[i]):
                # The label and the actual name/address are often
                # separated by a blank line (e.g. "Customer:\n\nAdani
                # Cement...") - skip past that before collecting.
                j = i + 1
                while j < len(out) and not out[j].strip():
                    j += 1
                block_start = j
                while j < len(out) and out[j].strip() and not OTHER_KNOWN_LABEL_RE.match(out[j]):
                    j += 1
                block_lines = out[block_start:j]
                if block_lines:
                    self._remember("\n".join(block_lines), "")
                    self._register_name_aliases(block_lines[0])
                    replacement = [self.rng.choice(SYNTHETIC_COMPANIES)]
                    # quotations.csv's `customer` field is typically just
                    # this first line - map it separately (in addition
                    # to the full block above) so the expected-output
                    # CSV shows the same synthetic name as the text.
                    self._remember(block_lines[0], replacement[0])
                    replacement += self.rng.sample(
                        SYNTHETIC_ADDRESS_LINES, k=min(len(block_lines) - 1, len(SYNTHETIC_ADDRESS_LINES))
                    ) if len(block_lines) > 1 else []
                    # Pad/truncate to the original line count so layout is preserved.
                    while len(replacement) < len(block_lines):
                        replacement.append(self.rng.choice(SYNTHETIC_ADDRESS_LINES))
                    replacement = replacement[: len(block_lines)]
                    out[block_start:j] = replacement
                    self.counts["customer_lines_replaced"] += len(block_lines)
                i = j
            else:
                i += 1
        return out

    def _register_name_aliases(self, company_line: str):
        """
        The customer's name often reappears elsewhere in the document
        outside the labeled block (RFQ references, footers, reference
        lists). Record each distinctive word from the company name so
        anonymize_global_aliases() can sweep the rest of the text too.
        Generic words shared with the document's own boilerplate
        ("Cement", "Industries", "Works", "Limited"...) are excluded so
        unrelated marketing sentences aren't corrupted.
        """
        for token in re.findall(r"[A-Za-z]{4,}", company_line):
            key = token.lower()
            if key in GENERIC_COMPANY_WORDS or key in self.name_aliases:
                continue
            self.name_aliases[key] = self.rng.choice(ALIAS_WORDS)

    # ------------------------------------------------------------
    # Customer's named contact (Kind Attention / Mr./Ms. lines)
    # ------------------------------------------------------------

    def anonymize_named_contacts(self, lines):
        out = list(lines)

        for i, line in enumerate(out):
            m = KIND_ATTENTION_RE.match(line)
            if m:
                original_name = m.group(2).strip()
                synthetic_name = self.rng.choice(SYNTHETIC_NAMES)
                self._remember(original_name, synthetic_name)
                out[i] = m.group(1) + synthetic_name
                self.counts["contacts_replaced"] += 1

        for i, line in enumerate(out):
            if not PERSON_NAME_LINE_RE.match(line):
                continue
            lookahead = " ".join(out[i + 1 : i + 3])
            own_email_nearby = any(
                is_own_company_email(e) for e in EMAIL_RE.findall(lookahead)
            )
            if own_email_nearby:
                continue
            original_name = line.strip()
            if original_name in self.value_map:
                continue
            synthetic_name = self.rng.choice(SYNTHETIC_NAMES)
            self._remember(original_name, synthetic_name)
            out[i] = synthetic_name
            self.counts["contacts_replaced"] += 1

        return out

    # ------------------------------------------------------------
    # Emails / phones (skip own-domain matches)
    # ------------------------------------------------------------

    def anonymize_contact_details(self, text):
        lines = text.split("\n")

        def replace_email(m):
            email = m.group(0)
            if is_own_company_email(email):
                return email
            synthetic = f"contact{self.rng.randint(100, 999)}@example.com"
            self._remember(email, synthetic)
            self.counts["emails_replaced"] += 1
            return synthetic

        lines = [EMAIL_RE.sub(replace_email, line) for line in lines]

        def own_contact_block_nearby(index):
            window = lines[max(0, index - 2) : index + 3]
            return any(OWN_EMAIL_DOMAIN in line.lower() for line in window)

        def replace_phone(m):
            phone = m.group(0)
            synthetic = "".join(str(self.rng.randint(0, 9)) for _ in range(10))
            self._remember(phone, synthetic)
            self.counts["phones_replaced"] += 1
            return synthetic

        new_lines = []
        for index, line in enumerate(lines):
            if own_contact_block_nearby(index):
                new_lines.append(line)
            else:
                new_lines.append(PHONE_RE.sub(replace_phone, line))

        return "\n".join(new_lines)

    # ------------------------------------------------------------
    # Registration numbers (customer's own CIN/GSTIN/PAN, if any)
    # ------------------------------------------------------------

    def anonymize_registration_numbers(self, text):
        lines = text.split("\n")

        def own_letterhead_nearby(index):
            window = " ".join(lines[max(0, index - 3) : index + 1]).lower()
            return any(marker in window for marker in OWN_COMPANY_MARKERS)

        new_lines = []
        for index, line in enumerate(lines):
            if own_letterhead_nearby(index):
                new_lines.append(line)
                continue

            def replace_reg_no(m):
                original = m.group(0)
                synthetic = re.sub(r"[A-Z0-9]", lambda c: self.rng.choice("0123456789") if c.group(0).isdigit() else self.rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ"), original)
                self._remember(original, synthetic)
                self.counts["registration_numbers_replaced"] += 1
                return synthetic

            new_lines.append(CIN_GSTIN_RE.sub(replace_reg_no, line))

        return "\n".join(new_lines)

    # ------------------------------------------------------------
    # Stray mentions of the customer's name outside the labeled block
    # ------------------------------------------------------------

    def anonymize_global_aliases(self, text):
        if not self.name_aliases:
            return text

        pattern = re.compile(
            r"\b(" + "|".join(re.escape(token) for token in self.name_aliases) + r")\b",
            re.IGNORECASE,
        )

        def replace(m):
            alias = self.name_aliases[m.group(0).lower()]
            self.counts["stray_name_mentions_replaced"] += 1
            return alias

        return pattern.sub(replace, text)

    # ------------------------------------------------------------
    # Money values
    # ------------------------------------------------------------

    def anonymize_money(self, text):
        def replace_prefixed(m):
            original_number = m.group("number")
            synthetic_number = synth_number(original_number, self.rng)
            self._remember(m.group(0), m.group("prefix") + " " + synthetic_number + (m.group("suffix") or ""))
            self.counts["money_values_replaced"] += 1
            return m.group("prefix") + " " + synthetic_number + (m.group("suffix") or "")

        text = _PREFIXED_MONEY_RE.sub(replace_prefixed, text)

        def replace_bare(m):
            original_number = m.group("number")
            synthetic_number = synth_number(original_number, self.rng)
            self._remember(m.group(0), synthetic_number + (m.group("suffix") or ""))
            self.counts["money_values_replaced"] += 1
            return synthetic_number + (m.group("suffix") or "")

        text = _BARE_MONEY_RE.sub(replace_bare, text)

        return text

    # ------------------------------------------------------------
    # Driver
    # ------------------------------------------------------------

    def anonymize(self, text: str) -> str:
        lines = text.split("\n")
        lines = self.anonymize_customer_block(lines)
        lines = self.anonymize_named_contacts(lines)
        text = "\n".join(lines)
        # Global alias sweep runs before contact/money substitution so
        # a stray customer-name mention isn't accidentally re-matched
        # by a later, unrelated pattern.
        text = self.anonymize_global_aliases(text)
        text = self.anonymize_contact_details(text)
        text = self.anonymize_registration_numbers(text)
        text = self.anonymize_money(text)
        return text

    def residual_leaks(self, anonymized_text: str):
        """Confirm none of the original sensitive strings survived."""
        lowered = anonymized_text.lower()
        leaks = [s for s in self.sensitive_strings if s and s.lower() in lowered]
        leaks += [
            token for token in self.name_aliases
            if re.search(r"\b" + re.escape(token) + r"\b", lowered)
        ]
        return leaks


def anonymize_file(relative_path: str, example_id: str) -> dict:
    """
    The original filename and folder name are themselves identifying
    (this corpus names files like "...SANGHI CEMENT WORKS.txt"), so the
    anonymized output is written under a generic example_id instead of
    the original path - the whole point of the "safe to share" output
    is defeated if the filename alone reveals the customer.
    """
    source_path = INPUT_ROOT / relative_path
    if not source_path.exists():
        print(f"MISSING: {source_path}")
        return None

    text = source_path.read_text(encoding="utf-8", errors="replace")

    anonymizer = Anonymizer(relative_path)
    anonymized_text = anonymizer.anonymize(text)

    output_path = ANONYMIZED_DIR / f"{example_id}.txt"
    output_path.write_text(anonymized_text, encoding="utf-8")

    leaks = anonymizer.residual_leaks(anonymized_text)
    if leaks:
        print(
            f"WARNING: {len(leaks)} original value(s) still present in "
            f"the anonymized output for {example_id} - inspect before use."
        )

    row = {"example_id": example_id}
    row.update(anonymizer.counts)
    row["residual_leaks_detected"] = len(leaks)

    return {"row": row, "value_map": anonymizer.value_map}


def anonymize_structured_rows(relative_paths_to_maps, id_by_path, quotations_csv, items_csv):
    """
    NOTE ON SAFETY: a value not found in a document's value_map is
    redacted, never passed through as the original. Matching the exact
    raw-text substring recorded while anonymizing the free text against
    a CSV field (e.g. unit_price_raw) is not guaranteed to line up
    (the parser may have stored the price without the currency prefix
    that was part of the matched text span, for instance) - and a
    silent fallback to the real value would defeat the entire point of
    this script. Redaction-by-default costs a few rows in this optional
    CSV; a leaked real price would cost a lot more.
    """
    STRUCTURED_OUT_DIR.mkdir(parents=True, exist_ok=True)
    source_files_by_name = {}
    for rel_path in relative_paths_to_maps:
        source_files_by_name[Path(rel_path).name] = rel_path

    redacted_count = 0

    def remap(value, value_map):
        nonlocal redacted_count
        key = str(value).strip()
        if key in value_map:
            return value_map[key]
        redacted_count += 1
        return "[REDACTED]"

    for csv_path, field_names in (
        (quotations_csv, ["customer"]),
        (items_csv, ["unit_price_raw", "total_price_raw", "unit_price", "total_price", "quantity"]),
    ):
        if not csv_path or not Path(csv_path).exists():
            continue
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = [r for r in reader if r.get("source_file") in source_files_by_name]
            fieldnames = reader.fieldnames

        for row in rows:
            rel_path = source_files_by_name[row["source_file"]]
            value_map = relative_paths_to_maps[rel_path]

            # The original filename/path columns are identifying too -
            # replace them with the same generic example_id used for
            # the anonymized text file, so this CSV is shareable too.
            row["source_file"] = id_by_path[rel_path]
            if "source_path" in row:
                row["source_path"] = row["source_file"]

            for field in field_names:
                if field in row and row[field]:
                    row[field] = remap(row[field], value_map)

        out_path = STRUCTURED_OUT_DIR / Path(csv_path).name
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"Wrote {len(rows)} anonymized row(s) to {out_path}")

    if redacted_count:
        print(
            f"NOTE: {redacted_count} field value(s) could not be matched "
            f"to a synthetic replacement and were written as [REDACTED] "
            f"rather than risking the real value - check these rows."
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--files", nargs="*", default=[], help="Relative paths under Quotation_Data/03_preprocessed_text_2/")
    parser.add_argument("--list", help="Text file with one relative path per line")
    parser.add_argument("--with-structured", action="store_true", help="Also emit anonymized quotations.csv/quotation_items.csv rows")
    parser.add_argument("--quotations-csv", default=str(STRUCTURED_DIR / "quotations.csv"))
    parser.add_argument("--items-csv", default=str(STRUCTURED_DIR / "quotation_items.csv"))
    args = parser.parse_args()

    relative_paths = list(args.files)
    if args.list:
        with open(args.list, encoding="utf-8") as f:
            relative_paths += [line.strip() for line in f if line.strip()]

    if not relative_paths:
        parser.error("Provide files with --files or --list")

    ANONYMIZED_DIR.mkdir(parents=True, exist_ok=True)

    # Generic per-run IDs - the real filenames in this corpus embed the
    # customer name (e.g. "...SANGHI CEMENT WORKS.txt"), so the
    # anonymized output must not be named after its source file.
    id_by_path = {
        relative_path: f"example_{i + 1:03d}"
        for i, relative_path in enumerate(relative_paths)
    }

    report_rows = []
    value_maps = {}
    index_rows = []

    for relative_path in relative_paths:
        example_id = id_by_path[relative_path]
        result = anonymize_file(relative_path, example_id)
        if result is None:
            continue
        report_rows.append(result["row"])
        value_maps[relative_path] = result["value_map"]
        index_rows.append({"example_id": example_id, "relative_path": relative_path})

    if report_rows:
        with open(REPORT_PATH, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(report_rows[0].keys()))
            writer.writeheader()
            writer.writerows(report_rows)
        print(f"\nWrote report (counts only, no values) to {REPORT_PATH}")

    if index_rows:
        index_path = OUTPUT_ROOT / "_local_index_DO_NOT_SHARE.csv"
        with open(index_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["example_id", "relative_path"])
            writer.writeheader()
            writer.writerows(index_rows)
        print(
            f"Wrote example_id -> source file mapping to {index_path} "
            f"- this file names real customers, keep it local, never share it."
        )

    if args.with_structured:
        anonymize_structured_rows(value_maps, id_by_path, args.quotations_csv, args.items_csv)

    print(f"\nAnonymized {len(report_rows)} file(s) into {ANONYMIZED_DIR}")


if __name__ == "__main__":
    main()
