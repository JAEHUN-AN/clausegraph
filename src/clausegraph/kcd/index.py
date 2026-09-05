"""KCD 코드 사전 — 코드가 존재하는지, 무슨 병인지, 어떤 말로 불리는지.

세 가지를 답한다.

1. **`exists(code)`** — 이 코드가 실제로 있는가. notes/012에서 로컬 LLM이
   `치핵 -> G00.1`, `발목 골절 -> L84.0`처럼 표기는 맞고 챕터가 틀린 코드를
   지어냈다. 모양 검사로는 못 막는다. 이제 존재 여부를 물을 수 있다.
2. **`name(code)`** — 그 코드가 무슨 병인가. 판정 근거에 코드만 적으면
   사람이 되짚을 수 없다.
3. **`lookup(narrative)`** — 서술에 나타난 병명으로 코드를 찾는다.
   `agents/terminology.py`의 하드코딩 8개를 대체한다.

## 용어 색인을 어떻게 만드는가

상병마스터의 한글명을 그대로 색인하면 쓸모가 없다. 실제 병명이
`상세불명의 급성 편도염`처럼 수식어 범벅이라 청구서의 "편도염"과 글자가
맞지 않는다.

그래서 한글명에서 **수식어를 떼어 낸 핵심어**도 함께 색인한다.
`상세불명의 급성 편도염` -> `급성 편도염`, `편도염`.

다만 짧게 자를수록 여러 코드가 같은 열쇠를 갖는다. 그래서 **열쇠가 가리키는
코드가 너무 많으면 버린다** — notes/009에서 변별어를 문서빈도로 고른 것과
같은 생각이다.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CSV = Path("data/kcd/sick_master.csv")
SOURCE_ENCODING = "cp949"

CODE_COLUMN = "상병기호"
NAME_COLUMN = "한글명"
ENGLISH_COLUMN = "영문명"
COMPLETE_COLUMN = "완전코드구분"

# 열쇠 하나가 여러 코드를 가리키는 것은 정상이다 — '우울증'은 F32·F33에
# 걸친다. 그래서 세분류를 **3자리로 묶은 뒤** 그 개수를 제한한다. 세분류
# 그대로 셌을 때는 흔한 병명이 전부 버려져 희귀 조합만 남았다(notes/013).
MAX_CATEGORIES_PER_KEY = 8
MIN_KEY_LEN = 2

# 일상어 -> KCD 공식 표기.
#
# 코드표는 공식 병명만 담는다. 청구서에는 일상어가 적힌다 — '충치'는
# KCD에 없고 `치아우식`이며, '발목'은 `복사`, '입덧'은 `과다구토`다.
# 그래서 이 표는 **코드를 담지 않는다.** 일상어를 공식 표기로 바꿔 주고,
# 코드는 언제나 코드표에서 찾는다. 코드를 손으로 적으면 코드표가 갱신될 때
# 어긋나고, 그 어긋남을 아무도 모른다.
# 아래는 전부 코드표에서 실제 표기를 확인한 것만 담았다.
#   치아우식 -> K02, 복사의 골절 -> S82, 임신중 과다구토 -> O21, 치질 -> K64
# 확인하지 못한 일상어는 넣지 않는다. 추측한 동의어는 엉뚱한 면책을 걸고,
# 왜 걸렸는지 아무도 설명하지 못한다.
SYNONYMS: dict[str, str] = {
    "충치": "치아우식",
    "발목 골절": "복사의 골절",
    "입덧": "과다구토",
    "치핵": "치질",
}

# 상병으로 쓰지 않는 챕터. 서술과 우연히 겹쳐 엉뚱한 코드를 물어 온다.
#   U — 특수목적 코드
#   Y — 손상의 외부요인·의료사고 (상병이 아니다)
#   Z — 보건서비스 접촉 이유
#
# 2글자 열쇠는 긴 낱말 안에 부분 일치할 수 있다 — '간병인'의 '병인',
# '영양수액'의 '수액'. 형태소 분석 없이는 못 막는다. 다만 2글자 열쇠를
# 버리면 `치질`(K64)·`비만`(E66)까지 잃는다. 그래서 남겨 두고, 이 잡음이
# 어떤 면책 구간에도 들지 않는다는 것을 측정으로 확인했다(notes/013).
EXCLUDED_CHAPTERS = frozenset("UYZ")

# 병명 앞에 붙는 수식어. 떼어 내면 청구서의 말과 맞을 확률이 올라간다.
_MODIFIER_RE = re.compile(
    r"^(?:상세불명의?|기타의?|기타\s+명시된|명시되지\s*않은|달리\s*분류되지\s*않은|"
    r"그\s*밖의?|미상의?|불명의?)\s*"
)
_TRAILING_RE = re.compile(r"[,(].*$")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class Disease:
    code: str
    name: str
    english: str
    complete: bool


class KcdIndex:
    """상병마스터를 코드 사전으로 만든다."""

    def __init__(self, diseases: list[Disease]) -> None:
        self._by_code = {disease.code: disease for disease in diseases}
        self._by_term = _build_term_index(diseases)
        # 긴 열쇠부터 본다 — '급성 편도염'이 '편도염'보다 정확하다.
        self._terms = tuple(sorted(self._by_term, key=len, reverse=True))

    @property
    def size(self) -> int:
        return len(self._by_code)

    @property
    def term_count(self) -> int:
        return len(self._by_term)

    def exists(self, code: str) -> bool:
        """이 코드가 상병마스터에 있는가.

        약관은 `F04~F99`처럼 3자리로 범위를 적고 청구서에는 `F32.1` 같은
        세분류가 온다. 세분류를 못 찾으면 3자리로 한 번 더 본다.
        """
        normalized = code.strip().upper()
        if normalized in self._by_code:
            return True
        return normalized.split(".")[0] in self._by_code

    def name(self, code: str) -> str | None:
        normalized = code.strip().upper()
        disease = self._by_code.get(normalized) or self._by_code.get(
            normalized.split(".")[0]
        )
        return disease.name if disease else None

    def lookup(self, narrative: str) -> tuple[str, ...]:
        """서술에 나타난 병명으로 3자리 분류를 찾는다.

        일상어를 먼저 공식 표기로 바꾼다 — 청구서의 '충치'는 코드표에
        없고 `치아우식`이다.
        """
        text = narrative
        for everyday, official in SYNONYMS.items():
            if everyday in text:
                text = f"{text} {official}"

        found: list[str] = []
        for term in self._terms:
            if term in text:
                for code in self._by_term[term]:
                    if code not in found:
                        found.append(code)
        return tuple(found)


def load_index(csv_path: Path = DEFAULT_CSV) -> KcdIndex:
    if not csv_path.exists():
        raise FileNotFoundError(
            f"코드표가 없다: {csv_path} — kcd.collect를 먼저 돌릴 것"
        )
    with csv_path.open(encoding=SOURCE_ENCODING, newline="") as handle:
        rows = list(csv.DictReader(handle))
    diseases = [
        Disease(
            code=(row.get(CODE_COLUMN) or "").strip().upper(),
            name=(row.get(NAME_COLUMN) or "").strip(),
            english=(row.get(ENGLISH_COLUMN) or "").strip(),
            complete=(row.get(COMPLETE_COLUMN) or "").strip() == "Y",
        )
        for row in rows
    ]
    return KcdIndex([disease for disease in diseases if disease.code])


def term_keys(name: str) -> list[str]:
    """병명에서 색인할 열쇠들을 만든다.

    `상세불명의 급성 편도염` -> ['상세불명의 급성 편도염', '급성 편도염', '편도염']
    """
    cleaned = _WS_RE.sub(" ", _TRAILING_RE.sub("", name)).strip()
    if not cleaned:
        return []

    keys = [cleaned]
    stripped = _MODIFIER_RE.sub("", cleaned).strip()
    if stripped and stripped != cleaned:
        keys.append(stripped)

    # 마지막 낱말만 남긴 형태도 넣는다 — 청구서는 대개 그렇게 쓴다.
    tail = stripped.split(" ")[-1] if stripped else ""
    if tail and tail not in keys:
        keys.append(tail)

    return [key for key in keys if len(key) >= MIN_KEY_LEN]


def category_of(code: str) -> str:
    """세분류를 3자리 분류로 묶는다. `F32.1` -> `F32`.

    약관은 면책을 3자리 범위로 적는다(`F04~F99`). 판정에 필요한 것은
    세분류가 아니라 어느 분류에 드는지다.
    """
    return re.sub(r"[^A-Z0-9]", "", code.strip().upper())[:3]


def _build_term_index(diseases: list[Disease]) -> dict[str, tuple[str, ...]]:
    buckets: dict[str, list[str]] = defaultdict(list)
    for disease in diseases:
        category = category_of(disease.code)
        if not category or category[0] in EXCLUDED_CHAPTERS:
            continue
        for key in term_keys(disease.name):
            if category not in buckets[key]:
                buckets[key].append(category)

    return {
        key: tuple(categories)
        for key, categories in buckets.items()
        if len(categories) <= MAX_CATEGORIES_PER_KEY
    }
