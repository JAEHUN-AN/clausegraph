"""트리아지를 실제 분쟁 21건으로 잰다.

    uv run python -m clausegraph.goldset.triage_eval

## 이 평가가 쉬운 문제를 푸는 것처럼 보이지 않게 하려면

`needs` 라벨은 **처리결과**를 읽고 붙였다. 트리아지가 보는 것은 **쟁점
문장**뿐이다. 결론을 보고 붙인 라벨을 결론 없이 맞히는 일이라, 라벨과
입력 사이에 실제로 정보 격차가 있다.

그 격차 때문에 못 맞히는 범주가 있고, 그건 정직하게 0으로 둔다.

- **판례**는 예측하지 않는다. 쟁점 문장에는 판례의 흔적이 없다. 예측하는
  척하면 사후 지식을 쓰는 것이 된다.

## 한 숫자로 합치지 않는다

범주마다 무게가 다르다.

- **상품약관정의를 놓치면** — 정의가 없는데 있는 줄 알고 표준약관 조문을
  인용해 답하게 된다. 지어낸 근거가 된다.
- **의무기록을 헛짚으면** — 문서만으로 될 일에 서류를 더 내라고 한다.
  청구인을 헛걸음시킨다.

그래서 범주별 정밀도·재현율을 따로 낸다.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from ..agents.definition_terms import load_index
from ..agents.definition_triage import Need, triage
from .definition_anatomy import load_gold

DEFAULT_GOLDSET_DIR = Path("data/goldset")
DEFAULT_LAW_DIR = Path("data/law/parsed")

# 쟁점 문장만으로는 알 수 없는 범주. 예측하지 않으므로 채점에서도 뺀다.
NOT_PREDICTED = (Need.PRECEDENT,)


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        hit = self.tp + self.fp
        return self.tp / hit if hit else 0.0

    @property
    def recall(self) -> float:
        real = self.tp + self.fn
        return self.tp / real if real else 0.0


def issue_of(goldset_dir: Path, case_slno: int) -> str:
    path = goldset_dir / "cases" / f"{case_slno}.json"
    case = json.loads(path.read_text(encoding="utf-8"))
    return (case["sections"].get("쟁점") or "").strip()


def run(goldset_dir: Path, law_dir: Path) -> int:
    gold = load_gold(goldset_dir)
    try:
        index = load_index(law_dir)
    except FileNotFoundError as exc:
        print(f"약관 수집본이 없어 용어 색인을 만들 수 없다 — {exc}")
        return 1

    print(
        f"용어 색인: 정의 {len(index.defined)}개 / 정의를 미룬 자리"
        f" {sorted(index.deferred_to)} / 정의 조문 {index.article_count}개"
    )

    scores = {need: Counts() for need in Need if need not in NOT_PREDICTED}
    term_found = 0
    rows = []
    for item in gold:
        issue = issue_of(goldset_dir, item.case_slno)
        result = triage(issue, index)
        if result.term:
            term_found += 1

        want = {Need(name) for name in item.needs} - set(NOT_PREDICTED)
        got = set(result.needs) - set(NOT_PREDICTED)
        for need, counts in scores.items():
            if need in want and need in got:
                counts.tp += 1
            elif need in got:
                counts.fp += 1
            elif need in want:
                counts.fn += 1
        rows.append((item.case_slno, result.term, sorted(want), sorted(got)))

    print(f"\n용어를 가린 사례 {term_found}/{len(gold)}")
    print("\n=== 범주별 (판례는 예측하지 않으므로 제외)")
    print(f"{'범주':12s} {'정밀도':>8s} {'재현율':>8s}   맞음/헛짚음/놓침")
    for need, counts in scores.items():
        print(
            f"{str(need):12s} {counts.precision:>7.0%} {counts.recall:>8.0%}"
            f"   {counts.tp}/{counts.fp}/{counts.fn}"
        )

    print("\n=== 건별 (기대 -> 예측)")
    for slno, term, want, got in rows:
        mark = " " if set(want) == set(got) else "!"
        print(f" {mark}[{slno:3d}] {term or '-':10s} {want} -> {got}")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="DEFINITION 트리아지 평가")
    parser.add_argument("--goldset-dir", type=Path, default=DEFAULT_GOLDSET_DIR)
    parser.add_argument("--law-dir", type=Path, default=DEFAULT_LAW_DIR)
    args = parser.parse_args()
    return run(args.goldset_dir, args.law_dir)


if __name__ == "__main__":
    raise SystemExit(main())
