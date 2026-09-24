"""
Regression pin: re-extract every document in tests/snapshots/
boq_rows_snapshot.json and require the rows to match the snapshot
exactly (every field, full text).

The snapshot is only ever written from output a human has verified as
correct (scripts/snapshot_rows.py) - first entries: Q24X10030 base/R1/R2,
verified by the user 2026-09-23. Row segmentation in this area has been
fixed and reverted several times because a change that helped one
document silently broke another that had no test; this pins them.

Skipped when the snapshot or a PDF is absent (both are gitignored - they
hold real commercial data).

Run: venv\\Scripts\\python.exe -m unittest tests.test_boq_snapshots -v
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from snapshot_rows import snapshot_document  # noqa: E402

SNAPSHOT = ROOT / "tests" / "snapshots" / "boq_rows_snapshot.json"


@unittest.skipUnless(SNAPSHOT.exists(), "row snapshot not present")
class TestBoqRowSnapshots(unittest.TestCase):

    def test_documents_match_verified_snapshot(self):
        expected_all = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        for rel_path, expected in expected_all.items():
            pdf = ROOT / rel_path
            with self.subTest(document=Path(rel_path).stem[:10]):
                if not pdf.exists():
                    self.skipTest("PDF not present")
                actual = snapshot_document(pdf)
                self.assertEqual(len(actual), len(expected), "table count")
                for t, (act_t, exp_t) in enumerate(zip(actual, expected)):
                    self.assertEqual(len(act_t), len(exp_t), f"table {t} row count")
                    for r, (act_r, exp_r) in enumerate(zip(act_t, exp_t)):
                        self.assertEqual(act_r, exp_r, f"table {t} row {r}")


if __name__ == "__main__":
    unittest.main()
