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
    # 통원 정액 공제(원)와 비율 공제. 약관은 "3만원과 보장대상 의료비의
    # 30% 중 큰 금액"처럼 둘 중 큰 쪽을 쓰라고 한다.
    outpatient_deductible: int | None
    outpatient_deductible_rate: float | None
    # 통원 1회(또는 1일)당 한도. 연간한도와 별개로 걸린다.
    outpatient_visit_limit: int | None = None
    # 급여 통원 공제의 정액은 의료기관 종류로 갈린다. 상급종합병원ㆍ종합병원ㆍ
    # 전문요양기관은 더 크다. 비급여 특약의 표는 한 줄뿐이라 이 값이 없다.
    outpatient_deductible_tertiary: int | None = None
    # 급여 통원 공제는 세 항 중 큰 금액이고, 세 번째 항이
    # `보장대상의료비 x 건강보험 본인부담률`이다. 그 비율은 진료비 영수증에서
    # 오는 값이라 **약관에 없다.** 그래서 이 값이 True인 규칙은 청구가
    # 본인부담률을 함께 들고 오지 않으면 공제를 끝까지 계산할 수 없다.
    outpatient_deductible_uses_copay_rate: bool = False
    # 이 값들을 읽은 조항. 사람이 열어 대조할 수 있어야 한다.
    source_articles: tuple[str, ...] = ()
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


# 급여 실손 — 제5조 ④ "본인부담금의 20%" -> 보상 80%,
#             제5조 ① "합산하여 5천만원 이내",
#             제3조 <표1> 통원 공제는 **세 항 중 큰 금액**이다.
#               "1만원(상급종합ㆍ종합ㆍ전문요양기관은 2만원),
#                보장대상 의료비의 20%,
#                보장대상의료비에 건강보험 본인부담률을 곱한 금액 중 큰 금액"
_BENEFIT_ARTICLES = (_uid(BENEFIT, "3"), _uid(BENEFIT, "5"))

# 중증 비급여 — 자기부담 30% -> 보상 70%,
#               제5조 ① "합산하여 5천만원 이내", ③ "통원 1회당 20만원 이내",
#               제3조 <표1> "3만원과 보장대상 의료비의 30% 중 큰 금액"
_SEVERE_ARTICLES = (_uid(SEVERE, "3"), _uid(SEVERE, "5"))

# 비중증 비급여 — 자기부담 50% -> 보상 50%,
#                 제5조 ① "합산하여 1천만원 이내", ③ "통원 1일당 20만원 이내",
#                 제3조 <표1> "5만원과 보장대상 의료비의 50% 중 큰 금액"
_MILD_ARTICLES = (_uid(MILD, "3"), _uid(MILD, "5"))

RULES: tuple[AmountRule, ...] = (
    AmountRule(
        product=BENEFIT,
        coverage="(1)상해급여",
        reimburse_rate=0.80,
        annual_limit=50_000_000,
        outpatient_deductible=10_000,
        outpatient_deductible_rate=0.20,
        outpatient_deductible_tertiary=20_000,
        outpatient_deductible_uses_copay_rate=True,
        source_articles=_BENEFIT_ARTICLES,
        note="통원 공제의 세 번째 항(건강보험 본인부담률)은 영수증에서 온다",
    ),
    AmountRule(
        product=BENEFIT,
        coverage="(2)질병급여",
        reimburse_rate=0.80,
        annual_limit=50_000_000,
        outpatient_deductible=10_000,
        outpatient_deductible_rate=0.20,
        outpatient_deductible_tertiary=20_000,
        outpatient_deductible_uses_copay_rate=True,
        source_articles=_BENEFIT_ARTICLES,
        note="상해급여와 같은 구조. 한도는 보장종목별로 따로 5천만원",
    ),
    AmountRule(
        product=SEVERE,
        coverage="(1)상해비급여",
        reimburse_rate=0.70,
        annual_limit=50_000_000,
        outpatient_deductible=30_000,
        outpatient_deductible_rate=0.30,
        outpatient_visit_limit=200_000,
        source_articles=_SEVERE_ARTICLES,
        note="통원 연간 100회 한도는 반영하지 않았다 — 누적 횟수를 저장해야 한다",
    ),
    AmountRule(
        product=SEVERE,
        coverage="(2)질병비급여",
        reimburse_rate=0.70,
        annual_limit=50_000_000,
        outpatient_deductible=30_000,
        outpatient_deductible_rate=0.30,
        outpatient_visit_limit=200_000,
        source_articles=_SEVERE_ARTICLES,
        note="상해비급여와 같은 구조",
    ),
    AmountRule(
        product=MILD,
        coverage="(1)상해비급여",
        reimburse_rate=0.50,
        annual_limit=10_000_000,
        outpatient_deductible=50_000,
        outpatient_deductible_rate=0.50,
        outpatient_visit_limit=200_000,
        source_articles=_MILD_ARTICLES,
        note="통원 연간 100일 한도는 반영하지 않았다 — 누적 일수를 저장해야 한다",
    ),
    AmountRule(
        product=MILD,
        coverage="(2)질병비급여",
        reimburse_rate=0.50,
        annual_limit=10_000_000,
        outpatient_deductible=50_000,
        outpatient_deductible_rate=0.50,
        outpatient_visit_limit=200_000,
        source_articles=_MILD_ARTICLES,
        note="상해비급여와 같은 구조",
    ),
    # 3대비급여·비급여 MRI는 제3조가 항목별로 따로 한도를 정한다. 하나의
    # 비율·한도로 요약할 수 없어 넣지 않는다 — 규칙이 없으면 계산기가
    # 계산하지 않고 가드레일이 사람에게 넘긴다.
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
