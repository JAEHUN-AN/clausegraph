"""Tier B 라벨러 — 분쟁이 **무엇을 두고** 갈렸는지 규칙으로 뽑는다.

## 왜 조항 번호가 아닌가

Tier B를 "근거 조항"으로 잡으려 했는데, 사례 160건에서 `제N조`를 인용하는
것이 **15건(9%)**뿐이었다. KCD 코드를 적는 것은 5건(3%)이다. 게다가 사례는
회사 상품 약관을 다루므로 조항 번호가 표준약관 번호와 맞지도 않는다.

대신 **쟁점 문장이 분쟁의 종류를 또렷하게 말한다.** 중앙 길이 55자이고,
지배적인 형태가 하나다.

    "…이 약관에서 정하고 있는 수술의 정의와 범위에 포함되는지 여부"
    "…가 실손보험의 보장 대상에 해당하는지 여부"
    "…가 보상하지 않는 사항으로 정한 '보조기 등'에 해당하는지 여부"

그래서 Tier B는 **분쟁 유형**을 라벨한다.

## 무엇에 쓰나

이 라벨의 쓸모는 정확도 자랑이 아니라 **범위를 정직하게 그리는 것**이다.
유형마다 이 시스템이 답할 수 있는지가 다르다.

- 면책 해당 여부는 답한다 — 코드 범위와 열거로 결정론적으로 걸린다.
- 시점 다툼도 답한다 — 가입일로 판본을 고정하는 것이 이 프로젝트의 축이다.
- **정의 해석은 못 답한다.** "IPL시술이 약관이 정한 수술인가"는 조문을 찾아
  주는 문제가 아니라 조문을 *해석하는* 문제다. 그래프도 벡터도 그걸 하지
  않는다.

실제 분쟁의 몇 %가 어느 쪽인지 세면, 이 시스템이 어디까지 쓸모 있는지가
숫자로 나온다. 그게 recall 100%보다 정직한 진술이다.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field

from .models import DisputeCase

ISSUE_SECTION = "쟁점"
QUOTE_CHARS = 60


class DisputeType(StrEnum):
    DEFINITION = "DEFINITION"    # 약관 용어(수술·입원·장해)의 범위 해석
    EXCLUSION = "EXCLUSION"      # 보상하지 않는 사항에 걸리는가
    DUPLICATE = "DUPLICATE"      # 다른 제도에서 받은 금액·환급금의 처리
    TIMING = "TIMING"            # 보장개시·면책기간·부담보·적용 판본
    DISCLOSURE = "DISCLOSURE"    # 계약 전 알릴 의무, 통지 의무, 설명 의무
    PROOF = "PROOF"              # 진단 확정·입증의 충분성
    SCOPE = "SCOPE"              # 그 담보·특약에 가입했는가
    PROCEDURE = "PROCEDURE"      # 청구권자·청구 절차
    CONTRACT = "CONTRACT"        # 계약 성립·취소·보험료·환급
    UNKNOWN = "UNKNOWN"


# 이 시스템이 유형별로 무엇을 할 수 있는가. 라벨과 함께 이 표를 내야
# "몇 건을 맞혔다"가 아니라 "무엇을 다룰 수 있다"를 말할 수 있다.
HANDLED: dict[DisputeType, bool] = {
    DisputeType.EXCLUSION: True,
    DisputeType.TIMING: True,
    DisputeType.DUPLICATE: True,   # 면책 조항이고, 그 예외 참조까지 따라간다
    # 상품을 고정하면 그 상품에 그 보장 조항이 있는지가 그래프에서 바로 나온다.
    # 특약 미가입이면 보장 조항이 없고, 시스템은 근거 없이 결론을 내지 않는다.
    DisputeType.SCOPE: True,
    DisputeType.DEFINITION: False,  # 조문을 찾는 문제가 아니라 해석하는 문제
    DisputeType.DISCLOSURE: False,
    DisputeType.PROOF: False,
    DisputeType.PROCEDURE: False,
    DisputeType.CONTRACT: False,
    DisputeType.UNKNOWN: False,
}


# --- 규칙 ---
#
# **순서가 곧 규칙이다.** 좁은 것을 먼저 본다. 쟁점 문장은 대개
# "X가 Y에 해당하는지 여부" 꼴이라 `해당하는지`만 보면 전부 한 통이 된다.
# 그래서 Y가 무엇인지 가리키는 낱말을 먼저 찾는다.

_RULES: tuple[tuple[DisputeType, re.Pattern[str]], ...] = (
    # 다른 제도에서 받은 돈. 실손의 중복보상 조항이 정면으로 다룬다.
    #
    # `환급`은 조심해야 한다 — **보험료** 환급은 계약 문제이지 중복보상이
    # 아니다("건강체 적용 후 정산받은 보험료 환급금액이 적정한지"). 그래서
    # 환급 계열은 의료비 문맥을 함께 요구한다. 손으로 검수해서 잡았다.
    (
        DisputeType.DUPLICATE,
        re.compile(
            r"본인부담상한|산재보험(?:금)?을?\s*지급\s*받|자동차보험에서\s*보상"
            r"|위험분담제"
            r"|(?=.*(?:의료비|약제비|치료비|보험금))(?:.*환급\s*받는|.*환급(?:금|액))"
        ),
    ),
    # 담보·특약 가입 여부. "미가입 시 보상받을 수 있는지"는 면책이 아니라
    # 애초에 그 보장이 계약에 없다는 문제다. 면책과 섞으면 안 된다.
    (
        DisputeType.SCOPE,
        re.compile(r"미가입|가입하지\s*않은\s*경우|특약(?:을)?\s*가입하지"),
    ),
    # 보상하지 않는 사항. 약관이 면책이라고 부르는 자리를 명시적으로 가리킨다.
    (
        DisputeType.EXCLUSION,
        re.compile(r"보상하지\s*않는|보상하지\s*아니하는|면책\s*(?:사항|조항)|부담보"),
    ),
    # 시점. 보장개시·면책기간·기간 내 발생·판본 기준.
    (
        DisputeType.TIMING,
        re.compile(
            r"보장개시|면책\s*기간|보장하는\s*기간\s*내|기간\s*내에\s*발생"
            r"|퇴직(?:한)?\s*이후|가입\s*시점|진단\s*시점|체결\s*시점|보험나이"
            r"|시점에\s*따라|이후에\s*가입|KCD\s*개정"
        ),
    ),
    # 고지·통지·설명 의무.
    (
        DisputeType.DISCLOSURE,
        re.compile(
            r"알릴\s*의무|통지할?\s*의무|고지(?:하|의)|설명\s*의무|서명\s*미비"
        ),
    ),
    # 입증과 진단 확정.
    (
        DisputeType.PROOF,
        re.compile(
            r"입증하?(?:였|여|는)|검사\s*결과가?\s*충분|진단(?:을)?\s*받은\s*것으로\s*볼"
            r"|확인이?\s*되지\s*않"
        ),
    ),
    # 청구 절차와 청구권자.
    (
        DisputeType.PROCEDURE,
        re.compile(r"대신하여\s*보험금을\s*청구|청구할\s*수\s*있는지|의사능력"),
    ),
    # 계약 자체. 성립·취소·보험료·만기.
    (
        DisputeType.CONTRACT,
        re.compile(r"취소\s*사유|보험료\s*환급|환급금액이\s*적정|만기보험금|판매행위"),
    ),
    # 면책 주제어 + 실손 문맥. 쟁점이 "X가 보험금 지급대상에 해당하는지"처럼
    # 일반형 술어로 끝나도, **X가 실손 약관 면책 목록에 이름 그대로 올라
    # 있으면** 그건 정의 해석이 아니라 면책 해당 여부다.
    #
    # 아래 낱말은 지어낸 것이 아니라 표준약관 제4조(보상하지 않는 사항)에
    # 실제로 적힌 항목에서 가져왔다.
    #
    # **실손 문맥을 함께 요구한다.** 낱말만 보면 두 가지가 새 들어온다.
    #   "치과치료보험금 지급 사유에 해당하는지"  -> 치과 담보의 정의 문제
    #   "건강검진 결과 의심소견도 알릴의무 대상"  -> 고지 의무 문제
    # 둘 다 면책이 아니다. 손으로 검수해서 잡았다.
    #
    # 순서도 마지막에 둔다 — 고지·입증·시점 규칙이 먼저 걸러 가게.
    (
        DisputeType.EXCLUSION,
        re.compile(
            r"(?=.*(?:실손|실비))"
            r".*(?:보조기|압박고정용|치과\s*치료|치과치료|한방\s*치료|한방치료"
            r"|한방병원|성장호르몬|호르몬\s*투여|영양제|비타민제|건강검진"
            r"|백신\s*접종|예방\s*접종|증명서\s*발급|증명료|간병(?:비|인))"
        ),
    ),
    # 약관 용어의 범위. 가장 넓으므로 마지막에 본다.
    (
        DisputeType.DEFINITION,
        re.compile(
            r"정의와\s*범위|정한\s*[“'\"]?수술|수술[”'\"]?에\s*해당"
            r"|지급\s*사유에\s*해당|지급사유에\s*해당|지급\s*대상에\s*해당"
            r"|지급대상에\s*해당|보장\s*대상(?:인지|에\s*해당)|보상\s*대상에\s*해당"
            r"|보장하는\s*치료|목적에\s*부합|장해(?:지급률|로\s*판단)"
            r"|포함되는지|해당하는지|해당되는지|해당될\s*수"
            # 가장 넓은 형태. 위 규칙이 전부 빗나갔을 때만 여기까지 온다.
            r"|지급\s*의무가?\s*있는지|지급\s*여부|지급\s*가능\s*여부"
        ),
    ),
)


class TypedCase(BaseModel):
    """분쟁 유형 라벨 한 건."""

    model_config = {"frozen": True}

    case_slno: int
    cvpl: str
    dispute_type: DisputeType
    handled: bool = Field(description="이 시스템이 답할 수 있는 유형인가")
    evidence: str = Field(default="", description="쟁점에서 규칙이 걸린 구절")


def classify_issue(issue: str) -> tuple[DisputeType, str]:
    """쟁점 문장에서 분쟁 유형과 근거 구절을 뽑는다.

    걸리는 규칙이 없으면 `UNKNOWN`이다. 억지로 배정하면 범위 측정이
    바로 거짓이 된다 — 이 라벨을 만드는 목적이 범위를 정직하게 그리는
    것이므로, 모르는 것은 모른다고 세어야 한다.
    """
    flat = " ".join(issue.split())
    if not flat:
        return DisputeType.UNKNOWN, ""

    for dispute_type, pattern in _RULES:
        match = pattern.search(flat)
        if match is not None:
            return dispute_type, _quote(flat, match.start())
    return DisputeType.UNKNOWN, ""


def type_case(case: DisputeCase) -> TypedCase:
    issue = case.sections.get(ISSUE_SECTION, "")
    dispute_type, evidence = classify_issue(issue)
    return TypedCase(
        case_slno=case.case_slno,
        cvpl=case.ref.cvpl,
        dispute_type=dispute_type,
        handled=HANDLED[dispute_type],
        evidence=evidence,
    )


def _quote(text: str, at: int) -> str:
    start = max(0, at - QUOTE_CHARS // 3)
    return text[start : start + QUOTE_CHARS].strip()
