r"""조문 간 참조를 뽑는다.

## 왜 필요한가

면책 조항의 절반 이상이 **예외를 달고 있고, 그 예외가 다른 조문을 가리킨다.**

> 산재보험에서 보상받는 의료비. **다만**, 본인부담의료비(…)는
> **제3조(보장종목별 보상내용) (2)질병급여 제1항 및 제3항부터 제8항에 따라
> 보상합니다.**

이 문장은 면책이면서 동시에 보상의 근거를 가리킨다. 참조를 따라가지 않으면
"면책에 걸렸다"까지만 말하고 끝난다 — 청구인에게는 그 뒤가 중요하다.

벡터 검색으로는 이 연결을 만들 수 없다. `제3조`라는 표기는 어떤 청구 문장과도
닮지 않았고, 가리키는 대상은 **같은 판본·같은 상품의** 제3조여야 한다.
판본이 넷이고 상품이 열여섯이면 같은 `제3조`가 예순 개가 넘는다.

## 약관 안과 밖을 가른다

`제N조` 표기의 42%는 **약관 밖**을 가리킨다.

    「국민건강보험법」 제42조      법령 이름이 앞에 붙는다
    ?금융소비자 보호에 관한 법률?제47조   원문에서 괄호가 ?로 깨진 표기
    동법 제3조의3                  앞서 든 법을 다시 가리킨다
    민법 제768조                   법명이 짧아 「」 없이 쓴다

이걸 약관 내부 참조로 잘못 읽으면 엉뚱한 조문에 엣지가 생긴다. 그래서
**앞을 보고 판정하고, 판정하지 못하면 엣지를 만들지 않는다.**

두 가지가 판정을 어렵게 한다.

1. **나열** — `「국민건강보험법」제5조, 제53조, 제54조`에서 법령 이름은 한 번만
   나온다. 앞의 참조가 외부였고 그 사이가 이음매뿐이면 이어받는다.
2. **표 셀** — 조문이 표 안에 있으면 법령 이름과 참조 사이에 `┃ ┃` 테두리가
   끼어든다. 그래서 앞을 볼 때 괘선을 먼저 지운다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_REF_RE = re.compile(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?")

# 참조 바로 앞이 법령 이름이면 약관 밖이다.
_EXTERNAL_BEFORE_RE = re.compile(
    r"(?:[」』?]|동\s*법|같은\s*법|고시|훈령|예규"
    r"|[가-힣]{1,20}(?:법|법률|규칙|령|협약|조약|고시))\s*$"
)

# 나열의 이음매. 항·호 표기와 접속어가 섞인다.
#   「…법」제11조제1항 또는 제13조제1항
#   「…법」제5조, 제53조, 제54조
_LIST_JOIN_RE = re.compile(r"(?:제\s*\d+\s*[항호목]|또는|및|부터|까지|[,·、\s])+")

# 표 테두리·괘선. 앞을 볼 때 지운다.
_BOX_RE = re.compile(r"[─-╿▀-▟]+")

# 앞을 얼마나 볼지. 법령 이름 하나가 들어갈 만큼.
_LOOKBACK = 40


@dataclass(frozen=True)
class Reference:
    """조문 하나가 가리키는 다른 조문."""

    # 가리키는 조문 번호. `3`, `29의2` 형태로 Article.number와 같은 표기다.
    number: str
    # 원문에서 참조가 놓인 자리. 예외 단서 안인지 판단할 때 쓴다.
    start: int
    # 이 참조가 '다만' 뒤에 있는가 — 예외를 가리키는 참조인가.
    in_proviso: bool


def find_references(text: str) -> tuple[Reference, ...]:
    """약관 내부 참조만 돌려준다. 외부 법령 참조는 버린다."""
    found: list[Reference] = []
    seen: set[str] = set()
    external_end: int | None = None

    for match in _REF_RE.finditer(text):
        if _is_external(text, match, external_end):
            external_end = match.end()
            continue
        external_end = None

        number = match.group(1) + (f"의{match.group(2)}" if match.group(2) else "")
        if number in seen:
            continue
        seen.add(number)
        found.append(
            Reference(
                number=number,
                start=match.start(),
                in_proviso=_in_proviso(text, match.start()),
            )
        )
    return tuple(found)


def _is_external(text: str, match: re.Match[str], external_end: int | None) -> bool:
    before = _BOX_RE.sub(" ", text[max(0, match.start() - _LOOKBACK) : match.start()])
    if _EXTERNAL_BEFORE_RE.search(before.rstrip()):
        return True
    # 앞의 참조가 외부였고 그 사이가 이음매뿐이면 같은 나열이다.
    if external_end is None:
        return False
    gap = _BOX_RE.sub(" ", text[external_end : match.start()])
    return bool(gap) and _LIST_JOIN_RE.fullmatch(gap) is not None


# 예외 단서를 여는 말. 이 뒤의 참조는 "면책이지만 이건 보상한다"를 가리킨다.
_PROVISO_RE = re.compile(r"다만|단,|except", re.IGNORECASE)


def _in_proviso(text: str, position: int) -> bool:
    """참조가 '다만' 뒤에 있는가.

    문장 단위로 보지 않고 그 앞 전체를 본다 — 법령문의 '다만'은 문장을 새로
    열고 그 문장이 끝까지 이어지기 때문이다.
    """
    return _PROVISO_RE.search(text[:position]) is not None
