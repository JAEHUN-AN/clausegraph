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
    # 보상 비율이 **입원과 통원에서 다르다.** 제3조 표의 입원 행은 비율을
    # 명시하지만("...의 70%에 해당하는 금액"), 통원 행은 공제금액을 뺀 금액
    # 자체를 보상금액으로 정하고 비율을 걸지 않는다. 그래서 통원은 1.0이다.
    #
    # 비율 하나로 뭉치면 통원을 과소지급한다(notes/019).
    inpatient_rate: float | None
    outpatient_rate: float | None
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
    # 3대비급여ㆍ비급여 MRI는 공제가 "1회당"이라 **입원에도 붙는다.**
    # (1)(2)의 공제는 통원에만 붙는다 — 그쪽이 기본값이다.
    deductible_applies_to_inpatient: bool = False
    # 감액기간 — 보장개시 초기의 지급사유에 비율을 곱하는 규정.
    #
    # **표준약관에는 없다.** 급여ㆍ특약 조문 전체에서 '감액'이 나오는 곳은
    # 알릴의무 위반, 보험가입금액 감액 요청, 지급절차 안내뿐이고, 어디에도
    # 보장개시 후 N일 동안 얼마만 지급한다는 규정이 없다(notes/019).
    #
    # 그래서 표준약관 규칙은 전부 None이다. 개별 회사 상품 약관이 정한
    # 경우에만 값이 들어간다. 근거 없는 감액은 과소지급이다.
    reduction_period_days: int | None = None
    reduction_rate: float | None = None
    # 연간 통원 횟수(특약2는 일수) 한도. 계약의 누적 횟수를 알아야 판정할 수
    # 있으므로 `ClaimHistory` 없이는 지급액을 단정하지 못한다.
    annual_visit_limit: int | None = None
    # 이 값들을 읽은 조항. 사람이 열어 대조할 수 있어야 한다.
    source_articles: tuple[str, ...] = ()
    note: str = ""

    def rate_for(self, inpatient: bool) -> float | None:
        return self.inpatient_rate if inpatient else self.outpatient_rate

    def complete_for_inpatient(self) -> bool:
        if self.inpatient_rate is None or self.annual_limit is None:
            return False
        # 입원에도 공제가 붙는 규칙이면 공제 값까지 있어야 계산이 된다.
        if not self.deductible_applies_to_inpatient:
            return True
        return None not in (self.outpatient_deductible, self.outpatient_deductible_rate)

    def complete_for_outpatient(self) -> bool:
        return self.outpatient_rate is not None and None not in (
            self.annual_limit,
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
#             제5조 ⑤ "통원 1회당 20만원 이내" — 특약에만 있는 줄 알고
#               빠뜨렸다가 실측으로 잡았다. 100만원 통원 청구에서 50만원
#               과다지급이었다(notes/027).
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
        inpatient_rate=0.80,
        outpatient_rate=1.00,
        annual_limit=50_000_000,
        outpatient_visit_limit=200_000,
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
        inpatient_rate=0.80,
        outpatient_rate=1.00,
        annual_limit=50_000_000,
        outpatient_visit_limit=200_000,
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
        annual_visit_limit=100,  # 제5조 ③ 연간 통원 100회
        inpatient_rate=0.70,
        outpatient_rate=1.00,
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
        annual_visit_limit=100,  # 제5조 ③ 연간 통원 100회
        inpatient_rate=0.70,
        outpatient_rate=1.00,
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
        annual_visit_limit=100,  # 제5조 ③ 연간 통원 100일
        inpatient_rate=0.50,
        outpatient_rate=1.00,
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
        annual_visit_limit=100,  # 제5조 ③ 연간 통원 100일
        inpatient_rate=0.50,
        outpatient_rate=1.00,
        annual_limit=10_000_000,
        outpatient_deductible=50_000,
        outpatient_deductible_rate=0.50,
        outpatient_visit_limit=200_000,
        source_articles=_MILD_ARTICLES,
        note="상해비급여와 같은 구조",
    ),
    # 3대비급여와 비급여 MRI는 제3조 <표1>이 **항목마다** 공제와 한도를
    # 따로 정한다. 그래서 보장종목 하나로 묶지 않고 항목별 규칙으로 넣는다.
    #
    # 이 항목들은 (1)(2)와 두 가지가 다르다.
    #   - 공제가 "1회당"이라 입원에도 붙는다.
    #   - 조문이 비율을 걸지 않는다("공제금액을 뺀 금액을 … 보상합니다").
    AmountRule(
        product=SEVERE,
        coverage="(3)3대비급여-근골격계이학요법치료ㆍ체외충격파치료",
        annual_visit_limit=50,
        inpatient_rate=1.00,
        outpatient_rate=1.00,
        annual_limit=3_500_000,
        outpatient_deductible=30_000,
        outpatient_deductible_rate=0.30,
        deductible_applies_to_inpatient=True,
        source_articles=_SEVERE_ARTICLES,
        note="연간 50회 한도(최초 10회, 이후 10회 단위)는 누적 횟수를 저장해야 한다",
    ),
    AmountRule(
        product=SEVERE,
        coverage="(3)3대비급여-주사료",
        annual_visit_limit=50,
        inpatient_rate=1.00,
        outpatient_rate=1.00,
        annual_limit=2_500_000,
        outpatient_deductible=30_000,
        outpatient_deductible_rate=0.30,
        deductible_applies_to_inpatient=True,
        source_articles=_SEVERE_ARTICLES,
        note="연간 50회 한도는 누적 횟수를 저장해야 한다",
    ),
    AmountRule(
        product=SEVERE,
        coverage="(3)3대비급여-자기공명영상진단",
        inpatient_rate=1.00,
        outpatient_rate=1.00,
        annual_limit=3_000_000,
        outpatient_deductible=30_000,
        outpatient_deductible_rate=0.30,
        deductible_applies_to_inpatient=True,
        source_articles=_SEVERE_ARTICLES,
        note="횟수 한도는 없다. 부위별로 각 1회로 본다",
    ),
    AmountRule(
        product=MILD,
        coverage="(3)비급여 자기공명영상진단",
        inpatient_rate=1.00,
        outpatient_rate=1.00,
        annual_limit=2_000_000,
        outpatient_deductible=50_000,
        outpatient_deductible_rate=0.50,
        deductible_applies_to_inpatient=True,
        source_articles=_MILD_ARTICLES,
        note="특약1의 3대비급여와 공제ㆍ한도가 다르다 — 5만원/50%, 200만원",
    ),
    # 상급병실료 차액(비급여 병실료의 50%, 1일 평균 10만원 한도)과 특약2
    # 입원의 의원급 1회당 300만원 한도는 넣지 않았다. 입원일수별 평균이나
    # 1회 단위 상태가 필요한데 지금 구조에 그 값이 없다.
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
