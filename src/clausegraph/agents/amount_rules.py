"""지급액 계산에 쓰는 약관 파라미터.

## 왜 자동 파싱하지 않는가

산식 자체는 약관에 분명히 적혀 있다.

> 본인이 실제로 부담한 금액(통원의 경우 … `<표1>` 통원항목별 공제금액을 뺀
> 금액)의 40%를 제5조(보험가입금액 한도 등)에서 정한 연간 보험가입금액의
> 한도 내에서 보상합니다

그런데 그 값들이 **표 안의 표에 중첩**되고, 보장종목·입원/통원·의료기관
종류마다 다르고, 다른 조항(제5조 한도, `<표1>` 공제금액)을 참조하며,
캡("연간 200만원을 초과하는 경우")이 겹친다.

이걸 정규식으로 긁으면 조용히 틀린 숫자가 나오고, **틀린 숫자는 곧 틀린
지급액**이다. 금액산정에 LLM을 쓰지 않기로 한 것과 같은 이유로 자동
파싱도 하지 않는다(notes/009).

## 대신 이렇게 한다

값을 **손으로 적되 조항을 함께 적는다.** 값이 몇 개뿐이고 약관 버전당
한 번만 바뀌므로, 사람이 근거 조항을 열어 대조할 수 있는 형태가 자동
파싱보다 안전하다.

**확인하지 못한 값은 비워 둔다.** `None`이면 계산기가 계산했다고 말하지
않고, 가드레일이 `HUMAN_REVIEW`로 넘긴다(notes/011). 추측한 값으로
지급액을 내는 것이 최악이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..graph.schema import article_uid


@dataclass(frozen=True)
class AmountRule:
    """한 상품·한 보장종목의 지급액 파라미터.

    비어 있는 값(None)은 "확인하지 못했다"는 뜻이며 추측하지 않는다.
    """

    product: str
    coverage: str
    # 보상 비율. 자기부담 20%면 0.8.
    reimburse_rate: float | None
    # 연간 보험가입금액 한도(원).
    annual_limit: int | None
    # 통원 정액 공제(원)와 비율 공제. 약관은 "1만원 또는 의료비의 20% 중
    # 큰 금액"처럼 둘 중 큰 쪽을 쓰라고 한다.
    outpatient_deductible: int | None
    outpatient_deductible_rate: float | None
    # 이 값들을 읽은 조항. 사람이 열어 대조할 수 있어야 한다.
    source_articles: tuple[str, ...]
    note: str = ""

    def complete_for_inpatient(self) -> bool:
        return self.reimburse_rate is not None and self.annual_limit is not None

    def complete_for_outpatient(self) -> bool:
        return self.complete_for_inpatient() and None not in (
            self.outpatient_deductible,
            self.outpatient_deductible_rate,
        )


BENEFIT = "기본형 실손의료보험(급여 실손의료비)"
SEVERE = "실손의료보험 특별약관1(중증 비급여 실손의료비)"
MILD = "실손의료보험 특별약관2(비중증 비급여 실손의료비)"

# 2026-05-06 이후 판본에서 읽었다. 판본이 바뀌면 여기도 함께 봐야 한다.
VERIFIED_VERSION = "20260506"


def _uid(product: str, number: str) -> str:
    return article_uid(VERIFIED_VERSION, product, number)


RULES: tuple[AmountRule, ...] = (
    AmountRule(
        product=BENEFIT,
        coverage="(1)상해급여",
        # 제5조 ④ "보상금액을 제외한 나머지 금액(… 본인부담금의 20%)" -> 보상 80%
        reimburse_rate=0.80,
        # 제5조 ① "입원과 통원의 보상금액을 합산하여 5천만원 이내"
        annual_limit=50_000_000,
        # <표1> "1만원 …, 보장대상의료비의 20% … 중 큰 금액"
        outpatient_deductible=10_000,
        outpatient_deductible_rate=0.20,
        source_articles=(_uid(BENEFIT, "3"), _uid(BENEFIT, "5")),
        note="통원 공제는 의료기관 종류에 따라 더 커진다 — 최소 유형만 반영했다",
    ),
    AmountRule(
        product=BENEFIT,
        coverage="(2)질병급여",
        reimburse_rate=0.80,
        annual_limit=50_000_000,
        outpatient_deductible=10_000,
        outpatient_deductible_rate=0.20,
        source_articles=(_uid(BENEFIT, "3"), _uid(BENEFIT, "5")),
        note="상해급여와 같은 구조. 한도는 보장종목별로 따로 5천만원",
    ),
    AmountRule(
        product=SEVERE,
        coverage="(1)상해비급여",
        # 중증 비급여는 자기부담 30% -> 보상 70%
        reimburse_rate=0.70,
        # 제5조를 읽었으나 보장종목별 한도를 이 판본에서 확정하지 못했다.
        annual_limit=None,
        outpatient_deductible=None,
        outpatient_deductible_rate=None,
        source_articles=(_uid(SEVERE, "3"),),
        note="한도·공제 미확인 — 계산하지 않고 사람에게 넘긴다",
    ),
    AmountRule(
        product=MILD,
        coverage="(1)상해비급여",
        # 비중증 비급여는 자기부담 50% -> 보상 50%
        reimburse_rate=0.50,
        annual_limit=None,
        outpatient_deductible=None,
        outpatient_deductible_rate=None,
        source_articles=(_uid(MILD, "3"),),
        note="한도·공제 미확인 — 계산하지 않고 사람에게 넘긴다",
    ),
)

_BY_KEY = {(rule.product, rule.coverage): rule for rule in RULES}


# KCD 챕터 S·T는 손상·중독, 즉 상해다. 나머지는 질병으로 본다.
# 보장종목이 상해급여/질병급여로 갈리므로 이 구분이 곧 파라미터 선택이다.
INJURY_CHAPTERS = frozenset("ST")


def classify_coverage(diagnosis_codes: tuple[str, ...]) -> str | None:
    """진단코드로 상해인지 질병인지 가른다. 코드가 없으면 None."""
    if not diagnosis_codes:
        return None
    if any(code.strip().upper()[:1] in INJURY_CHAPTERS for code in diagnosis_codes):
        return "상해"
    return "질병"


def find_rule(
    product: str,
    coverage: str | None = None,
    diagnosis_codes: tuple[str, ...] = (),
) -> AmountRule | None:
    """상품·보장종목의 파라미터. 없으면 None — 계산하지 않는다."""
    if coverage:
        exact = _BY_KEY.get((product, coverage))
        if exact is not None:
            return exact

    candidates = [rule for rule in RULES if rule.product == product]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # 보장종목이 여럿이면 진단코드로 상해/질병을 갈라 고른다.
    kind = classify_coverage(diagnosis_codes)
    if kind is None:
        return None
    matched = [rule for rule in candidates if kind in rule.coverage]
    return matched[0] if len(matched) == 1 else None
