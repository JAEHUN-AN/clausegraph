"""Tier A 라벨 실행 CLI.

    uv run python -m clausegraph.goldset.label_cli --in data/goldset

라벨 결과(labels.jsonl)와, 수동 검수용 워크시트(worksheet.md)를 낸다.
워크시트는 규칙이 매긴 답을 **가린 채** 사례 본문만 보여준다 — 규칙을
보고 라벨을 맞추면 정밀도 측정이 무의미해지기 때문이다.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from pathlib import Path

from .labeler import Label, LabeledCase, is_in_scope, label_case
from .models import DisputeCase

LABELS_FILENAME = "labels.jsonl"
WORKSHEET_FILENAME = "worksheet.md"
REFERENCE_FILENAME = "reference_labels.jsonl"
DEFAULT_SAMPLE_SIZE = 30
SAMPLE_SEED = 20260905


def load_cases(goldset_dir: Path) -> list[DisputeCase]:
    paths = sorted((goldset_dir / "cases").glob("*.json"), key=lambda p: int(p.stem))
    if not paths:
        raise FileNotFoundError(
            f"수집된 사례가 없다: {goldset_dir / 'cases'} — collect를 먼저 돌릴 것"
        )
    return [DisputeCase.model_validate_json(p.read_text(encoding="utf-8")) for p in paths]


def already_labeled(goldset_dir: Path) -> set[int]:
    """이미 기준 라벨이 있는 사례. 재측정은 늘 새 표본으로 해야 한다 —
    규칙을 고친 뒤 같은 표본으로 다시 재면 그 수치는 독립적이지 않다."""
    path = goldset_dir / REFERENCE_FILENAME
    if not path.exists():
        return set()
    import json

    return {
        json.loads(line)["case_slno"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    }


def write_worksheet(
    path: Path, cases: list[DisputeCase], sample_size: int, seed: int, exclude: set[int]
) -> list[int]:
    """수동 검수용 워크시트. 규칙의 답은 싣지 않는다."""
    pool = [case for case in cases if case.case_slno not in exclude]
    if not pool:
        raise ValueError("표본을 뽑을 사례가 남지 않았다 — 범위 내 사례가 모두 라벨링됐다")
    rng = random.Random(seed)
    sample = rng.sample(pool, min(sample_size, len(pool)))
    sample.sort(key=lambda c: c.case_slno)

    lines = [
        "# Tier A 수동 검수 워크시트",
        "",
        f"라벨 미보유 {len(pool)}건에서 시드 {seed}으로 뽑은 {len(sample)}건"
        + (f" (기존 라벨 {len(exclude)}건 제외)." if exclude else "."),
        "규칙이 매긴 답은 일부러 싣지 않았다 — 보고 나면 정밀도 측정이 무의미해진다.",
        "",
        "각 사례의 `label:` 줄에 PAID / DENIED / PARTIAL / NOT_CLAIM 중 하나를 적는다.",
        "",
    ]
    for case in sample:
        lines += [
            f"## caseSlno {case.case_slno} — {case.ref.cvpl}",
            "",
            f"**{case.ref.title}**",
            "",
            "```",
            case.sections.get("처리결과", case.body_text)[:700].strip(),
            "```",
            "",
            "label: ",
            "",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return [case.case_slno for case in sample]


def run(goldset_dir: Path, sample_size: int, seed: int) -> int:
    cases = load_cases(goldset_dir)
    scoped = [case for case in cases if is_in_scope(case)]
    print(f"수집 {len(cases)}건 중 범위 내 {len(scoped)}건")

    labeled = [label_case(case) for case in scoped]
    (goldset_dir / LABELS_FILENAME).write_text(
        "\n".join(item.model_dump_json() for item in labeled) + "\n", encoding="utf-8"
    )

    _report(labeled)

    exclude = already_labeled(goldset_dir)
    sampled = write_worksheet(
        goldset_dir / WORKSHEET_FILENAME, scoped, sample_size, seed, exclude
    )
    print(f"\n검수 워크시트: {goldset_dir / WORKSHEET_FILENAME} ({len(sampled)}건, 시드 {seed})")
    return 0


def _report(labeled: list[LabeledCase]) -> None:
    print("\n라벨 분포:")
    for label, count in Counter(item.label for item in labeled).most_common():
        print(f"  {count:4d}  {label}")

    print("\n확신도:")
    for conf, count in Counter(item.confidence for item in labeled).most_common():
        print(f"  {count:4d}  {conf}")

    review = [item for item in labeled if item.label == Label.NEEDS_REVIEW]
    if review:
        print(f"\n신호 충돌 {len(review)}건 — 규칙이 손대지 못한 지점:")
        for item in review:
            print(f"  {item.case_slno}: 제목={item.title_signal} 처리결과={item.outcome_signal}")
            print(f"     {item.title[:60]}")

    unknown = [item for item in labeled if item.label == Label.UNKNOWN]
    if unknown:
        print(f"\n미분류 {len(unknown)}건:")
        for item in unknown:
            print(f"  {item.case_slno}: {item.title[:60]}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Tier A 규칙 라벨링")
    parser.add_argument("--in", dest="goldset_dir", type=Path, default=Path("data/goldset"))
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--seed", type=int, default=SAMPLE_SEED)
    args = parser.parse_args()
    return run(args.goldset_dir, args.sample, args.seed)


if __name__ == "__main__":
    raise SystemExit(main())
