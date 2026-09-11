"""
Score an LLM's Structured Extraction Output Against Ground Truth
==================================================================

Pure stdlib (no pandas/numpy) so this file can be pasted into the Colab
notebook unchanged (llm_fewshot/benchmark_models.ipynb) as well as run
locally against the CSVs in llm_fewshot/benchmark_set/.

Ground truth field names follow docs/DATA_DICTIONARY.md
(quotations.csv / quotation_items.csv). The model output is expected to
be JSON shaped like:

    {
      "quotation_number": "...", "customer": "...", "subject": "...",
      "currency": "...", "subtotal": "...", "tax": "...",
      "discount": "...", "grand_total": "...",
      "items": [
        {"item_no": "...", "description": "...", "make": "...",
         "model": "...", "quantity": "...", "unit": "...",
         "unit_price": "...", "total_price": "..."},
        ...
      ]
    }

Missing/blank fields in either side are treated as equal (both empty),
since the parser leaves plenty of fields legitimately blank.
"""

import csv
import json
import re
import sys

csv.field_size_limit(sys.maxsize)

DOCUMENT_FIELDS = [
    "quotation_number", "customer", "subject", "currency",
    "subtotal", "tax", "discount", "grand_total",
]
ITEM_FIELDS = [
    "description", "make", "model", "quantity", "unit",
    "unit_price", "total_price",
]


def normalize(value):
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"[.,]+$", "", text)  # trailing punctuation (e.g. "/-")
    text = re.sub(r"\s+", " ", text)
    return text


def fields_match(expected, actual):
    return normalize(expected) == normalize(actual)


def score_document(ground_truth_row, model_output):
    """ground_truth_row: dict from quotations_ground_truth.csv.
    model_output: parsed JSON dict (or None if parsing failed)."""
    if model_output is None:
        return {"fields_correct": 0, "fields_total": len(DOCUMENT_FIELDS), "accuracy": 0.0}

    correct = 0
    for field in DOCUMENT_FIELDS:
        if fields_match(ground_truth_row.get(field, ""), model_output.get(field, "")):
            correct += 1
    return {
        "fields_correct": correct,
        "fields_total": len(DOCUMENT_FIELDS),
        "accuracy": correct / len(DOCUMENT_FIELDS),
    }


def score_items(ground_truth_items, model_items):
    """ground_truth_items: list of dicts (quotation_items_ground_truth.csv
    rows for one example_id). model_items: model_output.get("items", [])."""
    if not ground_truth_items:
        return {"precision": None, "recall": None, "field_accuracy": None}

    model_items = model_items or []
    matched_pairs = []
    used_model_idx = set()

    # Match by item_no first, fall back to position for unmatched rows.
    for gt_row in ground_truth_items:
        gt_item_no = normalize(gt_row.get("item_no", ""))
        match_idx = None
        if gt_item_no:
            for idx, model_item in enumerate(model_items):
                if idx in used_model_idx:
                    continue
                if normalize(model_item.get("item_no", "")) == gt_item_no:
                    match_idx = idx
                    break
        if match_idx is None:
            for idx in range(len(model_items)):
                if idx not in used_model_idx:
                    match_idx = idx
                    break
        if match_idx is not None:
            used_model_idx.add(match_idx)
            matched_pairs.append((gt_row, model_items[match_idx]))

    recall = len(matched_pairs) / len(ground_truth_items)
    precision = len(matched_pairs) / len(model_items) if model_items else 0.0

    total_fields = 0
    correct_fields = 0
    for gt_row, model_item in matched_pairs:
        for field in ITEM_FIELDS:
            total_fields += 1
            if fields_match(gt_row.get(field, ""), model_item.get(field, "")):
                correct_fields += 1

    return {
        "precision": precision,
        "recall": recall,
        "field_accuracy": (correct_fields / total_fields) if total_fields else None,
    }


def parse_model_json(raw_text):
    """Best-effort JSON extraction from raw model output (handles
    ```json fenced blocks and surrounding prose)."""
    if not raw_text:
        return None
    text = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    else:
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
            text = text[brace_start:brace_end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def score_example(example_id, ground_truth_rows, ground_truth_items_rows, raw_model_output):
    """ground_truth_rows: list of quotations_ground_truth.csv rows.
    ground_truth_items_rows: list of quotation_items_ground_truth.csv rows.
    raw_model_output: the model's raw text response."""
    gt_row = next((r for r in ground_truth_rows if r["example_id"] == example_id), None)
    if gt_row is None:
        raise ValueError(f"No ground truth for {example_id}")

    gt_items = [r for r in ground_truth_items_rows if r["example_id"] == example_id]
    model_output = parse_model_json(raw_model_output)

    doc_score = score_document(gt_row, model_output or {})
    item_score = score_items(gt_items, (model_output or {}).get("items"))

    return {
        "example_id": example_id,
        "json_parsed": model_output is not None,
        **{f"doc_{k}": v for k, v in doc_score.items()},
        **{f"item_{k}": v for k, v in item_score.items()},
    }


def aggregate_scores(example_scores):
    """example_scores: list of dicts from score_example(). Returns one
    summary dict, e.g. for one row of the model comparison table."""
    n = len(example_scores)
    if n == 0:
        return {}

    json_parse_rate = sum(1 for s in example_scores if s["json_parsed"]) / n
    doc_accuracy = sum(s["doc_accuracy"] for s in example_scores) / n

    item_precisions = [s["item_precision"] for s in example_scores if s["item_precision"] is not None]
    item_recalls = [s["item_recall"] for s in example_scores if s["item_recall"] is not None]
    item_field_accuracies = [s["item_field_accuracy"] for s in example_scores if s["item_field_accuracy"] is not None]

    return {
        "n_examples": n,
        "json_parse_rate": json_parse_rate,
        "mean_document_field_accuracy": doc_accuracy,
        "mean_item_precision": sum(item_precisions) / len(item_precisions) if item_precisions else None,
        "mean_item_recall": sum(item_recalls) / len(item_recalls) if item_recalls else None,
        "mean_item_field_accuracy": sum(item_field_accuracies) / len(item_field_accuracies) if item_field_accuracies else None,
    }


if __name__ == "__main__":
    # Quick self-check against benchmark_set/, using a hand-written fake
    # model output, so the scorer is verified before it's ever run in
    # Colab against a real model.
    from pathlib import Path

    benchmark_dir = Path(__file__).resolve().parent / "benchmark_set"
    with open(benchmark_dir / "quotations_ground_truth.csv", newline="", encoding="utf-8-sig") as f:
        gt_rows = list(csv.DictReader(f))
    items_path = benchmark_dir / "quotation_items_ground_truth.csv"
    gt_item_rows = []
    if items_path.exists():
        with open(items_path, newline="", encoding="utf-8-sig") as f:
            gt_item_rows = list(csv.DictReader(f))

    if not gt_rows:
        print("No ground truth found - run select_benchmark_set.py first.")
        sys.exit(1)

    example_id = gt_rows[0]["example_id"]
    fake_output = json.dumps({
        field: gt_rows[0].get(field, "") for field in DOCUMENT_FIELDS
    })
    result = score_example(example_id, gt_rows, gt_item_rows, f"```json\n{fake_output}\n```")
    print("Self-check (perfect document match expected):")
    print(json.dumps(result, indent=2))
