"""DEFINITION 21건이 무엇에 기대어 갈렸는지 센다.

    uv run python -m clausegraph.goldset.definition_anatomy

notes/024가 남긴 가장 큰 덩어리다.

> DEFINITION — 약관 용어(수술·입원·장해)의 범위 해석 — **21건 (30.9%)**
> 답할 수 있는 유형 30/68. **가장 큰 덩어리는 못 푼다.**

"못 푼다"를 "LLM을 붙이면 풀린다"로 읽고 싶어지는 자리다. 붙이기 전에
**무엇이 있어야 풀리는지**부터 셌다. 답은 모델이 아니었다(notes/034).

## 이 스크립트가 내는 것

1. **판정 쏠림** — 다수 클래스만 찍는 모형의 점수. 이게 높으면 정확도로는
   아무것도 못 잰다.
2. **무엇이 있어야 갈렸나** — 상품 약관 정의 / 상품 분류표 / 판례 / 의무기록
3. **그중 내 수집본에 있는 것** — 표준약관에서 실제로 찾아본다

3번이 핵심이다. 세어 보기 전에는 "표준약관에 수술의 정의가 있겠지"라고
생각했는데, 제2조(용어의 정의)는 계약자·보험수익자·장해만 정의한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

DEFAULT_GOLDSET_DIR = Path("data/goldset")
DEFAULT_LAW_DIR = Path("data/law/parsed")
GOLD_FILENAME = "definition_gold.jsonl"
TYPES_FILENAME = "dispute_types.jsonl"

# 분쟁이 실제로 기댄 것. 라벨 값과 같아야 한다.
NEEDS = ("상품약관정의", "상품분류표", "판례", "의무기록")

# 표준약관 안에서 찾아볼 것. 분쟁의 처리결과가 이 이름들로 판단했다.
LOOKED_FOR = (
    "수술분류표",
    "급성심근경색증 분류표",
    "장해분류표",
    "재해분류표",
)


@dataclass(frozen=True)
class Gold:
    case_slno: int
    term: str
    verdict: str
    needs: tuple[str, ...]
    note: str


def load_gold(goldset_dir: Path) -> list[Gold]:
    path = goldset_dir / GOLD_FILENAME
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [
        Gold(
            case_slno=row["case_slno"],
            term=row["term"],
            verdict=row["verdict"],
            needs=tuple(row["needs"]),
            note=row["note"],
        )
        for row in rows
    ]


def definition_slnos(goldset_dir: Path) -> set[int]:
    path = goldset_dir / TYPES_FILENAME
    return {
        json.loads(line)["case_slno"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line)["dispute_type"] == "DEFINITION"
    }


def corpus_mentions(law_dir: Path) -> dict[str, tuple[int, int]]:
    """표준약관에서 각 이름이 (언급된 횟수, 표 자체가 조문으로 있는 수).

    언급은 많은데 표가 없는 경우를 가려내려고 둘을 따로 센다. `장해분류표`가
    그렇다 — 조문이 32번 가리키는데 표는 수집본에 없다.
    """
    counts: dict[str, tuple[int, int]] = {}
    files = sorted(law_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"파싱된 약관이 없다: {law_dir}")

    latest = files[-1]
    data = json.loads(latest.read_text(encoding="utf-8"))
    articles = data.get("articles", [])
    blob = json.dumps(data, ensure_ascii=False)
    for name in LOOKED_FOR:
        mentions = blob.count(name)
        as_article = sum(1 for a in articles if name in (a.get("title") or ""))
        counts[name] = (mentions, as_article)
    return counts


def report(goldset_dir: Path, law_dir: Path) -> int:
    gold = load_gold(goldset_dir)
    expected = definition_slnos(goldset_dir)
    labeled = {item.case_slno for item in gold}
    missing = expected - labeled
    extra = labeled - expected

    print(f"DEFINITION {len(expected)}건 / 라벨 {len(gold)}건")
    if missing:
        print(f"  ! 라벨이 없는 사례 {sorted(missing)}")
    if extra:
        print(f"  ! DEFINITION이 아닌데 라벨된 사례 {sorted(extra)}")

    verdicts = Counter(item.verdict for item in gold)
    top, top_count = verdicts.most_common(1)[0]
    print("\n=== 판정 쏠림")
    for name, count in verdicts.most_common():
        print(f"  {name:4s} {count:2d}건")
    print(
        f"  **다수 클래스({top})만 찍는 모형의 정확도 {top_count / len(gold):.1%}** —"
        " 이 표본으로는 정확도를 지표로 쓸 수 없다."
    )

    needs = Counter(name for item in gold for name in item.needs)
    print("\n=== 무엇이 있어야 갈렸나 (한 건이 여럿을 요구할 수 있다)")
    for name in NEEDS:
        count = needs[name]
        print(f"  {name:10s} {count:2d}건  ({count / len(gold):.0%})")
    only_terms = [item for item in gold if item.needs == ("상품약관정의",)]
    print(
        f"  상품 약관의 정의 문구 하나만으로 갈린 사례: {len(only_terms)}건"
        f" ({len(only_terms) / len(gold):.0%})"
    )

    print("\n=== 그 근거가 내 수집본(표준약관)에 있는가")
    try:
        counts = corpus_mentions(law_dir)
    except FileNotFoundError as exc:
        print(f"  건너뜀 — {exc}")
        return 0
    for name, (mentions, as_article) in counts.items():
        state = "표 있음" if as_article else "**표 없음**"
        print(f"  {name:16s} 언급 {mentions:3d}회 / 조문 {as_article}개 — {state}")
    print(
        "  표준약관 제2조(용어의 정의)는 계약자·보험수익자·장해만 정의한다."
        " **`수술`과 `입원`의 정의는 없다** — 회사 상품 약관에 있다."
    )
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="DEFINITION 유형 해부")
    parser.add_argument("--goldset-dir", type=Path, default=DEFAULT_GOLDSET_DIR)
    parser.add_argument("--law-dir", type=Path, default=DEFAULT_LAW_DIR)
    args = parser.parse_args()
    return report(args.goldset_dir, args.law_dir)


if __name__ == "__main__":
    raise SystemExit(main())
