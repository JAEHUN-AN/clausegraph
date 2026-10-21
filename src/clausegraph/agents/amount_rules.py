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

import re
from dataclasses import dataclass

from ..graph.schema import article_uid
from .benefit_markers import clean, covered_word, noncovered_word, word_at


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
    # 급여 입원의 자기부담 연간 상한(제5조 ④).
    #
    #   "입원의 경우 … 본인부담금의 20%에 해당하는 금액이 … 연간 200만원을
    #    초과하는 경우 **그 초과금액은** 제1항의 한도내에서 **보상합니다**"
    #
    # 이 조항은 방향이 반대다 — 알수록 **더** 지급한다. 그래서 누적을 모르면
    # 우리 계산은 과소지급이 되고, 상한이 아니라 하한이 된다(notes/028).
    # 비급여 특약에는 이 조항이 없다.
    self_pay_annual_cap: int | None = None
    # 상급병실료 차액. 제3조 표의 **별도 행**이다 — 입원의료비와 따로 계산해
    # 더한다. 두 특약이 같은 문장을 쓴다.
    #
    #   "비급여 병실료의 50%. 다만, **1일 평균금액 10만원을 한도**로 하며,
    #    1일 평균금액은 입원기간 동안 비급여 병실료 전체를 총 입원일수로
    #    나누어 산출합니다."
    #
    # **어느 금액의 1일 평균인지 두 가지로 읽힌다.** 뒤 문장이 그것을
    # "비급여 병실료 전체 ÷ 총 입원일수"로 못박으므로 **병실료의 평균**으로
    # 읽었다. 지급액의 평균으로 읽으면 뒤 문장이 앞 문장과 어긋난다.
    # 두 읽기의 차이는 한도에서 두 배다 — 근거와 함께 notes/029에 적었다.
    room_charge_rate: float | None = None
    room_charge_daily_cap: int | None = None
    # 특약2 입원에만 있는 한도(제3조).
    #
    #   "다만, 「의료법」 제3조제2항에 의한 의료기관(동법 제3조의3에 의한
    #    종합병원은 제외)에서 발생한 비급여 의료비는 **1회당 300만원**을
    #    한도로 합니다."
    #
    # 의료기관 종류를 모르면 이 한도를 걸지 못하고, 안 거는 쪽이 과다지급이다.
    inpatient_clinic_cap: int | None = None
    # 한도가 **약관이 아니라 보험증권에** 있는 상품. 해외여행 기본형 제5조 ①:
    #
    #   "(1)상해의료비 해외, (2)질병의료비 해외의 경우 각각에 대하여
    #    **계약시 계약자가 선택한 금액**"
    #
    # 그래서 `annual_limit`은 비워 두고 청구가 가입금액을 들고 와야 한다.
    # 모르면 한도를 걸지 못하고, 그때 금액은 지급액이 아니라 상한이다.
    limit_from_policy: bool = False
    # 해외의료기관에서 치료받은 경우에만 쓰는 규칙. 같은 상품의 국내(급여)
    # 행은 공제·비율·한도가 전부 다르다 — 해외 규칙을 국내 치료에 쓰면
    # 공제를 하나도 안 빼고 100%를 주는 과다지급이다. 치료 장소를 모르면
    # 이 규칙을 고르지 않는다.
    abroad_only: bool = False
    # 이 값들을 읽은 조항. 사람이 열어 대조할 수 있어야 한다.
    source_articles: tuple[str, ...] = ()
    note: str = ""

    def rate_for(self, inpatient: bool) -> float | None:
        return self.inpatient_rate if inpatient else self.outpatient_rate

    def has_limit(self) -> bool:
        """한도를 어디서 정하는지 아는가. 값 자체는 증권에 있을 수 있다."""
        return self.annual_limit is not None or self.limit_from_policy

    def complete_for_inpatient(self) -> bool:
        if self.inpatient_rate is None or not self.has_limit():
            return False
        # 입원에도 공제가 붙는 규칙이면 공제 값까지 있어야 계산이 된다.
        if not self.deductible_applies_to_inpatient:
            return True
        return None not in (self.outpatient_deductible, self.outpatient_deductible_rate)

    def complete_for_outpatient(self) -> bool:
        return (
            self.outpatient_rate is not None
            and self.has_limit()
            and None not in (self.outpatient_deductible, self.outpatient_deductible_rate)
        )


BENEFIT = "기본형 실손의료보험(급여 실손의료비)"
SEVERE = "실손의료보험 특별약관1(중증 비급여 실손의료비)"
MILD = "실손의료보험 특별약관2(비중증 비급여 실손의료비)"
OVERSEAS = "기본형 해외여행 실손의료보험"

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

# 해외여행 기본형의 **해외** 행 — 제3조 표 (1)·(2) 해외 ①
#   "해외의료기관에서 의사 … 의 치료를 받은 때에는 보험가입금액을 한도로
#    피보험자가 실제 부담한 의료비 전액을 보상합니다."
#   -> 공제 없음, 비율 100%, 입원·통원 구분 없음.
# 제5조 ① 한도는 "계약시 계약자가 선택한 금액" -> 증권 값(limit_from_policy).
# 20250401·20250901·20260910 판본도 같은 문장이다(2026-10-20 원문 대조).
#
# 같은 상품의 국내(급여) 행(붙임 공제, 180일 90회)은 넣지 않았다. 넣으면
# `find_rule`이 상해/질병으로 고를 때 해외·국내 둘이 걸려 아무것도 못 고른다.
_OVERSEAS_ARTICLES = (_uid(OVERSEAS, "3"), _uid(OVERSEAS, "5"))

RULES: tuple[AmountRule, ...] = (
    AmountRule(
        product=BENEFIT,
        coverage="(1)상해급여",
        self_pay_annual_cap=2_000_000,  # 제5조 ④ 입원 자기부담 연 200만원
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
        self_pay_annual_cap=2_000_000,  # 제5조 ④ 입원 자기부담 연 200만원
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
        room_charge_rate=0.50,
        room_charge_daily_cap=100_000,
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
        room_charge_rate=0.50,
        room_charge_daily_cap=100_000,
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
        inpatient_clinic_cap=3_000_000,  # 제3조 의원급 입원 1회당 300만원
        room_charge_rate=0.50,
        room_charge_daily_cap=100_000,
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
        inpatient_clinic_cap=3_000_000,  # 제3조 의원급 입원 1회당 300만원
        room_charge_rate=0.50,
        room_charge_daily_cap=100_000,
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
    # `find_rule`은 이 규칙을 **자동으로 고르지 않는다.** 청구에 이 항목의
    # 표지가 있으면 (1)(2)도 고르지 않고 사람에게 넘긴다(`noncovered_items`,
    # notes/048).
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
    AmountRule(
        product=OVERSEAS,
        coverage="(1)상해의료비 해외",
        inpatient_rate=1.00,
        outpatient_rate=1.00,
        annual_limit=None,
        limit_from_policy=True,
        abroad_only=True,
        outpatient_deductible=0,
        outpatient_deductible_rate=0.0,
        source_articles=_OVERSEAS_ARTICLES,
        note="해외의료기관 치료만. 한도는 증권의 보험가입금액",
    ),
    AmountRule(
        product=OVERSEAS,
        coverage="(2)질병의료비 해외",
        inpatient_rate=1.00,
        outpatient_rate=1.00,
        annual_limit=None,
        limit_from_policy=True,
        abroad_only=True,
        outpatient_deductible=0,
        outpatient_deductible_rate=0.0,
        source_articles=_OVERSEAS_ARTICLES,
        note="상해의료비 해외와 같은 구조. 가입금액은 보장종목별로 따로다",
    ),
)

_BY_KEY = {(rule.product, rule.coverage): rule for rule in RULES}


# --- (1)(2)가 빼고 보상하는 의료행위 — 3대비급여ㆍ비급여 MRI ---
#
#   특약1 제3조 (1)(2) ① "비급여의료비(3대비급여는 제외합니다)"
#   특약2 제3조 (1)(2) ① "비급여의료비(비급여 자기공명영상진단은 제외합니다)"
#
# 그래서 청구에 이 의료행위가 있으면 (1)(2) 규칙은 **틀린 규칙**이다. 진단코드는
# 상해/질병만 가를 뿐 의료행위를 모른다 — 요통(M54.5) 도수치료 청구를
# (2)질병비급여(연 5천만원, 통원 1회 20만원)로 계산해, 항목 한도 350만원을 이미
# 다 쓴 계약에 7만원을 지급했다(notes/048).
#
# 항목 규칙도 자동으로 고르지 않는다(notes/048).
#   - 기지급액ㆍ횟수(`ClaimHistory`)는 보장종목 단위로 들어온다. 그 값이 항목
#     단위인지 알 수 없다 — 모르는 값을 항목 누적으로 읽으면 한도가 틀린다.
#   - 청구액이 한 덩어리다. 같은 통원에서 도수치료와 다른 비급여를 받았으면
#     항목 몫을 나눌 수 없다.
#   - 이학요법은 "최초 10회 보장하고, 이후 … 증상의 개선, 병변호전 등이 …
#     확인된 경우에 한하여 10회 단위로" 보상한다. 확인은 사람의 일이다.
MUSCULOSKELETAL = "근골격계 이학요법ㆍ체외충격파치료"
INJECTION = "주사료"
MRI = "자기공명영상진단"
# 급여 실손은 비급여를 아예 보상하지 않는다(제4조의2, 표지는 `benefit_markers`).
NONCOVERED = "비급여"
# 비급여 특약은 급여를 아예 보상하지 않는다(제3조, 표지는 `benefit_markers`).
COVERED = "급여"

# 특약1 제2조가 이학요법의 예로 든 것 — "FIMS(기능적근육내 자극치료), 신장분사치료,
# 도수치료, 증식치료, 비침습적 무통증 신호요법 등" — 과 현장 표기("도수 10회",
# "충격파", IMS, ESWT, 인대강화주사). 체외충격파쇄석술(결석)은 제2조의
# 체외충격파치료("건(힘줄) 및 뼈의 치유 과정을 자극")가 아니다. '빈도수'의 도수는
# 뺀다. "안경 도수" 같은 오탐은 사람에게 가는 쪽이라 둔다.
_MUSCULOSKELETAL_RE = re.compile(
    r"(?<!빈)도수|충격파(?!\s*쇄석)|증식\s*치료|프롤로|인대\s*강화|이학\s*요법"
    r"|(?<![A-Z])F?IMS(?![A-Z])|ESWT|근육\s*내\s*자극|신장\s*분사|무통증\s*신호",
    re.IGNORECASE,
)
_MRI_RE = re.compile(r"자기\s*공명|MR(?:I|A|CP)|엠\s*알\s*아이", re.IGNORECASE)
# 주사ㆍ수액(링거ㆍ링게르ㆍ닝겔).
#
# 항암제ㆍ항생제ㆍ희귀의약품 주사는 특약1 제3조 (3) ②가 (1)(2)로 돌려보내고 특약2
# 제4조도 면책에서 빼지만, **그 갈래를 서술의 낱말로 가르지 않는다.** 처음엔
# "항암주사"라고 적힌 자리를 표지에서 뺐는데, "싸이모신 항암주사"처럼 항암으로
# 불려도 약관의 항암제(종양용약)가 아닌 주사가 흔하다 — 주사료 한도를 다 쓴 계약에
# 20만원이 나갔다(반박 검증, notes/048). 약제가 무엇인지는 사람이 본다.
_INJECTION_RE = re.compile(r"주사|수액|[링닝][거겔게]")
_INJECTION_NOTE = "항암제ㆍ항생제ㆍ희귀의약품 주사면 (1)(2)에서 보상한다 — 약제 확인 필요"

# 급여 실손 제4조의2 — ② "… 다른 의료비가 함께 청구되어 각 항목별 의료비가 구분되지 않는 경우
# 회사는 … 각각의 의료비에 대한 확인을 요청할 수 있습니다." 표지는 `benefit_markers`(notes/049).
_NONCOVERED_NOTE = (
    "급여 의료비와 함께 청구됐으면 영수증의 급여ㆍ비급여 구분이 필요하다 — 제4조의2 ②"
)

# 비급여 특약 제3조 — 보상 대상은 비급여 의료비뿐이다. 표지는 `benefit_markers`(notes/050).
_COVERED_NOTE = (
    "특약은 비급여 의료비만 보상한다 — 급여 본인부담금은 기본형(급여 실손)에서 보상하며,"
    " 비급여와 함께 청구됐으면 영수증의 급여ㆍ비급여 구분이 필요하다"
)

# 상품마다 그 항목을 (1)(2) 대신 어디서 다루는가. 해외여행 기본형에는 이 구분이
# 없다. 급여 실손은 "비급여"라는 말만 본다 — 급여 MRI는 급여 의료비다.
_ITEM_RULES: dict[tuple[str, str], str] = {
    (SEVERE, MUSCULOSKELETAL): "(3)3대비급여-근골격계이학요법치료ㆍ체외충격파치료",
    (SEVERE, INJECTION): "(3)3대비급여-주사료",
    (SEVERE, MRI): "(3)3대비급여-자기공명영상진단",
    (MILD, MRI): "(3)비급여 자기공명영상진단",
}
# 보상하지 않는 항목과 그 조문. 급여 실손의 비급여는 제4조의2, 비급여 특약의 급여는 제3조.
#
# 특약2 제4조 — "근골격계 이학요법 치료행위에 수반된 일체의 의료비", "체외충격파
# 치료행위에 수반된 일체의 의료비", "항암제, 항생제, 희귀의약품을 제외한
# 주사치료시 사용된 행위, 약제 및 치료재료와 관련하여 소요된 비용".
# 면책 검증은 서술 낱말 겹침으로만 이것을 찾아 놓칠 수 있다 — 2026-10-21 실측에서
# 특약2 도수치료 청구가 면책 0건으로 5만원 지급됐다.
_ITEM_EXCLUDED: dict[tuple[str, str], str] = {
    (MILD, MUSCULOSKELETAL): "제4조",
    (MILD, INJECTION): "제4조",
    (BENEFIT, NONCOVERED): "제4조의2",
    (SEVERE, COVERED): "제3조",
    (MILD, COVERED): "제3조",
}
_ITEM_NOTES: dict[str, str] = {
    INJECTION: _INJECTION_NOTE,
    NONCOVERED: _NONCOVERED_NOTE,
    COVERED: _COVERED_NOTE,
}


_ITEM_PATTERNS = (
    (MUSCULOSKELETAL, _MUSCULOSKELETAL_RE),
    (INJECTION, _INJECTION_RE),
    (MRI, _MRI_RE),
)



def noncovered_items(treatment: str) -> dict[str, str]:
    """서술ㆍ시술명에 나온 3대비급여ㆍ비급여 MRI 항목과 '비급여'ㆍ'급여' 구분, 그렇게 읽은 낱말."""
    text = clean(treatment)
    found: dict[str, str] = {}
    for item, pattern in _ITEM_PATTERNS:
        match = pattern.search(text)
        if match:
            found[item] = word_at(text, *match.span())
    # '비급여'ㆍ'급여'는 의료행위가 아니라 건강보험 급여 구분이다(`benefit_markers`).
    for item, word in ((NONCOVERED, noncovered_word(text)), (COVERED, covered_word(text))):
        if word is not None:
            found[item] = word
    return found


def carved_out_items(product: str, treatment: str) -> dict[str, str]:
    """`noncovered_items` 중 이 상품의 (1)(2)가 빼는 것만.

    비급여 특약에서 '비급여'는 보장 대상 그 자체라 빼지 않고 '급여'를 뺀다. 급여 실손은
    거꾸로 '비급여'만 남는다. 해외여행 기본형은 늘 빈 값이다.
    """
    return {
        item: word
        for item, word in noncovered_items(treatment).items()
        if (product, item) in _ITEM_RULES or (product, item) in _ITEM_EXCLUDED
    }


def _carved_out(product: str, treatment: str) -> str | None:
    """청구에 (1)(2)가 빼는 항목이 있으면 그 설명. 없으면 None."""
    parts: list[str] = []
    for item, word in carved_out_items(product, treatment).items():
        coverage = _ITEM_RULES.get((product, item))
        if coverage is not None:
            parts.append(
                f"'{word}' — (1)(2)가 빼는 {item} 항목({coverage}{_limit_text(product, coverage)})"
                "이라 항목별 의료비ㆍ기지급액ㆍ횟수가 필요하다"
            )
        else:
            article = _ITEM_EXCLUDED[(product, item)]
            parts.append(f"'{word}' — {article}가 보상하지 않는 {item} 비용일 수 있다")
        if item in _ITEM_NOTES:
            parts[-1] += f"({_ITEM_NOTES[item]})"
    if not parts:
        return None
    return f"{'; '.join(parts)} — (1)(2) 산식으로 계산하지 않는다 — 심사자 확인이 필요하다"


def _limit_text(product: str, coverage: str) -> str:
    rule = _BY_KEY[(product, coverage)]
    limits = [f"연 {rule.annual_limit:,}원"] if rule.annual_limit is not None else []
    if rule.annual_visit_limit:
        limits.append(f"{rule.annual_visit_limit}회")
    return f", {'ㆍ'.join(limits)} 한도" if limits else ""


# KCD 챕터 S·T는 손상·중독, 즉 상해다. 나머지는 질병으로 본다.
# 보장종목이 상해급여/질병급여로 갈리므로 이 구분이 곧 파라미터 선택이다.
INJURY_CHAPTERS = frozenset("ST")


# 보장종목이 섞였는지 볼 때의 질병 장. R(증상)·V~Y(외인)·Z(보건서비스)·U는
# 뺀다 — 손목 골절 서술의 '통증'(R52)이나 개물림 외인(W54)이 질병으로 세어지면
# 멀쩡한 상해 청구가 '섞였다'가 된다.
DISEASE_CHAPTERS = frozenset("ABCDEFGHIJKLMNOPQ")


def coverage_kinds(diagnosis_codes: tuple[str, ...]) -> frozenset[str]:
    """청구 코드가 가리키는 보장종목들 — {'상해'}, {'질병'}, 둘 다, 또는 빈 집합."""
    letters = {code.strip().upper()[:1] for code in diagnosis_codes}
    kinds = set()
    if letters & INJURY_CHAPTERS:
        kinds.add("상해")
    if letters & DISEASE_CHAPTERS:
        kinds.add("질병")
    return frozenset(kinds)


def why_no_rule(
    product: str,
    diagnosis_codes: tuple[str, ...],
    *,
    abroad: bool | None,
    treatment: str = "",
) -> str | None:
    """규칙을 일부러 고르지 않는 경우의 이유. 해당 없으면 None.

    `find_rule`이 None을 돌려주는 이유가 "파라미터가 없다"뿐이면 심사자가
    엉뚱한 것을 찾는다. 이것들은 파라미터가 없어서가 아니라 **계산하면 안 돼서**다.

    `treatment`는 청구의 서술과 시술명이다. 3대비급여ㆍ비급여 MRI와 급여 실손의
    비급여, 비급여 특약의 급여는 진단코드가 아니라 의료행위ㆍ급여 구분으로 갈려 여기서만
    보인다(`noncovered_items`).
    """
    rules = [rule for rule in RULES if rule.product == product]
    if abroad is True and rules and not any(rule.abroad_only for rule in rules):
        return "해외의료기관 치료를 국내 상품 산식으로 계산하지 않는다 — 심사자 확인이 필요하다"
    if any(rule.limit_from_policy for rule in rules) and len(coverage_kinds(diagnosis_codes)) > 1:
        return (
            "상해와 질병 진단이 섞여 보장종목별 보험가입금액을 나눌 수 없다"
            " — 보장종목별 의료비 구분이 필요하다"
        )
    return _carved_out(product, treatment)


# 손상의 외부요인(V~Y)은 어떻게 다쳤는지를 말하지, 어디가 어떻게 다쳤는지는 말하지
# 않는다. 외인 코드만 있으면 상해인지 질병인지 정하지 않는다.
#
# 전에는 S·T가 없으면 무조건 질병이었다. '교통사고로 입원'(V87)이 질병급여 규칙을
# 골랐고, notes/047에서 장을 못 정한 tail의 S 코드를 약한 코드로 내리자 '낙상 후
# 경막하출혈로 수술'(I62·P10·S06 -> 확실 W19만)이 같은 길로 질병급여가 됐다(검토 HIGH-1).
# 상해와 질병은 연간한도가 따로라 금액이 달라진다.
EXTERNAL_CAUSE_CHAPTERS = frozenset("VWXY")


def classify_coverage(diagnosis_codes: tuple[str, ...]) -> str | None:
    """진단코드로 상해인지 질병인지 가른다. 코드가 없거나 외인 코드뿐이면 None.

    R(증상)·Z만 있는 청구는 전처럼 질병으로 본다 — '두통으로 통원'(R51)이 그 예다.
    """
    if not diagnosis_codes:
        return None
    letters = {code.strip().upper()[:1] for code in diagnosis_codes}
    if letters & INJURY_CHAPTERS:
        return "상해"
    if not letters & DISEASE_CHAPTERS and letters & EXTERNAL_CAUSE_CHAPTERS:
        return None
    return "질병"


def find_rule(
    product: str,
    coverage: str | None = None,
    diagnosis_codes: tuple[str, ...] = (),
    *,
    abroad: bool | None = None,
    treatment: str = "",
) -> AmountRule | None:
    """상품·보장종목의 파라미터. 없으면 None — 계산하지 않는다.

    `abroad`는 해외의료기관에서 치료받았는지다. **True일 때만** 해외 전용
    규칙을 고른다 — 모르는 것(None)을 해외로 치면 국내 치료에 공제 없는
    100%를 주게 된다. 거꾸로 **해외 치료에 국내 규칙도 고르지 않는다** — 급여
    실손은 국민건강보험 요양급여를, 비급여 특약은 국내 요양기관을 전제로 한다
    (검증 OP-7). 고르지 않는 이유는 `why_no_rule`이 말한다.

    `treatment`(서술ㆍ시술명)에 3대비급여ㆍ비급여 MRI나 '급여' 표지가 있으면 특약1ㆍ2에서,
    '비급여'라는 말이 있으면 급여 실손에서 아무 규칙도 고르지 않는다. `coverage`로
    지목해도 마찬가지다 — (1)(2)를 지목하면 불확실 면책이 (2)를 가리키는 길로 같은
    버그가 새고, 항목 규칙을 지목해도 들어온 기지급액ㆍ횟수가 항목 단위인지 알 수
    없다(notes/048ㆍ049ㆍ050).
    """
    if why_no_rule(product, diagnosis_codes, abroad=abroad, treatment=treatment) is not None:
        return None

    def usable(rule: AmountRule) -> bool:
        return rule.abroad_only if abroad is True else not rule.abroad_only

    if coverage:
        exact = _BY_KEY.get((product, coverage))
        if exact is not None and usable(exact):
            return exact

    candidates = [rule for rule in RULES if rule.product == product and usable(rule)]
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
