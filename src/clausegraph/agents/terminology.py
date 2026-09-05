"""증상·시술 표현을 질병분류 코드로 옮긴다.

**여기가 이 시스템에서 LLM이 실제로 필요한 자리다.**

면책은 코드로 못박혀 있는데(치과치료 K00∼K08), 청구서에는 코드가 아니라
사람 말이 적힌다 — "충치가 심해 임플란트를 했습니다". '임플란트'도 '충치'도
약관에는 없는 낱말이라 표현 일치로는 잡히지 않는다. 코드로 옮겨야 걸린다.

아래 표는 그 자리를 보여 주기 위한 **대역**이다. 실제로는 KCD 용어집이나
LLM이 맡아야 하고, 어느 쪽이든 결과를 코드로 내놓기만 하면 이 자리에
그대로 꽂힌다. 판정 자체는 여전히 코드 대조로 결정론적으로 이뤄진다.
"""

from __future__ import annotations

# 표현 -> KCD 코드. 데모용 최소 표본이다.
_TERMS: dict[str, tuple[str, ...]] = {
    "임플란트": ("K08",),
    "충치": ("K02",),
    "치아": ("K08",),
    "우울증": ("F32",),
    "요실금": ("N39.3",),
    "치핵": ("K64",),
    "비만": ("E66",),
    "제왕절개": ("O82",),
}


def lookup(narrative: str) -> tuple[str, ...]:
    codes: list[str] = []
    for term, mapped in _TERMS.items():
        if term in narrative:
            codes.extend(mapped)
    return tuple(dict.fromkeys(codes))
