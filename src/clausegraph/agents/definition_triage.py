"""용어 해석 쟁점을 **판정하지 않고 분류해서 넘긴다.**

notes/034에서 DEFINITION 21건을 읽고 LLM 해석 에이전트를 붙이지 않기로
했다. 근거가 되는 문서가 없기 때문이다 — 상품 약관의 정의, 수술분류표,
판례, 의무기록. 없는 문서는 모델 급을 올려도 읽을 수 없다.

그렇다고 "못 푼다"에서 끝내면 심사자에게 준 것이 없다. 금액에서 쓴 방법을
그대로 쓴다.

> 무엇이 없어서 못 정했는지 **이름까지** 돌려준다 — 심사자가 할 일이
> "판단"이 아니라 "값 두 개 확인"이 된다. (notes/018)

## 무엇을 말하고 무엇을 말하지 않는가

**말한다**

- 이 쟁점의 용어가 표준약관에 정의돼 있는가 — `TermIndex` 조회, 결정론
- 약관이 가리키는데 수집본에 없는 문서가 있는가 — `부표 3`, `붙임1`
- 쟁점이 **사실 확인**을 요구하는 표현을 담고 있는가

**말하지 않는다**

- 해당/미해당. 이건 판정이고, 근거 문서 없이 내면 지어내는 것이다.
- **판례가 필요한지.** 쟁점 문장만 보고는 알 수 없다. 판례가 갈랐는지는
  결론을 봐야 아는 것이라, 예측하는 척하면 사후 지식을 쓰는 셈이 된다
  (notes/035).

세 번째가 이 모듈에서 가장 조심한 자리다. 네 범주 중 셋만 답하고 하나는
비워 두는 쪽이, 넷을 다 답하고 하나를 지어내는 쪽보다 낫다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .definition_terms import TermIndex

# 쟁점에서 따옴표로 묶인 용어를 먼저 본다. 금감원 쟁점 문장은 다투는 말을
# 따옴표로 묶는 습관이 있다 — `보험약관에서 정한 "수술"에 해당되는지`.
_QUOTED_RE = re.compile(r"[“\"'‘]([^”\"'’]{2,20})[”\"'’]")
# 따옴표가 없으면 "약관에서 정한 X" 꼴을 본다.
_PHRASE_RE = re.compile(
    r"약관(?:에서|이|상|에)?\s*(?:정한|정하는|정하고 있는|말하는)?\s*([가-힣]{2,12})"
)
# 그래도 없으면 알려진 다툼 용어를 찾는다.
_KNOWN_TERMS = (
    "수술",
    "입원비",
    "입원",
    "통원",
    "장해",
    "진단",
    "치료",
    "보철치료",
    "시력교정술",
    "특정전염병",
    "농업작업",
)

# 사실을 확인해야 갈리는 쟁점의 표지. 문서를 더 읽어서 되는 일이 아니다.
_FACT_WORDS = (
    "직접적인 목적",
    "직접 목적",
    "필요성",
    "입증",
    "확인",
    "의학적",
    "소견",
    "목적에 부합",
    "병행",
)
# 상품 약관이 표로 열거해 두는 것들. 그 표가 있어야 해당 여부가 갈린다.
_TABLE_WORDS = (
    "분류표",
    "특정전염병",
    "급성심근경색",
    "수술분류",
    "열거",
    "지급대상",
    "보장대상",
)


class Need(StrEnum):
    TERMS = "상품약관정의"
    TABLE = "상품분류표"
    PRECEDENT = "판례"
    RECORDS = "의무기록"


@dataclass(frozen=True)
class Triage:
    """이 쟁점을 판단하려면 무엇이 필요한가."""

    term: str
    needs: tuple[Need, ...]
    defined_in_standard_terms: bool
    # 정의는 있는데 그 정의가 수집본에 없는 표를 가리킨다.
    hollow: bool = False
    dangling: tuple[str, ...] = ()
    reason: str = ""

    def render(self) -> str:
        lines = [
            "이 쟁점은 용어 해석(DEFINITION)이다. 표준약관만으로는 판단하지 않는다.",
            f"다투는 용어: {self.term or '가리지 못함'}",
        ]
        if self.defined_in_standard_terms and self.hollow:
            lines.append(
                "  표준약관에 정의 조문은 있지만 **그 정의가 또 다른 문서를"
                " 가리키고, 그 문서가 수집본에 없다.** 조문만 봐서는 못 가른다."
            )
        elif self.defined_in_standard_terms:
            lines.append("  표준약관에 이 용어의 정의가 있다 — 그 조문부터 볼 것.")
        else:
            lines.append("  **표준약관에 이 용어의 정의가 없다.** 상품 약관을 봐야 한다.")
        if self.dangling:
            lines.append(
                f"  약관이 가리키는데 수집본에 없는 문서: {', '.join(self.dangling)}"
            )
        lines.append("필요한 것:")
        for need in self.needs:
            lines.append(f"  - {need}")
        lines.append(
            "판례가 갈랐는지는 이 도구가 말하지 않는다 — 쟁점 문장만으로는 알 수 없다."
        )
        return "\n".join(lines)


def extract_term(issue: str) -> str:
    """쟁점에서 다투는 용어를 뽑는다. 못 뽑으면 빈 문자열 — 지어내지 않는다."""
    quoted = _QUOTED_RE.search(issue)
    if quoted:
        return quoted.group(1).strip()

    for term in _KNOWN_TERMS:
        if term in issue:
            return term

    phrase = _PHRASE_RE.search(issue)
    return phrase.group(1).strip() if phrase else ""


def triage(issue: str, index: TermIndex) -> Triage:
    """쟁점 하나를 분류한다. 판정은 하지 않는다."""
    term = extract_term(issue)
    known = index.knows(term) if term else False
    hollow = index.is_hollow(term) if term else False

    needs: list[Need] = []
    # 정의가 수집본에 없으면 상품 약관이 있어야 한다. 이건 조회 결과이지
    # 추측이 아니다.
    if not known:
        needs.append(Need.TERMS)
    elif hollow:
        # 정의는 있는데 기준표가 없다. 조문을 보라고 하면 빈 곳으로 보낸다.
        needs.append(Need.TABLE)
    if any(word in issue for word in _TABLE_WORDS):
        needs.append(Need.TABLE)
    if any(word in issue for word in _FACT_WORDS):
        needs.append(Need.RECORDS)

    dangling = tuple(sorted(index.deferred_to))
    if not term:
        reason = "쟁점에서 다투는 용어를 가리지 못했다"
    elif not known:
        reason = f"'{term}'의 정의가 수집한 표준약관 {index.article_count}개 정의 조문에 없다"
    elif hollow:
        reason = f"'{term}'의 정의는 있으나 그 정의가 가리키는 표가 수집본에 없다"
    else:
        reason = f"'{term}'는 표준약관이 정의한다"

    return Triage(
        term=term,
        needs=tuple(dict.fromkeys(needs)),
        defined_in_standard_terms=known,
        hollow=hollow,
        dangling=dangling,
        reason=reason,
    )
