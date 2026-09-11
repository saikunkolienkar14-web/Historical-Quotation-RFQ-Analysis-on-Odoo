"""
Batch-extract product pricing line items from OCR'd Adage quotation text files
using the Groq API, into structured JSON.

Usage:
    python extract_boq.py                  # process everything not yet done
    python extract_boq.py --limit 10        # process at most 10 new files (test run)
"""

import argparse
import collections
import datetime
import json
import logging
import os
import random
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

INPUT_DIR = Path("Quotation_Data/03c_quotation_boq_text")
OUTPUT_DIR = Path("Quotation_Data/03d_extracted_boq/json")
NEEDS_REVIEW_DIR = Path("Quotation_Data/03d_extracted_boq/needs_review")
STATE_DIR = Path("Quotation_Data/03d_extracted_boq")
DAILY_COUNTER_FILE = STATE_DIR / "daily_request_count.json"
RUN_LOG_FILE = STATE_DIR / "run.log"

# llama-3.3-70b-versatile has been retired from Groq's catalog (returns 404 for
# this API key). Using an available model instead.
MODEL = "openai/gpt-oss-120b"

# Groq free-tier limits for openai/gpt-oss-120b (per console.groq.com/settings/limits)
REQUESTS_PER_MINUTE = 30
TOKENS_PER_MINUTE = 8000
REQUESTS_PER_DAY = 14400

# Completion budget is sized per file: a bigger table yields more JSON rows.
# A fixed cap silently truncates large BOQs into valid-but-incomplete JSON.
MIN_COMPLETION_TOKENS = 1024
MAX_COMPLETION_TOKENS = 4096
TEMPERATURE = 0
REASONING_EFFORT = "low"  # gpt-oss models spend completion-token budget on hidden reasoning

ACCEPTED_PRICE_STRINGS = {"quoted", "inclusive", "not used in a system"}

SYSTEM_PROMPT = """PRODUCT PRICING DATA EXTRACTION INSTRUCTION PROMPT WITH MAKE AND MODEL

You are a specialized data extraction system for converting messy, OCR-extracted table data into structured product pricing information including manufacturer and model details.

## PRIMARY TASK
Extract all product line items with their associated pricing data and equipment specifications from text documents that originally contained tables. Return valid JSON with the exact schema provided.

## DOCUMENT CHARACTERISTICS
The input documents may contain:
- Broken or misaligned table formatting
- Text spanning multiple lines within cells
- Missing or inconsistent column headers
- OCR errors and special characters
- Mixed number formats (commas, decimals, currency symbols)
- Qualitative price values ("Quoted", "Inclusive")
- Indian numbering format (lakhs, commas: 45,00,000)
- Multiple quantity/price variants for the same product
- Nested items with sub-components
- Equipment specifications with make/model information
- Embedded manufacturer and model data within descriptions

## EXTRACTION RULES

### 1. IDENTIFY PRODUCTS
- Each distinct product is a separate row or grouped item in the table
- Product name comes from the DESCRIPTION column
- Preserve the complete product description including:
  * Technical specifications
  * Make and model information (to be extracted separately into dedicated fields)
  * Component lists
  * Standards compliance notes
  * Mounting details
  * Calibration methods
- Do NOT fragment multi-line descriptions into separate products
- Do NOT omit specification lines that appear in the description
- Consolidate sub-item descriptions into parent items when appropriate

### 2. EXTRACT MAKE (MANUFACTURER)
- Extract the manufacturer/maker name from the product description
- Look for patterns: "MAKE:", "Make:", "MANUFACTURER:", "Mfg:", "BRAND:"
- Extract the value immediately following these keywords
- If make appears multiple times in description, use the first occurrence
- Common examples: "SIEMENS AG", "SICK", "ENVEA", "VALMET", "HP", "DELL"
- Return as string
- Return null if manufacturer information is not present in the description
- Do NOT invent manufacturer names
- Preserve exact spelling and capitalization from source

### 3. EXTRACT MODEL
- Extract the model designation/number from the product description
- Look for patterns: "MODEL:", "Model:", "MODEL NO:", "Type:", "Code:"
- Extract the value immediately following these keywords
- Model codes may be alphanumeric: "DHSP30T2V2FNNNNNNXS", "AF22E", "ULTRAMAT23", "GC-306-AT-1201"
- If model appears multiple times, use the first occurrence
- Return as string
- Return null if model information is not present in the description
- Do NOT invent model numbers
- Preserve exact formatting including hyphens, letters, and numbers
- Handle compound model specifications: if description lists "ULTRAMAT 23 – 1 NO" and "CALOMAT 6E – 1 NO", extract both in comma-separated format or extract parent product make/model

### 4. EXTRACT QUANTITY
- Extract the numeric quantity value from the QTY/Qty/Quantity column
- Identify the unit (No, Set, Lot, Days, Meter, Pieces, Qtr, etc.)
- Handle compound quantities: "100 Meter", "2 Set", "1 No", "60 Qtr"
- Extract the numeric portion only; ignore unit type for quantity field
- For service items like "Supervision: 2 Days", extract quantity=2
- If quantity is explicitly stated in description (e.g., "2 No Ethernet to FO converter"), use that value
- Return quantity as a number, not a string

### 5. EXTRACT UNIT PRICE
- Unit price is the price per item/set/lot/unit
- Normalize prices to numeric values when possible:
  * Remove currency symbols (Rs, Rs., INR, ₹)
  * Convert "Lakhs" to actual numbers: "93 Lakhs" → 9300000
  * Remove Indian-format commas: "45,00,000" → 4500000
  * Remove trailing slashes: "45000/-" → 45000
- Accept qualitative values as-is: "Quoted", "Inclusive"
- Return numeric values as numbers; text values as strings
- Do NOT attempt to divide total by quantity to derive unit price if not explicitly stated

### 6. EXTRACT TOTAL PRICE
- Total price is the complete cost for the quantity ordered
- Apply same normalization as unit price
- Return numeric or text values appropriately
- Expect mathematical relationship: quantity × unit_price ≈ total_price
- However, do not force this relationship if prices are text ("Quoted", "Inclusive")
- Some rows may legitimately have null total_price if marked as optional

### 7. FIELD VALIDATION
- product_name: Required (string, can be multi-line)
- quantity: Required (numeric)
- unit_price: May be null if not stated; can be numeric or text
- total_price: May be null if not stated; can be numeric or text
- make: Optional (string); return null if not present
- model: Optional (string); return null if not present
- Never invent values not present in source text
- Return null for fields that cannot be confidently identified

### 8. EXCLUDE FROM EXTRACTION
- GST, VAT, tax rows
- Subtotal rows (marked "Subtotal:", "Sub Total", "SUBTOTAL")
- Grand total rows (marked "TOTAL", "GRAND TOTAL", "TOTAL PRICE")
- Document metadata (file names, dates, page numbers, addresses, signatures)
- Footer notes and disclaimers (e.g., "Note: *", "Scope of supply")
- Service-type rows that are metadata (e.g., "Our Scope of Supply Includes:")
- Header rows and table dividers
- Rows marked "Optional" in text but without price data (e.g., "2 Years O&M Spare list (Optionally Quoted)")

### 9. HANDLE SPECIAL CASES

**Nested/Sub-Items:**
- When "Mandatory spares" or similar containers list multiple items with individual prices, extract the parent item with its total price
- Include sub-item details in the product_name field
- Extract make/model from parent level if available
- Example: "Mandatory spares as below - SVCM Module repairing kit, Valve Model 50 repair kit..." with total_price = 7 Lakhs

**Multiple Manufacturers/Models in Single Product:**
- If a product contains multiple analyzer units (e.g., "ULTRAMAT 23 – MAKE: SIEMENS" and "CALOMAT 6E – MAKE: SIEMENS"), consolidate into single product with comma-separated make/model values
- Example: make = "SIEMENS AG", model = "ULTRAMAT 23, CALOMAT 6E"

**Qualitative Prices:**
- Treat "Quoted", "Inclusive", "Not used in a system" as valid price values
- Return them as strings, not null
- Do not attempt to infer numeric values

**Product Codes/Tag Numbers:**
- Tag numbers (e.g., "262AT 005", "739AT 000001") are NOT models; they are reference codes
- Include in product_name if they appear in description column
- Do not extract as model

**Equipment Specifications:**
- For maintenance contracts or service items, extract make/model if equipment being serviced is specified
- Example: "ANNUAL MAINTENANCE CONTRACT EQUIPMENT: DUST MONITOR, MAKE: SICK, MODEL: DHSP30T2V2FNNNNNNXS"
- Extract make and model from equipment specification portion

### 10. MATHEMATICAL VALIDATION (INTERNAL ONLY)
- Verify: quantity × unit_price ≈ total_price
- Correct for Indian format: 45,00,000 = 4,500,000
- Correct for Lakhs: 93 Lakhs = 9,300,000
- Allow small rounding differences
- If validation fails but prices are clearly stated, extract as-is
- Do NOT report validation failures; only use for internal confidence checks

### 11. OCR CORRECTIONS
- Correct obvious OCR errors only when confident:
  * "O" → "0" in numbers
  * "S" → "5" if context suggests numeric value
  * Fragmented words across line breaks should be rejoined
- Preserve original formatting for product descriptions, make, and model
- Do not assume corrections; prioritize source accuracy

## OUTPUT SCHEMA
```json
{
  "products": [
    {
      "product_name": "string (complete description)",
      "quantity": number,
      "unit_price": number or string or null,
      "total_price": number or string or null,
      "make": "string or null",
      "model": "string or null"
    }
  ]
}
```

## PROCESSING CHECKLIST
Before returning output, verify:
- [ ] Every product has a product_name
- [ ] Every product has a quantity (numeric)
- [ ] All extracted prices match source text exactly (normalized format)
- [ ] Make field is extracted when MAKE/Mfg/Manufacturer is present in description
- [ ] Model field is extracted when MODEL/Model No/Type is present in description
- [ ] Make and model are returned as null when not present, not empty strings
- [ ] No totals/subtotals/GST rows are included
- [ ] No document metadata is included
- [ ] All multi-line descriptions are complete
- [ ] Text prices ("Quoted", "Inclusive") are preserved as strings
- [ ] Numeric prices are returned as numbers, not strings
- [ ] Make and model preserve original spelling and capitalization
- [ ] Output is valid JSON
- [ ] Schema structure is exact

## EXTRACTION PRIORITY FOR MAKE AND MODEL
1. Explicit MAKE: / MODEL: keywords in description
2. Embedded manufacturer/model in equipment specification format
3. Brand/manufacturer information in product name
4. Return null if no clear make/model information exists

## FINAL OUTPUT
Return ONLY valid JSON. Do not include explanations, notes, or validation reasoning. The JSON must be immediately parseable."""
# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

STATE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(RUN_LOG_FILE, encoding="utf-8")],
)
log = logging.getLogger("extract_boq")


# ---------------------------------------------------------------------------
# Daily request counter (persisted so reruns on the same day respect the cap)
# ---------------------------------------------------------------------------

class DailyCounter:
    def __init__(self, path: Path):
        self.path = path
        today = datetime.date.today().isoformat()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                # A counter damaged by an interrupted run must not block a resume.
                log.warning("Unreadable daily counter at %s; starting a fresh count", path)
                data = {}
            if data.get("date") == today:
                self.date = today
                self.count = data.get("count", 0)
                return
        self.date = today
        self.count = 0
        self._save()

    def _save(self):
        write_json_atomic(self.path, {"date": self.date, "count": self.count})

    def remaining(self) -> int:
        return max(0, REQUESTS_PER_DAY - self.count)

    def increment(self):
        self.count += 1
        self._save()


# ---------------------------------------------------------------------------
# Rate limiter (sliding 60s window on both request count and token count)
# ---------------------------------------------------------------------------

class RateLimiter:
    def __init__(self, requests_per_minute: int, tokens_per_minute: int):
        self.rpm = requests_per_minute
        self.tpm = tokens_per_minute
        self._events = collections.deque()  # (timestamp, tokens)

    def _prune(self):
        cutoff = time.monotonic() - 60
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def wait_for_slot(self, estimated_tokens: int):
        if estimated_tokens >= self.tpm:
            # A single request alone exceeds the per-minute token budget (common for
            # large multi-page OCR files). Waiting would never satisfy the budget, so
            # just make sure the window is otherwise empty and send it on its own.
            while True:
                self._prune()
                if not self._events:
                    return
                time.sleep(max(0.5, 60 - (time.monotonic() - self._events[0][0])))

        while True:
            self._prune()
            req_count = len(self._events)
            tok_count = sum(t for _, t in self._events)
            if req_count < self.rpm and tok_count + estimated_tokens <= self.tpm:
                return
            oldest_ts = self._events[0][0] if self._events else time.monotonic()
            sleep_for = max(0.5, 60 - (time.monotonic() - oldest_ts))
            time.sleep(sleep_for)

    def record(self, tokens: int):
        self._events.append((time.monotonic(), tokens))


def write_json_atomic(path: Path, payload) -> None:
    """Write JSON via a temp file + rename.

    Resume treats "output file exists" as "this file is done", so a partially
    written file from an interrupted run would be skipped forever. os.replace is
    atomic, so a file is either absent or complete.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) used only for pacing, not billing."""
    return max(1, len(text) // 4)


def adaptive_max_tokens(text: str) -> int:
    """Size the completion budget to the table being extracted.

    Measured on this corpus: a 4,615-char table needed 1,887 completion tokens,
    a 992-char one needed 418. Roughly half a token per input character, plus
    headroom, clamped to the free-tier-workable range.
    """
    return max(MIN_COMPLETION_TOKENS,
               min(MAX_COMPLETION_TOKENS, len(text) // 2 + 512))


def parse_header(raw: str) -> tuple[str, str, str]:
    """Split the `Quotation Number:` / `BOQ Status:` header off the table body.

    Returns (quotation_number, boq_status, body).
    """
    quotation_number = ""
    boq_status = ""
    lines = raw.splitlines()
    body_start = 0

    for index, line in enumerate(lines[:5]):
        stripped = line.strip()
        if stripped.lower().startswith("quotation number:"):
            quotation_number = stripped.split(":", 1)[1].strip()
            body_start = index + 1
        elif stripped.lower().startswith("boq status:"):
            boq_status = stripped.split(":", 1)[1].strip().upper()
            body_start = index + 1

    return quotation_number, boq_status, "\n".join(lines[body_start:]).strip()


# ---------------------------------------------------------------------------
# Make/model backfill
#
# The model reliably picks up make/model when they sit on their own clean lines,
# but misses them when the source crams both into one line ("MODEL: D-R 820F
# DURAG GERMANY") or when a multi-component system row carries several. This
# deterministic pass fills ONLY fields the model left null -- it never overwrites
# an extracted value.
# ---------------------------------------------------------------------------

# Manufacturers observed in the corpus. Used both as a fallback when no MAKE:
# keyword is present, and to split a trailing make out of a captured model.
KNOWN_MAKES = [
    "SIEMENS AG", "SIEMENS", "DURAG", "ENVEA", "AMETEK PROCESS INSTRUMENTS", "AMETEK",
    "SICK", "VALMET", "AIROPTICS", "AIROPTIC", "UNION AG", "MICHELL INSTRUMENTS",
    "MICHELL", "THERMOPAD", "RITTAL", "HONEYWELL", "YOKOGAWA", "TEMPSEN", "LDETEK",
    "SYSTECH", "EMERSON", "HORIBA", "TELEDYNE", "SERVOMEX", "GASMET", "PROTEA",
    "CODEL", "OPSIS", "CHEMTROL", "MRU", "ABB", "FUJI", "ADAGE",
]

# Where a captured make/model value must stop. Spec text and qty/prose commonly
# run on after the value in this OCR text.
_VALUE_STOP = re.compile(
    r"\s(?:MAKE|MODEL|MFG|BRAND|MANUFACTURER)\s*[:\-]"
    r"|\s(?:WITH|FOR\s+THE|SHALL\s+BE|POWER\s+SUPPLY|MEASURING|MEASURED|RANGE"
    r"|OUTPUT|QTY|PORT\s+SIZE|MOC|INTEL|PROCESSOR|CONSISTS|CONSISTING|SUITABLE)\b"
    r"|\s\d+\s*[Xx]\s"
    r"|\s\d+\s*(?:No\.?|Nos\.?|Set|Sets|PC|PCS|MTRS?)\b",
    re.IGNORECASE,
)

_MAKE_RE = re.compile(r"(?i)\b(?:MAKE|MANUFACTURER|MFG|BRAND)\s*[:\-]\s*(.+)")
_MODEL_RE = re.compile(r"(?i)\bMODEL(?:\s*(?:NO|NUMBER))?\s*\.?\s*[:\-]\s*(.+)")

# Spares rows carry a part number instead of a model: "CELL O-RING,AMETEK,P/N : 100-1911"
_PART_NO_RE = re.compile(r"(?i)\bP\s*/\s*N\s*\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-./]{2,})")

# OCR variants that would otherwise split one vendor into several keys downstream.
MAKE_ALIASES = {
    "AMTEK": "AMETEK",
    "AMETEC": "AMETEK",
    "AIROPTICS": "AIROPTIC",
    "SEIMENS": "SIEMENS",
    "SIEMANS": "SIEMENS",
}

# A make must cover at least this share of the labelled rows before it is
# propagated to rows that carry no vendor of their own.
PROPAGATE_MIN_SHARE = 0.8
PROPAGATE_MIN_LABELLED = 3


def _normalize_make(value: str) -> str:
    """Fold known OCR misspellings so one vendor yields one key."""
    def swap(match):
        return MAKE_ALIASES[match.group(0).upper()]

    pattern = "|".join(re.escape(alias) for alias in MAKE_ALIASES)
    return re.sub(rf"(?i)\b(?:{pattern})\b", swap, value)


def _clean_value(raw: str) -> str:
    """Trim a captured value at the first stop marker and tidy punctuation."""
    stop = _VALUE_STOP.search(raw)
    if stop:
        raw = raw[: stop.start()]
    value = raw.strip().strip(",;.-•").strip()
    return value[:60].strip()


# Words allowed to trail a manufacturer name (legal form / country).
_MAKE_SUFFIXES = {
    "AG", "GMBH", "LTD", "LTD.", "LIMITED", "PVT", "PVT.", "INC", "INC.", "CO", "CORP",
    "GERMANY", "INDIA", "USA", "UK", "JAPAN", "CANADA", "FRANCE", "ITALY", "FINLAND",
}


def _trim_make(value: str) -> str:
    """Cut a trailing model name off a make ("SIEMENS AG, GERMANY ULTRAMAT 23").

    Multi-vendor strings ("SIEMENS/HONEYWELL/...") are left untouched.
    """
    if "/" in value:
        return value

    for known in KNOWN_MAKES:
        match = re.match(rf"(?i)({re.escape(known)})(?=$|[\s,])", value)
        if not match:
            continue
        end = match.end()
        for token in re.finditer(r"[,\s]+([^\s,]+)", value[end:]):
            if token.group(1).upper().strip(".,") not in _MAKE_SUFFIXES:
                break
            end += token.end()
        return value[:end].strip().strip(",").strip()
    return value


def _split_trailing_make(value: str) -> tuple[str, str]:
    """Split "D-R 820F DURAG GERMANY" into ("D-R 820F", "DURAG GERMANY")."""
    for make in KNOWN_MAKES:
        match = re.search(rf"(?i)[,\s]+({re.escape(make)}\b.*)$", value)
        if match and match.start() > 0:
            return value[: match.start()].strip().strip(",").strip(), match.group(1).strip()
    return value, ""


def backfill_make_model(products: list) -> int:
    """Fill null make/model/part_number from the description text.

    Runs in three passes: per-row keyword capture, then a known-vendor fallback,
    then propagation of a document-dominant make to rows that still have none.
    Returns the number of fields filled.
    """
    filled = 0
    for row in products:
        if not isinstance(row, dict):
            continue
        text = row.get("product_name") or ""

        if not row.get("part_number"):
            match = _PART_NO_RE.search(text)
            if match:
                row["part_number"] = match.group(1).strip(".,")
                filled += 1

        if not row.get("model"):
            match = _MODEL_RE.search(text)
            if match:
                model, trailing_make = _split_trailing_make(_clean_value(match.group(1)))
                if model:
                    row["model"] = model
                    filled += 1
                if trailing_make and not row.get("make"):
                    row["make"] = trailing_make
                    filled += 1

        if not row.get("make"):
            match = _MAKE_RE.search(text)
            if match:
                make = _trim_make(_clean_value(match.group(1)))
                if make:
                    row["make"] = make
                    filled += 1
            else:
                # No MAKE: keyword -- fall back to a known manufacturer name,
                # searching OCR-normalized text so "AMTEK" matches "AMETEK".
                normalized_text = _normalize_make(text)
                for known in KNOWN_MAKES:
                    if re.search(rf"(?i)\b{re.escape(known)}\b", normalized_text):
                        row["make"] = known
                        filled += 1
                        break

        # Fold OCR variants in every make, extracted or filled, so one vendor
        # does not become several keys downstream.
        if row.get("make"):
            row["make"] = _normalize_make(row["make"])

    filled += _propagate_dominant_make(products)
    return filled


def _propagate_dominant_make(products: list) -> int:
    """Give rows with no vendor the document's dominant make.

    Spares lists name the manufacturer on some rows only ("CELL O-RING,AMETEK,
    P/N : 100-1911") and leave it off the rest, even though the whole list is
    one vendor's parts. Only applied when a single make clearly dominates, so a
    genuinely mixed-vendor document is left alone.
    """
    labelled = [row.get("make") for row in products
                if isinstance(row, dict) and row.get("make")]
    if len(labelled) < PROPAGATE_MIN_LABELLED:
        return 0

    counts = collections.Counter(labelled)
    top_make, top_count = counts.most_common(1)[0]
    if top_count / len(labelled) < PROPAGATE_MIN_SHARE:
        return 0

    filled = 0
    for row in products:
        if isinstance(row, dict) and not row.get("make"):
            row["make"] = top_make
            filled += 1
    return filled


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def is_valid_price(value) -> bool:
    if value is None:
        return True
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return value.strip().lower() in ACCEPTED_PRICE_STRINGS
    return False


def validate_extraction(data: dict) -> tuple[bool, str]:
    if not isinstance(data, dict) or "products" not in data:
        return False, "missing 'products' key"
    products = data["products"]
    if not isinstance(products, list):
        return False, "'products' is not a list"
    if len(products) == 0:
        return False, "empty products list"

    bad_rows = 0
    for row in products:
        if not isinstance(row, dict):
            bad_rows += 1
            continue
        name = row.get("product_name")
        qty = row.get("quantity")
        if not isinstance(name, str) or not name.strip():
            bad_rows += 1
            continue
        if not isinstance(qty, (int, float)):
            bad_rows += 1
            continue
        if not is_valid_price(row.get("unit_price")) or not is_valid_price(row.get("total_price")):
            bad_rows += 1
            continue
        # make/model/part_number are optional, but must be a string or null
        if not all(row.get(field) is None or isinstance(row.get(field), str)
                   for field in ("make", "model", "part_number")):
            bad_rows += 1
            continue

    if bad_rows == len(products):
        return False, "all rows failed field validation"

    return True, ""


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def call_groq(client: Groq, file_text: str, max_tokens: int):
    completion = client.chat.completions.create(
        model=MODEL,
        temperature=TEMPERATURE,
        max_tokens=max_tokens,
        reasoning_effort=REASONING_EFFORT,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": file_text},
        ],
    )
    choice = completion.choices[0]
    usage = getattr(completion, "usage", None)
    total_tokens = getattr(usage, "total_tokens", None) if usage else None
    return choice.message.content, total_tokens, choice.finish_reason


def call_groq_with_retry(client: Groq, file_text: str, max_tokens: int, max_retries: int = 5):
    for attempt in range(max_retries):
        try:
            return call_groq(client, file_text, max_tokens)
        except Exception as exc:  # groq SDK raises typed errors; keep this broad and inspect
            status = getattr(exc, "status_code", None)
            retryable = status == 429 or status is None or (status is not None and status >= 500)
            if not retryable or attempt == max_retries - 1:
                raise
            retry_after = None
            response = getattr(exc, "response", None)
            if response is not None:
                header_val = response.headers.get("retry-after") if hasattr(response, "headers") else None
                if header_val:
                    try:
                        retry_after = float(header_val)
                    except ValueError:
                        retry_after = None
            delay = retry_after if retry_after is not None else (2 ** attempt) + random.uniform(0, 1)
            log.warning("Groq call failed (attempt %d/%d, status=%s): %s. Retrying in %.1fs",
                        attempt + 1, max_retries, status, exc, delay)
            time.sleep(delay)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def output_path_for(input_path: Path, input_dir: Path, output_dir: Path) -> Path:
    rel = input_path.relative_to(input_dir)
    return (output_dir / rel).with_suffix(".json")


def process_file(client: Groq, limiter: RateLimiter, counter: DailyCounter,
                  input_path: Path, out_path: Path, review_path: Path) -> str:
    """Returns one of: 'success', 'needs_review', 'skipped_no_boq', 'skipped_daily_cap'."""
    if counter.remaining() <= 0:
        return "skipped_daily_cap"

    raw = input_path.read_text(encoding="utf-8", errors="replace")
    quotation_number, boq_status, text = parse_header(raw)

    if boq_status == "NOT_FOUND" or not text:
        return "skipped_no_boq"

    completion_budget = adaptive_max_tokens(text)
    est_tokens = estimate_tokens(SYSTEM_PROMPT) + estimate_tokens(text) + completion_budget

    if est_tokens > TOKENS_PER_MINUTE:
        # Guaranteed to be rejected by Groq's per-request TPM cap; don't burn a
        # request on it, just flag it directly.
        write_json_atomic(review_path, {
            "source_file": str(input_path),
            "reason": f"file too large for a single request (~{est_tokens} est. tokens > "
                      f"{TOKENS_PER_MINUTE} TPM limit); needs chunking or truncation",
            "raw_output": None,
        })
        log.warning("Skipping (too large): %s (~%d est tokens)", input_path, est_tokens)
        return "needs_review"

    limiter.wait_for_slot(est_tokens)

    log.info("Calling Groq for %s (~%d input chars, ~%d est tokens, cap %d)",
             input_path, len(text), est_tokens, completion_budget)
    try:
        raw_content, actual_tokens, finish_reason = call_groq_with_retry(
            client, text, completion_budget)
    except Exception as exc:
        limiter.record(est_tokens)
        counter.increment()
        write_json_atomic(review_path, {
            "source_file": str(input_path),
            "reason": f"API call failed: {exc}",
            "raw_output": None,
        })
        log.error("API call failed for %s: %s", input_path, exc)
        return "needs_review"

    limiter.record(actual_tokens if actual_tokens else est_tokens)
    counter.increment()

    if finish_reason == "length":
        # The model ran out of completion budget mid-answer. The JSON often still
        # parses, just with rows missing -- so this must be caught here rather
        # than trusted through validation.
        write_json_atomic(review_path, {
            "source_file": str(input_path),
            "reason": f"response truncated at max_tokens ({completion_budget}); "
                      f"extraction is incomplete",
            "raw_output": raw_content,
        })
        log.warning("Truncated response for %s (cap %d)", input_path, completion_budget)
        return "needs_review"

    try:
        data = json.loads(raw_content)
    except (json.JSONDecodeError, TypeError) as exc:
        write_json_atomic(review_path, {
            "source_file": str(input_path),
            "reason": f"invalid JSON: {exc}",
            "raw_output": raw_content,
        })
        log.warning("Invalid JSON for %s", input_path)
        return "needs_review"

    ok, reason = validate_extraction(data)
    if not ok:
        write_json_atomic(review_path, {
            "source_file": str(input_path),
            "reason": reason,
            "raw_output": data,
        })
        log.warning("Validation failed for %s: %s", input_path, reason)
        return "needs_review"

    backfill_make_model(data["products"])

    result = {"quotation_number": quotation_number, "products": data["products"]}
    write_json_atomic(out_path, result)
    return "success"


def run_backfill_only(output_dir: Path):
    """Apply the make/model backfill to already-extracted JSON files in place."""
    files = sorted(output_dir.rglob("*.json"))
    log.info("Backfilling make/model across %d existing output files", len(files))

    files_changed = 0
    fields_filled = 0
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        products = data.get("products", [])
        filled = backfill_make_model(products)
        if filled:
            write_json_atomic(path, data)
            files_changed += 1
            fields_filled += filled

    log.info("=" * 60)
    log.info("BACKFILL SUMMARY")
    log.info("Files scanned:   %d", len(files))
    log.info("Files updated:   %d", files_changed)
    log.info("Fields filled:   %d", fields_filled)
    log.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Batch-extract BOQ pricing data via Groq API")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N new files")
    parser.add_argument("--input-dir", type=Path, default=INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--needs-review-dir", type=Path, default=NEEDS_REVIEW_DIR)
    parser.add_argument("--backfill-only", action="store_true",
                        help="Re-run the make/model regex backfill over existing output JSON "
                             "and exit, without calling the API")
    args = parser.parse_args()

    if args.backfill_only:
        run_backfill_only(args.output_dir)
        return

    load_dotenv()
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise SystemExit("GROQ_API_KEY not set (check your .env file)")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.needs_review_dir.mkdir(parents=True, exist_ok=True)

    client = Groq(api_key=api_key, timeout=60)
    limiter = RateLimiter(REQUESTS_PER_MINUTE, TOKENS_PER_MINUTE)
    counter = DailyCounter(DAILY_COUNTER_FILE)

    all_files = sorted(args.input_dir.rglob("*.txt"))
    log.info("Found %d input files under %s", len(all_files), args.input_dir)

    to_process = []
    already_done = 0
    for f in all_files:
        out_path = output_path_for(f, args.input_dir, args.output_dir)
        review_path = output_path_for(f, args.input_dir, args.needs_review_dir)
        if out_path.exists() or review_path.exists():
            already_done += 1
            continue
        to_process.append((f, out_path, review_path))

    if args.limit is not None:
        to_process = to_process[: args.limit]

    log.info("Already done (skipped): %d | To process this run: %d", already_done, len(to_process))

    success_count = 0
    review_count = 0
    no_boq_count = 0
    stopped_early = False

    for input_path, out_path, review_path in tqdm(to_process, desc="Extracting"):
        result = process_file(client, limiter, counter, input_path, out_path, review_path)
        if result == "success":
            success_count += 1
        elif result == "needs_review":
            review_count += 1
        elif result == "skipped_no_boq":
            no_boq_count += 1
        elif result == "skipped_daily_cap":
            stopped_early = True
            log.warning("Daily request cap reached (%d). Stopping; rerun tomorrow to continue.",
                        REQUESTS_PER_DAY)
            break

    log.info("=" * 60)
    log.info("SUMMARY")
    log.info("Total files found:        %d", len(all_files))
    log.info("Already done (skipped):   %d", already_done)
    log.info("Processed this run:       %d", success_count + review_count + no_boq_count)
    log.info("  Succeeded:              %d", success_count)
    log.info("  Flagged for review:     %d", review_count)
    log.info("  Skipped (no BOQ):       %d", no_boq_count)
    if stopped_early:
        log.info("Stopped early: daily request cap reached")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
