"""
Tests for boq_coords/rows.py's multi-page stitching guard
(_has_region_below). Regression for a real corpus bug (2026-09-28): when
one page held two BOQ tables, BOTH tables stitched the same following
pages as their unheaded continuation, so every continuation row was
emitted twice (two documents: 124 and 32 exact duplicate rows). Only the
bottom-most table on a page may flow onto the next page.

stdlib-only (unittest), matching the rest of the project.

Run: venv\\Scripts\\python.exe -m unittest tests.test_rows_stitching -v
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boq_coords.rows import _has_region_below


def region(page_no, y0):
    # Only page_no and bbox are read by _has_region_below.
    return SimpleNamespace(page_no=page_no, bbox=(30.0, y0, 565.0, y0 + 100.0))


class TestHasRegionBelow(unittest.TestCase):

    def test_upper_table_on_a_two_table_page_cannot_continue(self):
        upper, lower = region(2, 150.0), region(2, 567.0)
        self.assertTrue(_has_region_below(upper, [upper, lower]))

    def test_lower_table_on_a_two_table_page_can_continue(self):
        upper, lower = region(2, 150.0), region(2, 567.0)
        self.assertFalse(_has_region_below(lower, [upper, lower]))

    def test_tables_on_other_pages_do_not_block(self):
        this_page, next_page = region(2, 150.0), region(3, 600.0)
        self.assertFalse(_has_region_below(this_page, [this_page, next_page]))

    def test_single_table_can_continue(self):
        only = region(0, 200.0)
        self.assertFalse(_has_region_below(only, [only]))


if __name__ == "__main__":
    unittest.main()
