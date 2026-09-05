"""증상·시술 표현을 질병분류 코드로 옮긴다.

면책은 코드로 못박혀 있는데(`치과치료(K00~K08)`) 청구서에는 사람 말이
적힌다 — "충치가 심해 임플란트를 했습니다". 코드로 옮겨야 걸린다.

**코드표가 있으면 그것을 쓴다.** `data/kcd/sick_master.csv`(심평원 상병마스터,
47,798행)가 있으면 `kcd.index`의 사전으로 조회한다. 없으면 아래 표로
내려간다 — 수집 전에도 데모가 돌아야 하고, 폐쇄망에서 파일이 빠질 수 있다.

아래 표는 **대역이다.** 코드를 손으로 적어 두는 것은 코드표가 갱신될 때
어긋나고 그 어긋남을 아무도 모르므로, 임시로만 쓴다(notes/013).
"""

from __future__ import annotations

from functools import lru_cache

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
    """서술에서 코드를 찾는다. 코드표가 있으면 사전으로, 없으면 대역 표로."""
    index = _kcd_index()
    if index is not None:
        return index.lookup(narrative)

    codes: list[str] = []
    for term, mapped in _TERMS.items():
        if term in narrative:
            codes.extend(mapped)
    return tuple(dict.fromkeys(codes))


@lru_cache(maxsize=1)
def _kcd_index():
    """코드표를 한 번만 읽는다. 없으면 None을 돌려 대역 표로 내려간다."""
    from ..kcd.index import load_index

    try:
        return load_index()
    except FileNotFoundError:
        return None
