"""
Tests for scripts/score.py's self-consistency verbatim-traceability check
(2026-09-18 fix): a "derived" total_price (quantity x unit_price, used
only when the source states no total of its own) was never itself
written in the source text, so it must be exempt from the verbatim check
the same way validate.py's own Rule 1 already exempts it - without this,
every derived total in the corpus is a guaranteed false "non-verbatim"
flag, unrelated to extraction quality. Found via a real row in a 300-doc
smoke test (2026-09-18): item 1.2, "PTFE TUBE", unit_price stated as
"Rs 45,000", no total stated, total_price=4,500,000 correctly derived as
100 x 45,000 and correctly never appearing verbatim in raw_row_text.

stdlib-only (unittest), matching the rest of the project.

Run: venv\\Scripts\\python.exe -m unittest tests.test_score -v
"""
import contextlib
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from score import self_consistency  # noqa: E402


def _run(rows) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        self_consistency(rows, "test")
    return buf.getvalue()


class DerivedTotalVerbatimExemptionTest(unittest.TestCase):

    def test_derived_total_is_not_flagged_non_verbatim(self):
        # Exactly the real row's shape: total_price never appears in
        # raw_row_text because it was derived, not stated.
        rows = [{
            "raw_row_text": "1.2 PTFE TUBE 100 Rs 45,000\nMeters",
            "quantity": "100.0",
            "unit_price": "45000.0",
            "total_price": "4500000.0",
            "total_price_source": "derived",
        }]
        out = _run(rows)
        self.assertIn("verbatim in raw_row_text (of 2): 2 (100.00%)", out)

    def test_stated_total_that_is_not_verbatim_is_still_caught(self):
        # A STATED total (not derived) that doesn't appear in the raw
        # text is a real defect and must still be flagged - the
        # exemption is specific to total_price_source == "derived".
        rows = [{
            "raw_row_text": "1 Widget 5 100",
            "quantity": "5",
            "unit_price": "100",
            "total_price": "999999",
            "total_price_source": "stated",
        }]
        out = _run(rows)
        self.assertIn("verbatim in raw_row_text (of 3): 2 (66.67%)", out)

    def test_quantity_and_unit_price_are_never_exempt(self):
        rows = [{
            "raw_row_text": "no numbers here at all",
            "quantity": "5",
            "unit_price": "100",
            "total_price": "",
            "total_price_source": "",
        }]
        out = _run(rows)
        self.assertIn("verbatim in raw_row_text (of 2): 0 (0.00%)", out)


if __name__ == "__main__":
    unittest.main()
