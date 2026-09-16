"""
Money and quantity parsing for the coordinate-aware extractor.

Replaces quotation_parser_v1.py:814 parse_number(), whose
`re.sub(r"[^0-9,.\\-]", "", value)` keeps hyphens and strips letters out of
arbitrary prose, which is the direct cause of:

    "CAPACITY) QTY-2"                -> -2
    "Size-6x4 with real time data"   -> -64
    "Power Supply: 230 VAC, 50 Hz"   -> 23050

The fix here is a fully anchored grammar (^...$, never re.search over
prose) with no minus sign in it at all, plus plausibility bounds that v1
never applied to price fields (it bounds item_no and quantity but not
price - see quotation_parser_v1.py:963 MAX_ITEM_NUMBER /
validate_quantity:1020).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from boq_coords.vocab import PRICE_SENTINELS, UNITS_RE

CURRENCY_RX = r"(?:₹|Rs\.?|INR|USD|\$|SAR|€|£)"

# The digit-group sub-pattern is deliberately width-agnostic: it accepts any
# run of comma-separated 1-3-digit groups (Indian "45,00,000", Western
# "4,500,000", or an OCR-uneven mix like "2,70,04,00") rather than validating
# grouping width, since real Indian lakh-style documents break positional-
# grouping assumptions but should still parse (see money.py requirements
# decision, "strip ALL non-digit/decimal ... do NOT validate comma-grouping
# width"). The digits are still only ever pulled from a fully anchored
# ^...$ match, though - not a blind re.sub over the whole cell - because a
# blind strip-everything-but-digits pass reintroduces the exact bug class
# this module exists to prevent (see module docstring): "Power Supply: 230
# VAC, 50 Hz" would strip to "23050" a price. Anchoring means any stray
# letter/punctuation anywhere in the cell still fails the whole match, same
# as before.
MONEY_RX = re.compile(
    rf"""^\s*
    (?P<cur>{CURRENCY_RX})?\s*
    (?P<num>
        \d{{1,3}}(?:,\d{{1,3}})+   # any comma-grouped run, any group width
        | \d+                       # plain run, no comma grouping at all
    )
    (?:\.(?P<dec>\d{{1,2}}))?
    \s*(?:/-|/=)?\s*
    (?P<cur2>{CURRENCY_RX})?\s*$""",
    re.VERBOSE | re.IGNORECASE,
)

LAKH_CR_RX = re.compile(
    r"^\s*(?:Rs\.?|INR|₹)?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<scale>lakh|lac|cr|crore)s?\.?\s*$",
    re.IGNORECASE,
)

PLACEHOLDER_RX = re.compile(
    "^(?:" + "|".join(re.escape(s) for s in PRICE_SENTINELS) + ")$",
    re.IGNORECASE,
)

# Canonicalization patterns (price fix requirement 6). Source text uses
# inconsistent phrasing for exactly two recurring non-numeric price
# statuses; these collapse all observed variants down to one status each so
# they group together downstream instead of producing dozens of near-
# duplicate raw values.
QUOTED_PATTERN = re.compile(r"\b(quoted|to be quoted|tbq|price on request)\b", re.IGNORECASE)
INCLUDED_PATTERN = re.compile(r"\b(included|inclusive|incl\.?|bundled|part of above)\b", re.IGNORECASE)

# "Not Quoted" / "Not Included" are a real, distinct sentinel in this corpus
# (see vocab.PRICE_SENTINELS) meaning the opposite of QUOTED_PATTERN/
# INCLUDED_PATTERN - a bare \bquoted\b search would otherwise misfire on the
# "quoted" substring inside "Not Quoted" and canonicalize a negation into
# "QUOTED". Checked first so the negated phrase falls through to the
# PLACEHOLDER_RX/MISSING path instead.
NEGATED_PRICE_RX = re.compile(r"\bnot\s+(?:quoted|included|inclusive)\b", re.IGNORECASE)

QUANTITY_RX = re.compile(
    r"^\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[A-Za-z][A-Za-z.\-]*)?\s*$"
)

# Plausibility bounds (design §money.py). v1 has no equivalent for price.
MIN_TOTAL_PRICE = 1000.0
MAX_TOTAL_PRICE = 5_000_000_000.0
MIN_UNIT_PRICE = 1000.0
MAX_UNIT_PRICE = 500_000_000.0

SCALE = {"lakh": 100_000, "lac": 100_000, "cr": 10_000_000, "crore": 10_000_000}


@dataclass
class ParsedPrice:
    value: float | None          # numeric rupee value, or None
    raw: str                     # original text, verbatim
    is_placeholder: bool = False
    currency: str | None = "INR"
    # "NUMERIC" | "QUOTED" | "INCLUDED" | "MISSING" (price fix requirement 6).
    # is_placeholder is kept as a broader, structural signal for banded.py's
    # row-segmentation gate (any recognized non-numeric price text, so it
    # doesn't get reverted into the description column) - status is the
    # narrower, output-facing classification with exactly these 4 values.
    status: str = "MISSING"


def _detect_currency(cur: str | None, cur2: str | None) -> str:
    tok = (cur or cur2 or "").upper().replace(".", "")
    if tok in ("USD", "$"):
        return "USD"
    if tok == "SAR":
        return "SAR"
    if tok in ("€", "EUR"):
        return "EUR"
    if tok in ("£", "GBP"):
        return "GBP"
    return "INR"


def parse_price(text: str) -> ParsedPrice:
    """Parse a single price-band cell. Never uses re.search for the NUMBER
    grammar over free text - the caller (banded.py) is responsible for
    handing this only content that already belongs to a price cell/band,
    never a whole description line. (QUOTED_PATTERN/INCLUDED_PATTERN do use
    re.search, deliberately - they're matched against short, already-priced
    cells to catch phrasing like "To be Quoted" or "Included Above", not
    against arbitrary prose.)"""
    raw = text
    stripped = text.strip()
    if not stripped:
        return ParsedPrice(value=None, raw=raw, currency=None, status="MISSING")

    negated = NEGATED_PRICE_RX.search(stripped)

    if not negated and QUOTED_PATTERN.search(stripped):
        return ParsedPrice(value=None, raw=raw, is_placeholder=True, currency=None, status="QUOTED")

    if not negated and INCLUDED_PATTERN.search(stripped):
        return ParsedPrice(value=None, raw=raw, is_placeholder=True, currency=None, status="INCLUDED")

    if PLACEHOLDER_RX.match(stripped):
        # A recognized non-numeric sentinel that isn't one of the two
        # canonicalized categories above ("Not Quoted", "TBD", "Existing",
        # "N/A", ...) - still a legitimate placeholder (is_placeholder=True
        # keeps banded.py's row-segmentation gate happy), but not something
        # to fold into QUOTED/INCLUDED, so it's MISSING at the status level.
        return ParsedPrice(value=None, raw=raw, is_placeholder=True, currency=None, status="MISSING")

    m = LAKH_CR_RX.match(stripped)
    if m:
        value = float(m.group("num")) * SCALE[m.group("scale").lower()]
        return ParsedPrice(value=value, raw=raw, currency="INR", status="NUMERIC")

    m = MONEY_RX.match(stripped)
    if not m:
        return ParsedPrice(value=None, raw=raw, currency=None, status="MISSING")

    num = m.group("num").replace(",", "")
    dec = m.group("dec")
    try:
        value = float(f"{num}.{dec}" if dec else num)
    except ValueError:
        return ParsedPrice(value=None, raw=raw, currency=None, status="MISSING")

    currency = _detect_currency(m.group("cur"), m.group("cur2"))
    return ParsedPrice(value=value, raw=raw, currency=currency, status="NUMERIC")


# price fix requirement 6: canonical raw token per non-numeric status.
STATUS_CANONICAL_RAW = {"QUOTED": "QUOTED", "INCLUDED": "INCLUDED"}


def canonical_raw(parsed: ParsedPrice) -> str:
    """The text to emit for a price cell's *_raw field: the single
    canonical token when status is QUOTED/INCLUDED (every observed
    phrasing - "TBQ", "Price on Request", "To be Quoted" -> "QUOTED";
    "Incl.", "Bundled", "Part of above" -> "INCLUDED" - collapses to one
    value so they group together downstream instead of producing dozens of
    near-duplicate raw strings), otherwise the original text verbatim."""
    return STATUS_CANONICAL_RAW.get(parsed.status, parsed.raw)


def combine_price_status(unit_status: str, total_status: str) -> str:
    """Row-level price_status folding unit_price's and total_price's
    individual statuses into one value (price fix requirement 6: the field
    "applies at the row level ... since in practice both cells carry the
    same status"). NUMERIC wins whenever either side has an actual number -
    that's the strongest signal available for the row, e.g. a lump-sum row
    with a stated total but no unit price."""
    statuses = {unit_status, total_status}
    if "NUMERIC" in statuses:
        return "NUMERIC"
    if "QUOTED" in statuses:
        return "QUOTED_SEPARATELY"
    if "INCLUDED" in statuses:
        return "INCLUDED"
    return "MISSING"


def price_in_bounds(value: float | None, *, is_unit: bool) -> bool:
    if value is None:
        return True  # nulls are fine; a bad number is the concern
    lo, hi = (MIN_UNIT_PRICE, MAX_UNIT_PRICE) if is_unit else (MIN_TOTAL_PRICE, MAX_TOTAL_PRICE)
    return lo <= value <= hi


@dataclass
class ParsedQuantity:
    value: float | None
    unit: str | None
    raw: str


def parse_quantity(text: str) -> ParsedQuantity:
    """A bare number with no unit word is not a quantity (Step 4 rule 4)."""
    raw = text
    stripped = text.strip()
    if not stripped:
        return ParsedQuantity(value=None, unit=None, raw=raw)
    m = QUANTITY_RX.match(stripped)
    if not m:
        return ParsedQuantity(value=None, unit=None, raw=raw)
    unit = m.group("unit")
    if unit and not UNITS_RE.match(unit.rstrip(".")):
        unit = None
    if unit is None:
        return ParsedQuantity(value=None, unit=None, raw=raw)
    return ParsedQuantity(value=float(m.group("num")), unit=unit, raw=raw)


def resolve_quantity_and_unit(qty_band_text: str, unit_band_text: str) -> ParsedQuantity:
    """Some templates carry QTY and UOM as two separate header columns
    (e.g. 'QUANTITY' | 'UOM'), not one combined 'QTY' cell like '1 Nos' -
    confirmed as a real bug: a document with genuinely separate columns
    fed only the quantity band ('1', no unit suffix) through
    parse_quantity() and got nulled out by the 'bare number has no unit'
    rule, even though the real unit ('MAN-DAY') was sitting right there
    in its own column. When a distinct, non-empty unit band exists and is
    itself a recognized unit word, trust it directly rather than
    requiring the unit to be glued onto the quantity text."""
    unit_stripped = (unit_band_text or "").strip()
    if unit_stripped and UNITS_RE.match(unit_stripped.rstrip(".")):
        try:
            value = float(qty_band_text.strip())
        except (ValueError, AttributeError):
            return parse_quantity(qty_band_text)
        return ParsedQuantity(value=value, unit=unit_stripped, raw=qty_band_text)
    return parse_quantity(qty_band_text)


def arithmetic_ok(qty: float | None, unit_price: float | None, total_price: float | None) -> bool:
    """|unit * qty - total| <= max(1, 2% of total), per Step 4 rule 2 / design §money.py."""
    if qty is None or unit_price is None or total_price is None:
        return True
    expected = qty * unit_price
    tolerance = max(1.0, 0.02 * total_price)
    return abs(expected - total_price) <= tolerance
