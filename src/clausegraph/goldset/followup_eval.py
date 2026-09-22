"""후속 질문 분류를 잰다.

    uv run python -m clausegraph.goldset.followup_eval

## 이 평가의 정직성 문제부터

후속 질문 말뭉치는 없다. 금감원이 공개하는 것은 **분쟁 사례의 3인칭
요약**이지 판정을 듣고 난 사람이 되묻는 말이 아니다. 그러니 질문은 내가
써야 하는데, **규칙도 내가 쓰고 문제도 내가 쓰면 그건 평가가 아니다.**

그래서 둘로 나눠 재고 따로 보고한다.

| | 무엇 | 라벨 | 무엇을 재는가 |
|---|---|---|---|
| A | 내가 쓴 질문 | 손 | 분류 체계를 덮는가 (**내 말투 편향 있음**) |
| B | 실제 분쟁 문장 | **기계** | 실제 말에 대고 헛짚지 않는가 |

B에 속임수가 없는 이유가 이 평가의 핵심이다.

## B — 라벨이 기계적으로 정해지는 자리

실제 분쟁 문장 하나를 가져와 **그 문장으로 청구를 만든다**(`extract_claim`).
그리고 **같은 문장을 후속 질문으로 되먹인다.**

그러면 그 문장 안의 모든 사실은 **이미 청구가 아는 값**이다. 따라서

> 이때 `NEW_FACTS`가 뜨면 **무조건 헛짚음이다.**

손으로 라벨을 붙일 필요가 없다. 정의상 정답이 "새 사실 없음"이다.

이게 재는 것은 *"이미 아는 값을 되물었는데 재심사로 보내는"* 실패다.
그 실패가 잦으면 사용자는 무슨 말을 하든 "다시 심사했습니다"를 듣게 되고,
그러면 후속 질문 기능이 있으나 마나가 된다.

실제 분쟁 문장에는 숫자가 많다 — 과실비율 60%, 부상등급 12급, 2017년 가입,
KCD 8차. 내가 쓴 짧은 질문으로는 나오지 않는 밀도다.

## 규칙을 이 표본에 맞춰 고치지 않는다

고치면 그 수치는 독립적이지 않다(`label_cli.already_labeled`와 같은 이유).
틀린 것은 틀린 대로 적고, 무엇을 고쳤는지는 따로 남긴다.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..agents.extract import extract_claim
from ..agents.followup import FollowUp, classify, detect_new_facts
from ..agents.models import Claim
from ..agents.terminology import lookup
from .label_cli import load_cases

DEFAULT_GOLDSET_DIR = Path("data/goldset")

# 기준 청구 — A에서 질문을 걸어 볼 상대. 충치 임플란트 120만원 건이다.
_BASE_CLAIM = Claim(
    claim_id="EVAL",
    product="실손의료보험 특별약관1(중증 비급여 실손의료비)",
    enrolled_on=date(2026, 7, 1),
    diagnosis_codes=("K02",),
    claimed_amount=1_200_000,
    narrative="충치가 심해 임플란트를 했습니다. 비급여 1,200,000원 청구합니다.",
)


@dataclass(frozen=True)
class Written:
    question: str
    expected: FollowUp
    note: str = ""


# 낱말은 실제 분쟁 문장에서 빌렸다 — `부당하다`, `구상청구`, `과소 지급`,
# `임의로 판단`, `지급 거절`. 내 어휘로만 쓰면 규칙이 내 어휘에만 맞는다.
WRITTEN: tuple[Written, ...] = (
    # WHY — 판정을 설명해 달라
    Written("왜 부지급인가요", FollowUp.WHY),
    Written("어째서 안 되는 건가요", FollowUp.WHY),
    Written("지급 거절 이유가 뭔가요", FollowUp.WHY),
    Written("이거 부당하다고 생각하는데 무슨 근거로 안 준 건가요", FollowUp.WHY),
    Written("납득이 안 됩니다", FollowUp.WHY),
    # CLAUSE — 어느 조항인가
    Written("어느 조항 때문인가요", FollowUp.CLAUSE),
    Written("약관 몇 조에 그렇게 적혀 있나요", FollowUp.CLAUSE),
    Written("근거 조항을 보여주세요", FollowUp.CLAUSE),
    # DOCS — 무엇을 내면 되는가
    Written("서류를 뭘 내면 되나요", FollowUp.DOCS),
    Written("추가로 필요한 게 있나요", FollowUp.DOCS),
    Written("보완할 서류 알려주세요", FollowUp.DOCS),
    Written("진단서를 제출하면 되나요", FollowUp.DOCS),
    # AMOUNT — 얼마인가
    Written("지급액이 얼마인가요", FollowUp.AMOUNT),
    Written("얼마나 나오나요", FollowUp.AMOUNT),
    Written("금액이 과소 지급된 것 같은데 어떻게 계산한 건가요", FollowUp.AMOUNT),
    # NEW_FACTS — 판정의 입력이 바뀐다
    Written("작년에 이미 3,000,000원 받았습니다", FollowUp.NEW_FACTS, "누적"),
    Written(
        "작년에 3,000,000원 받았는데 왜 부지급이죠",
        FollowUp.NEW_FACTS,
        "'왜'가 섞여도 새 사실이 먼저다 — 이걸 놓치면 낡은 판정을 설명한다",
    ),
    Written("S82 골절도 함께 진단받았습니다", FollowUp.NEW_FACTS, "모르던 코드"),
    Written("사실 4일간 입원했습니다", FollowUp.NEW_FACTS, "입원일수"),
    Written("금액을 잘못 적었고 2,400,000원입니다", FollowUp.NEW_FACTS, "청구금액"),
    # 새 사실처럼 보이지만 아닌 것 — 이미 아는 값
    Written(
        "1,200,000원이 왜 안 나오나요",
        FollowUp.WHY,
        "이미 아는 금액. 재심사로 보내면 같은 답을 두 번 준다",
    ),
    Written("K02 때문에 안 되는 건가요", FollowUp.WHY, "이미 아는 코드"),
    Written("2026-07-01 가입 맞습니다", FollowUp.UNKNOWN, "아는 날짜를 확인해 준 말"),
    # UNKNOWN — 못 알아들으면 지어내지 않는다
    Written("오늘 날씨 어때요", FollowUp.UNKNOWN),
    Written("담당자 연결해 주세요", FollowUp.UNKNOWN),
)


@dataclass
class WrittenScore:
    total: int = 0
    correct: int = 0
    confusion: Counter[tuple[str, str]] = field(default_factory=Counter)
    wrong: list[tuple[Written, FollowUp]] = field(default_factory=list)

    @property
    def new_facts_missed(self) -> int:
        """새 사실을 놓친 수. 낡은 판정을 설명하게 되는 실패다."""
        return sum(
            1
            for case, got in self.wrong
            if case.expected is FollowUp.NEW_FACTS and got is not FollowUp.NEW_FACTS
        )

    @property
    def new_facts_false(self) -> int:
        """새 사실이 아닌데 재심사로 보낸 수."""
        return sum(
            1
            for case, got in self.wrong
            if case.expected is not FollowUp.NEW_FACTS and got is FollowUp.NEW_FACTS
        )


def score_written() -> WrittenScore:
    result = WrittenScore()
    for case in WRITTEN:
        got = classify(case.question, _BASE_CLAIM)
        result.total += 1
        result.confusion[(str(case.expected), str(got))] += 1
        if got is case.expected:
            result.correct += 1
        else:
            result.wrong.append((case, got))
    return result


@dataclass
class RealScore:
    """실제 문장을 되먹였을 때의 헛짚음.

    `signal`이 없으면 이 점수는 읽을 수 없다. 검출기가 애초에 아무 값도
    보지 못한 사례는 **거저 맞은 것**이고, 그런 사례만 모아 놓고 100%라고
    말하면 아무것도 잰 게 아니다. 처음 돌렸을 때가 그랬다 — 금액 축이
    160건에서 한 번도 발화하지 않은 채 100%가 나왔다(notes/033).
    """

    total: int = 0
    false_new_facts: int = 0
    fields: Counter[str] = field(default_factory=Counter)
    samples: list[tuple[int, str, tuple[str, ...]]] = field(default_factory=list)
    # 축마다 몇 건에서 값이 잡혔는가. 이 축의 분모다.
    signal: Counter[str] = field(default_factory=Counter)
    with_any_signal: int = 0

    @property
    def clean_rate(self) -> float:
        return (self.total - self.false_new_facts) / self.total if self.total else 0.0


def score_real(goldset_dir: Path, limit: int = 0) -> RealScore:
    """실제 분쟁 문장으로 청구를 만들고, 같은 문장을 후속 질문으로 되먹인다.

    그 문장의 사실은 전부 청구가 아는 값이므로 `NEW_FACTS`는 전부 헛짚음이다.
    """
    cases = load_cases(goldset_dir)
    result = RealScore()
    for case in cases[: limit or None]:
        text = case.sections.get("민원내용", "") or case.body_text
        text = text.strip()
        if not text:
            continue

        claim = extract_claim(
            str(case.case_slno),
            "실손의료보험 특별약관1(중증 비급여 실손의료비)",
            date(2026, 7, 1),
            text,
            enrich=lookup,
        )
        # 이 사례가 어느 축을 실제로 건드렸는가. 안 건드린 축은 재지 못한
        # 것이지 통과한 것이 아니다.
        axes = {
            "금액": bool(claim.claimed_amount),
            "진단코드": bool(claim.diagnosis_codes),
            "입원일수": bool(claim.hospital_days),
            "일자": claim.incident_on is not None,
        }
        for name, present in axes.items():
            if present:
                result.signal[name] += 1
        result.with_any_signal += any(axes.values())

        found = detect_new_facts(text, claim)
        result.total += 1
        if found:
            result.false_new_facts += 1
            for name in found:
                result.fields[name] += 1
            if len(result.samples) < 8:
                result.samples.append((case.case_slno, text[:70], found))
    return result


def report_written(result: WrittenScore) -> None:
    print("\n=== A. 내가 쓴 질문 (내 말투 편향 있음)")
    print(f"  분류 정확 {result.correct}/{result.total}")
    print(f"  새 사실을 놓침 {result.new_facts_missed}   (낡은 판정을 설명하게 된다)")
    print(f"  새 사실이 아닌데 재심사 {result.new_facts_false}")
    if result.wrong:
        print("  틀린 것:")
        for case, got in result.wrong:
            note = f"  — {case.note}" if case.note else ""
            print(f"    {case.question[:34]:36s} {case.expected} -> {got}{note}")


def report_real(result: RealScore) -> None:
    print("\n=== B. 실제 분쟁 문장 되먹이기 (라벨은 기계적)")
    print(f"  사례 {result.total}건 — 전부 '새 사실 없음'이 정답")
    print(
        f"  헛짚음 {result.false_new_facts}건"
        f"   깨끗함 {result.clean_rate:.1%}"
    )
    # 이 줄이 없으면 위 수치를 읽을 수 없다.
    axes = ", ".join(f"{name} {count}" for name, count in result.signal.most_common())
    print(f"  값이 잡힌 사례 {result.with_any_signal}/{result.total} — 축별: {axes}")
    print("  값이 안 잡힌 사례는 '거저 맞은' 것이다. 축별 분모를 보고 읽을 것.")
    if result.fields:
        broken = ", ".join(f"{name} {count}" for name, count in result.fields.most_common())
        print(f"  어느 항목에서 헛짚었나: {broken}")
    for slno, text, found in result.samples:
        print(f"    [{slno}] {list(found)}  {text}…")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="후속 질문 분류 평가")
    parser.add_argument("--goldset-dir", type=Path, default=DEFAULT_GOLDSET_DIR)
    parser.add_argument("--limit", type=int, default=0, help="B에서 볼 사례 수")
    args = parser.parse_args()

    report_written(score_written())
    try:
        report_real(score_real(args.goldset_dir, args.limit))
    except FileNotFoundError as exc:
        print(f"\n=== B. 건너뜀 — {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
