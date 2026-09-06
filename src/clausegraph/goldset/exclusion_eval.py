"""실제 분쟁조정사례로 면책검증을 잰다.

    uv run --extra graph python -m clausegraph.goldset.exclusion_eval

## 왜 또 만드나

`rag/evaluate.py`의 18문항은 **내가 쓴 문장**이다. 약관을 읽고 만들었으니
약관의 말투가 배어 있고, 그만큼 쉬울 수 있다. 이 평가는 다르다 —
**금감원이 공개한 분쟁 사례의 쟁점 문장을 그대로 넣는다.**

대상은 Tier B가 `EXCLUSION`으로 분류한 13건이다(notes/024). 사례마다
정답이 되는 면책 조항의 표지 낱말을 손으로 정해 두었다
(`exclusion_gold.jsonl`).

## 세 가지를 함께 낸다

1. **정답 조항을 짚었는가** — 표지 낱말이 들어간 조항이 히트에 있는가
2. **면책이 아닌 사례에 걸리지 않았는가** — 부담보 해제처럼 면책 조항과
   무관한 쟁점 3건은 히트가 없어야 맞다
3. **히트가 몇 건인가** — 사례 하나에 "걸릴 수도 있다"를 열네 건 내놓으면
   심사자에게는 아무 도움이 안 된다. 정답률만 보면 이게 안 보인다.

세 번째가 이 평가를 만든 이유다. 조사 붙은 어절을 변별어로 세던 시절에는
정답률이 9/10이었지만 히트가 178건이었다(notes/025).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from neo4j import GraphDatabase

from ..agents.exclusion import screen
from ..agents.extract import extract_claim
from ..agents.terminology import lookup
from .label_cli import load_cases

GOLD_FILENAME = "exclusion_gold.jsonl"

# 실손 계열 세 상품에 걸어 본다. 사례가 어느 상품인지 적어 두지 않으므로
# 하나라도 걸리면 짚은 것으로 센다.
PRODUCTS = (
    "기본형 실손의료보험(급여 실손의료비)",
    "실손의료보험 특별약관1(중증 비급여 실손의료비)",
    "실손의료보험 특별약관2(비중증 비급여 실손의료비)",
)
ENROLLED_ON = date(2026, 7, 1)
VERSION = "20260506"


@dataclass(frozen=True)
class Outcome:
    case_slno: int
    gold_keyword: str | None
    hits: int
    found: bool


def evaluate(driver, cases: dict[int, str], gold: list[dict]) -> list[Outcome]:
    outcomes = []
    for row in gold:
        slno = row["case_slno"]
        issue = cases[slno]
        quotes: list[str] = []
        for product in PRODUCTS:
            claim = extract_claim(str(slno), product, ENROLLED_ON, issue, enrich=lookup)
            hits, _ = screen(driver, claim, VERSION)
            quotes.extend(hit.evidence.quote for hit in hits)
        keyword = row["gold_keyword"]
        outcomes.append(
            Outcome(
                case_slno=slno,
                gold_keyword=keyword,
                hits=len(quotes),
                found=bool(keyword) and any(keyword in quote for quote in quotes),
            )
        )
    return outcomes


def report(outcomes: list[Outcome]) -> str:
    with_gold = [o for o in outcomes if o.gold_keyword]
    without = [o for o in outcomes if not o.gold_keyword]
    found = sum(o.found for o in with_gold)
    false_alarms = sum(bool(o.hits) for o in without)
    total_hits = sum(o.hits for o in outcomes)

    lines = ["", f"=== 실제 분쟁 {len(outcomes)}건 ===", ""]
    for outcome in outcomes:
        if outcome.gold_keyword:
            mark = "O" if outcome.found else "X"
            note = f"정답표지 {outcome.gold_keyword}"
        else:
            mark = "-" if not outcome.hits else "!"
            note = "면책 조항이 아닌 쟁점"
        lines.append(
            f"  [{mark}] {outcome.case_slno:4d}  히트 {outcome.hits:3d}  {note}"
        )

    lines += [
        "",
        f"  정답 조항을 짚은 사례      {found}/{len(with_gold)}",
        f"  면책 아닌 사례의 오발동    {false_alarms}/{len(without)}",
        f"  사례당 평균 히트          {total_hits / len(outcomes):.1f}건",
        "",
        "  히트가 많다고 좋은 것이 아니다 — 그만큼 심사자가 읽어야 한다.",
    ]
    return "\n".join(lines)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="실제 분쟁으로 면책검증 측정")
    parser.add_argument("--in", dest="goldset_dir", default="data/goldset")
    args = parser.parse_args()

    goldset_dir = Path(args.goldset_dir)
    gold_path = goldset_dir / GOLD_FILENAME
    if not gold_path.exists():
        print(f"정답 표지가 없다: {gold_path}", file=sys.stderr)
        return 1
    gold = [
        json.loads(line)
        for line in gold_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    issues = {
        case.case_slno: " ".join(case.sections.get("쟁점", "").split())
        for case in load_cases(goldset_dir)
    }

    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    try:
        outcomes = evaluate(driver, issues, gold)
    finally:
        driver.close()
    print(report(outcomes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
