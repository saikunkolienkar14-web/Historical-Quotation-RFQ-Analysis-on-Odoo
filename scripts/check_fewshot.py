"""
Guard against llm_fewshot/fewshot_prompt.txt's Step-6 defect: examples whose
OUTPUT invents a product or price that appears nowhere in that example's own
INPUT. (Examples 1 and 2 did exactly this - ENVEA AAQMS input, Siemens
ULTRAMAT6E output at a different price; Example 5 wrote "93 Lakhs" as the
bare integer 93, a 10^5 scale error.)

For each EXAMPLE block:
  - every numeric price/quantity in OUTPUT must be derivable from some
    money-like or quantity-like token found in that example's INPUT
    (via boq_coords.money, so "93 Lakhs" correctly matches 9300000)
  - every long alphabetic word in OUTPUT.product_name (len >= 5) must
    appear verbatim (case-insensitive) somewhere in INPUT - this is what
    catches a hallucinated make/model like "ULTRAMAT6E" when the input is
    about a completely different product

Usage:
    python scripts/check_fewshot.py [path/to/fewshot_prompt.txt]

Exits non-zero (and prints every violation) if any example fails.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boq_coords.money import parse_price  # noqa: E402

DEFAULT_PATH = ROOT / "llm_fewshot" / "fewshot_prompt.txt"

EXAMPLE_RX = re.compile(
    r"={20}\nEXAMPLE (\d+)\n={20}\nINPUT:\n(?P<input>.*?)\nOUTPUT:\n(?P<output>\{.*?\n\})",
    re.DOTALL,
)

# Tokens that ride along in a JSON string but aren't a "value copied from
# input" in the sense we're checking (field labels the model itself emits,
# common connective words, unit words).
IGNORE_WORDS = {
    "with", "make", "model", "system", "gas", "analyser", "analyzer",
    "based", "measurement", "measuring", "supply", "sample", "range",
    "handling", "specifications", "consisting", "following", "process",
    "principle", "power", "cable", "cylinder", "component", "product",
    "service", "notes", "communication", "documentation", "operating",
    "area", "classification", "mounting", "location", "requirement",
    "output", "standard", "graphic", "screen", "display", "local", "data",
    "acquisition", "panel", "regulators", "valves", "connection",
    "sensors", "electronics", "channel", "pressure", "temperature",
    "conditions", "provided", "customer", "isolation", "tapping",
    "utility", "extractive", "type", "mounted", "install", "commissioning",
}


def candidate_numbers_from_text(text: str) -> set[float]:
    """Every money-like or lakh/crore-like token in the input text, parsed
    to a rupee float, forms the set of numbers an OUTPUT price is allowed
    to equal."""
    candidates: set[float] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Whole-line match only - money.py's grammar is fully anchored
        # (^...$). Deliberately NOT split into whitespace tokens: splitting
        # "93 Lakhs" into "93" + "Lakhs" would let a bare "93" validate as
        # if it were a real 93-rupee price, which is exactly the scale bug
        # (93 vs 9,300,000) this checker exists to catch.
        p = parse_price(line)
        if p.value is not None:
            candidates.add(p.value)
    return candidates


def check_example(num: str, input_text: str, output_json: str) -> list[str]:
    errors = []
    try:
        data = json.loads(output_json)
    except json.JSONDecodeError as e:
        return [f"Example {num}: OUTPUT is not valid JSON ({e})"]

    candidates = candidate_numbers_from_text(input_text)

    for field in ("unit_price", "total_price"):
        val = data.get(field)
        if val is None:
            continue
        val = float(val)
        if not any(abs(val - c) < 0.01 for c in candidates):
            errors.append(
                f"Example {num}: {field}={val!r} does not match any money-like "
                f"token found in this example's own INPUT "
                f"(candidates include: {sorted(candidates)[:8]}...)"
            )

    product_name = data.get("product_name", "") or ""
    input_lower = input_text.lower()
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-]{4,}", product_name)
    for word in words:
        wl = word.lower()
        if wl in IGNORE_WORDS:
            continue
        if wl not in input_lower:
            errors.append(
                f"Example {num}: product_name contains '{word}', which does not "
                f"appear anywhere in this example's own INPUT (looks invented)"
            )

    return errors


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    text = path.read_text(encoding="utf-8")

    matches = list(EXAMPLE_RX.finditer(text))
    if not matches:
        print(f"No EXAMPLE blocks found in {path}", file=sys.stderr)
        return 2

    all_errors: list[str] = []
    for m in matches:
        num = m.group(1)
        errs = check_example(num, m.group("input"), m.group("output"))
        all_errors.extend(errs)
        status = "FAIL" if errs else "ok"
        print(f"Example {num}: {status}")

    if all_errors:
        print("\nViolations:")
        for e in all_errors:
            print(f"  - {e}")
        print(f"\n{len(all_errors)} violation(s) across {len(matches)} examples.")
        return 1

    print(f"\nAll {len(matches)} examples check out: every output value traces to its own input.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
