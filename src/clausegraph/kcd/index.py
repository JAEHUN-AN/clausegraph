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

# 열쇠를 두 종류로 나눠 다르게 다룬다. 실측하면 성질이 전혀 다르다.
#
#   전체 병명 열쇠  24,463개  분류 수 중위 1, p95 1, 최대 10
#   tail 열쇠        2,923개  분류 수 중위 1, p95 6, 최대 609 ('장애'=183)
#
# 전체 병명은 본래 정확하다. 오염원은 tail이다 — `분만힘의 이상`에서 뽑은
# `이상`이 K00·K07·O62를 끌어와 선천성 뇌질환 청구에 치과치료 면책을
# 발동시켰다. 2글자 tail(`수술`·`이상`·`장애`)은 한국어에서 너무 흔하다.
#
# 이 값으로 오발동이 4건에서 0건이 됐다. 적중은 6/8에서 5/8로 하나 줄지만,
# 오발동은 곧 잘못된 부지급이고 놓침은 지급 쪽으로 기울므로 이 거래가 맞다.
MAX_CATEGORIES_PER_FULL_KEY = 10
MAX_CATEGORIES_PER_TAIL_KEY = 3
MIN_KEY_LEN = 2
MIN_TAIL_KEY_LEN = 3

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
        matched: list[str] = []
        for term in self._terms:
            if term not in text:
                continue
            # 이미 맞은 더 긴 열쇠의 부분 문자열이면 건너뛴다 —
            # `복사의 골절`이 맞았는데 `골절`까지 누적하면 M·P·S·T가 섞인다.
            if any(term in longer for longer in matched):
                continue
            matched.append(term)
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


def term_keys(name: str) -> tuple[list[str], list[str]]:
    """병명에서 색인할 열쇠들을 만든다. (전체 병명 열쇠, tail 열쇠).

    `상세불명의 급성 편도염` -> (['상세불명의 급성 편도염', '급성 편도염'], ['편도염'])

    tail을 따로 돌려주는 이유는 상한이 다르기 때문이다. 전체 병명은
    정확하지만 tail은 흔한 낱말이 되기 쉽다.
    """
    cleaned = _WS_RE.sub(" ", _TRAILING_RE.sub("", name)).strip()
    if not cleaned:
        return [], []

    full = [cleaned]
    stripped = _MODIFIER_RE.sub("", cleaned).strip()
    if stripped and stripped != cleaned:
        full.append(stripped)

    # 마지막 낱말만 남긴 형태 — 청구서는 대개 그렇게 쓴다.
    tail = stripped.split(" ")[-1] if stripped else ""
    tails = (
        [tail]
        if tail and tail not in full and len(tail) >= MIN_TAIL_KEY_LEN
        else []
    )
    return [key for key in full if len(key) >= MIN_KEY_LEN], tails


def category_of(code: str) -> str:
    """세분류를 3자리 분류로 묶는다. `F32.1` -> `F32`.

    약관은 면책을 3자리 범위로 적는다(`F04~F99`). 판정에 필요한 것은
    세분류가 아니라 어느 분류에 드는지다.
    """
    return re.sub(r"[^A-Z0-9]", "", code.strip().upper())[:3]


def _build_term_index(diseases: list[Disease]) -> dict[str, tuple[str, ...]]:
    full_buckets: dict[str, list[str]] = defaultdict(list)
    tail_buckets: dict[str, list[str]] = defaultdict(list)

    for disease in diseases:
        category = category_of(disease.code)
        if not category or category[0] in EXCLUDED_CHAPTERS:
            continue
        full_keys, tail_keys = term_keys(disease.name)
        for key in full_keys:
            if category not in full_buckets[key]:
                full_buckets[key].append(category)
        for key in tail_keys:
            if category not in tail_buckets[key]:
                tail_buckets[key].append(category)

    index = {
        key: tuple(categories)
        for key, categories in full_buckets.items()
        if len(categories) <= MAX_CATEGORIES_PER_FULL_KEY
    }
    # 전체 병명 열쇠가 이미 있으면 tail로 덮지 않는다.
    for key, categories in tail_buckets.items():
        if key not in index and len(categories) <= MAX_CATEGORIES_PER_TAIL_KEY:
            index[key] = tuple(categories)
    return index
