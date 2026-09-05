"""Tier A 규칙 라벨의 정밀도 측정.

    uv run python -m clausegraph.goldset.label_eval

기준 라벨(reference_labels.jsonl)과 규칙 결과(labels.jsonl)를 맞춰 본다.
불일치는 요약하지 않고 전부 출력한다 — 규칙을 고칠 지점이 거기 있다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .labeler import Label, LabeledCase

REFERENCE_FILENAME = "reference_labels.jsonl"
LABELS_FILENAME = "labels.jsonl"


def load_reference(path: Path, batch: int | None = None) -> dict[int, tuple[str, str]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if batch is not None:
        rows = [row for row in rows if row.get("batch") == batch]
    return {row["case_slno"]: (row["label"], row.get("note", "")) for row in rows}


def load_predictions(path: Path) -> dict[int, LabeledCase]:
    items = [
        LabeledCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    return {item.case_slno: item for item in items}


def run(goldset_dir: Path, batch: int | None) -> int:
    reference = load_reference(goldset_dir / REFERENCE_FILENAME, batch)
    predictions = load_predictions(goldset_dir / LABELS_FILENAME)

    missing = sorted(set(reference) - set(predictions))
    if missing:
        print(f"규칙 결과에 없는 기준 라벨 {len(missing)}건: {missing}", file=sys.stderr)

    matched = [(slno, ref, predictions[slno]) for slno, (ref, _) in reference.items()
               if slno in predictions]
    hits = [row for row in matched if row[1] == row[2].label]
    misses = [row for row in matched if row[1] != row[2].label]

    total = len(matched)
    scope = f"batch {batch}" if batch else "전체"
    print(f"기준 라벨 {total}건 대조 ({scope})")
    print(f"일치 {len(hits)}건 — 정확도 **{len(hits) / total:.1%}**\n")

    print("확신도별 정확도:")
    for confidence in ("HIGH", "MEDIUM", "NONE"):
        bucket = [row for row in matched if row[2].confidence == confidence]
        if not bucket:
            continue
        correct = sum(1 for row in bucket if row[1] == row[2].label)
        print(f"  {confidence:6s} {correct:2d}/{len(bucket):2d}  {correct / len(bucket):6.1%}")

    print("\n라벨별 (기준 기준):")
    for label in Label:
        bucket = [row for row in matched if row[1] == label.value]
        if not bucket:
            continue
        correct = sum(1 for row in bucket if row[1] == row[2].label)
        print(f"  {label.value:13s} {correct:2d}/{len(bucket):2d}")

    if misses:
        print(f"\n불일치 {len(misses)}건:")
        for slno, ref, pred in misses:
            print(f"  [{slno}] 기준={ref} 규칙={pred.label} ({pred.confidence})")
            print(f"        {pred.title[:64]}")
            print(f"        기준 근거: {reference[slno][1]}")
            if pred.evidence:
                print(f"        규칙 근거: {pred.evidence}")
    else:
        print("\n불일치 없음.")

    print("\n혼동 (기준 -> 규칙):")
    for (ref, pred), count in Counter((row[1], row[2].label.value) for row in misses).most_common():
        print(f"  {count:2d}  {ref} -> {pred}")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Tier A 라벨 정밀도 측정")
    parser.add_argument("--in", dest="goldset_dir", type=Path, default=Path("data/goldset"))
    parser.add_argument("--batch", type=int, default=None, help="표본 회차만 대조")
    args = parser.parse_args()
    return run(args.goldset_dir, args.batch)


if __name__ == "__main__":
    raise SystemExit(main())
