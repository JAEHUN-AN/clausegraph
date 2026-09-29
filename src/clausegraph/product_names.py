"""도구에 들어온 상품명을 그래프의 정식 명칭으로 옮긴다.

상품명은 그래프 조회의 열쇠라 **한 글자만 달라도 조항이 0개**로 나온다.
그런데 그 0개는 오류가 아니라 판정으로 나간다 — "보장 조항을 찾지 못했다"는
`NEEDS_DOCS`다. 사용자가 `특별약관1(중증 비급여)`라고 줄여 말한 걸 모델이
그대로 넘기면, 면책 3건이 걸려야 할 청구가 **서류 보완 요청**이 된다
(Claude Desktop에 붙여 돌려 보다가 발견).

그래서 도구 입구에서 이름을 확정한다. 원칙은 하나다 — **하나로 좁혀질
때만 바꾸고, 바꿨다고 말한다.** 둘 이상이면 고르지 않고 후보를 돌려준다.
국내/해외처럼 이름이 가르는 상품을 추측으로 고르면 그게 지어내는 것이다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

# 공백·괄호·가운뎃점은 사람마다 다르게 쓴다. 상품을 가르는 글자가 아니다.
_NOISE = re.compile(r"[\s()（）\[\]·ㆍ・,./-]+")


@dataclass(frozen=True)
class ProductMatch:
    name: str | None
    """확정된 정식 명칭. 확정하지 못했으면 None."""
    exact: bool = False
    """들어온 이름이 정식 명칭 그대로였는가. 아니면 바꿨다고 알려야 한다."""
    candidates: tuple[str, ...] = ()
    """확정하지 못했을 때 사용자에게 되물을 후보."""


def _normalize(name: str) -> str:
    return _NOISE.sub("", name)


def match_product(query: str, known: Iterable[str]) -> ProductMatch:
    """`query`를 `known` 중 하나로 확정하거나, 되물을 후보를 돌려준다.

    1. 정식 명칭 그대로면 그대로.
    2. 공백·괄호를 걷어 내고 같으면 그것.
    3. 걷어 낸 이름을 **앞머리로** 가진 상품이 하나뿐이면 그것.
       안에 품기만 한 상품(`해외여행 실손…`)은 앞머리 후보가 없을 때만 센다
       — 앞에 붙는 말은 생략된 말이 아니라 상품을 가르는 말이기 때문이다.
    """
    names = tuple(sorted(set(known)))
    if query in names:
        return ProductMatch(name=query, exact=True)

    wanted = _normalize(query)
    if not wanted:
        return ProductMatch(name=None)

    normalized = {name: _normalize(name) for name in names}
    same = [name for name in names if normalized[name] == wanted]
    if len(same) == 1:
        return ProductMatch(name=same[0])

    starts = [name for name in names if normalized[name].startswith(wanted)]
    contains = [name for name in names if wanted in normalized[name]]
    for pool in (starts, contains):
        if len(pool) == 1:
            return ProductMatch(name=pool[0])
        if pool:
            return ProductMatch(name=None, candidates=tuple(pool))
    return ProductMatch(name=None)
