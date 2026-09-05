"""Tier B 라벨 실행 CLI — 분쟁이 무엇을 두고 갈렸는지 센다.

    uv run python -m clausegraph.goldset.type_cli --in data/goldset

내는 것은 두 가지다.

1. `dispute_types.jsonl` — 사례별 분쟁 유형
2. **범위 표** — 유형별로 이 시스템이 답할 수 있는지

두 번째가 이 CLI의 목적이다. 실제 분쟁의 몇 %가 이 구조로 풀리는지를
숫자로 내야, "recall 100%"가 무엇에 대한 100%인지 말할 수 있다.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from .dispute_type import HANDLED, DisputeType, TypedCase, type_case
from .label_cli import load_cases
from .labeler import Label, is_in_scope
from .models import DisputeCase

TYPES_FILENAME = "dispute_types.jsonl"
LABELS_FILENAME = "labels.jsonl"


def in_scope_claims(goldset_dir: Path, cases: list[DisputeCase]) -> list[DisputeCase]:
    """청구 분쟁이면서 표준약관으로 다룰 수 있는 사례.

    Tier A가 `NOT_CLAIM`으로 본 것은 뺀다 — 계약 취소나 판매 행위 분쟁에
    분쟁 유형을 매기면 범위 계산이 흐려진다. Tier A 라벨이 없는 사례도
    뺀다. 두 라벨은 같은 모집단 위에 있어야 한다.
    """
    import json

    path = goldset_dir / LABELS_FILENAME
    if not path.exists():
        raise FileNotFoundError(
            f"Tier A 라벨이 없다: {path} — label_cli를 먼저 돌릴 것"
        )
    labels = {
        json.loads(line)["case_slno"]: json.loads(line)["label"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    return [
        case
        for case in cases
        if labels.get(case.case_slno) not in (None, Label.NOT_CLAIM.value)
        and is_in_scope(case)
    ]


def write_types(goldset_dir: Path, typed: list[TypedCase]) -> Path:
    path = goldset_dir / TYPES_FILENAME
    path.write_text(
        "\n".join(case.model_dump_json() for case in typed) + "\n", encoding="utf-8"
    )
    return path


def report(typed: list[TypedCase]) -> str:
    counts = Counter(case.dispute_type for case in typed)
    total = len(typed)
    handled = sum(n for t, n in counts.items() if HANDLED[t])

    lines = [f"분쟁 유형 (청구 분쟁 × 표준약관 범위 {total}건)", ""]
    for dispute_type, count in counts.most_common():
        mark = "O" if HANDLED[dispute_type] else "X"
        lines.append(
            f"  [{mark}] {dispute_type.value:11s} {count:3d}  ({count / total * 100:4.1f}%)"
        )
    lines.append("")
    lines.append(
        f"  이 시스템이 답할 수 있는 유형 {handled}/{total} "
        f"({handled / total * 100:.1f}%)"
    )
    lines.append("")
    lines.append(
        "  가장 큰 덩어리는 DEFINITION이고, 이 시스템은 그걸 못 푼다. "
        "조문을 찾아 주는 문제가 아니라 조문을 해석하는 문제다."
    )
    return "\n".join(lines)


def unknowns(typed: list[TypedCase], cases: list[DisputeCase]) -> list[str]:
    """유형을 못 매긴 사례. 감추지 않고 그대로 낸다."""
    by_slno = {case.case_slno: case for case in cases}
    lines = []
    for case in typed:
        if case.dispute_type is not DisputeType.UNKNOWN:
            continue
        issue = " ".join(by_slno[case.case_slno].sections.get("쟁점", "").split())
        lines.append(f"  [{case.case_slno}] {issue[:100] or '(쟁점 없음)'}")
    return lines


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Tier B 분쟁 유형 라벨")
    parser.add_argument("--in", dest="goldset_dir", default="data/goldset")
    args = parser.parse_args()

    goldset_dir = Path(args.goldset_dir)
    cases = load_cases(goldset_dir)
    targets = in_scope_claims(goldset_dir, cases)
    typed = [type_case(case) for case in targets]

    path = write_types(goldset_dir, typed)
    print(f"수집 사례 {len(cases)}건 -> 대상 {len(targets)}건")
    print(f"라벨 파일 {path}\n")
    print(report(typed))

    missing = unknowns(typed, targets)
    if missing:
        print(f"\n유형을 못 매긴 사례 {len(missing)}건:")
        print("\n".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
