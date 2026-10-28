"""Measure exact span-level PICO precision and recall on the starter gold set."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.pico_rules import tag_abstract


FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "pico_gold.json"


def main() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    totals = {label: Counter() for label in ("P", "I", "C", "O")}
    for record in fixture["records"]:
        gold = {
            (span["label"], span["start_char"], span["end_char"])
            for span in record["spans"]
        }
        predicted = {
            (span.label, span.start_char, span.end_char)
            for span in tag_abstract(record["abstract"])
        }
        for label in totals:
            gold_label = {span for span in gold if span[0] == label}
            predicted_label = {span for span in predicted if span[0] == label}
            totals[label]["tp"] += len(gold_label & predicted_label)
            totals[label]["fp"] += len(predicted_label - gold_label)
            totals[label]["fn"] += len(gold_label - predicted_label)

    output = {"records": len(fixture["records"]), "matching": "exact_offsets", "labels": {}}
    for label, counts in totals.items():
        precision = counts["tp"] / (counts["tp"] + counts["fp"]) if counts["tp"] + counts["fp"] else 0.0
        recall = counts["tp"] / (counts["tp"] + counts["fn"]) if counts["tp"] + counts["fn"] else 0.0
        output["labels"][label] = {
            "tp": counts["tp"],
            "fp": counts["fp"],
            "fn": counts["fn"],
            "precision": round(precision, 3),
            "recall": round(recall, 3),
        }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
