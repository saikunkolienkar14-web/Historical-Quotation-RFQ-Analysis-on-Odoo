"""
Raw page-text sidecar (plan §5, constraint "nothing lost"). Verbatim
page.get_text("text") per page - deliberately NOT run through v1's
clean_text(), which collapses repeated whitespace and is itself
layout-destructive; that has no place in an archival copy whose whole
purpose is to preserve what the flattened .txt tree destroys (page
boundaries and geometry).
"""
from __future__ import annotations

import json
from pathlib import Path


def write_pages_jsonl(path: Path, doc, letterhead_bands: list[tuple[float, float]] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for page_no in range(doc.page_count):
            page = doc[page_no]
            words = page.get_text("words")
            try:
                n_tables = len(page.find_tables().tables)
            except Exception:  # noqa: BLE001
                n_tables = 0
            record = {
                "page": page_no,
                "width": page.rect.width,
                "height": page.rect.height,
                "rotation": page.rotation,
                "text": page.get_text("text"),
                "n_words": len(words),
                "n_tables": n_tables,
                "letterhead_bands": letterhead_bands or [],
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
