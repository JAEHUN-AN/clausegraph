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
#   치아우식 -> K02, 복사의 골절 -> S82, 임신중 과다구토 -> O21, 치질 -> K64,
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

# 병명이 아닌데 병명 끝에 붙어 tail 열쇠가 되는 낱말. 청구서에는 거의
# 늘 나온다.
#   `상엽, 기관지 또는 폐의 악성 신생물, 오른쪽`  ->  tail '오른쪽' = C34
#   `벌레먹은 발바닥`(요스)                      ->  tail '발바닥' = A66
# '오른쪽 손목 골절' 청구에 폐암 코드가 붙었다(2026-10-20 실측, notes/044).
# 측성·부위 낱말은 병을 가리키지 않으므로 열쇠로 쓰지 않는다.
NON_DISEASE_KEYS = frozenset({"오른쪽", "왼쪽", "양쪽", "발바닥", "손바닥", "기관"})
# `기관`(R89 검사물의 이상소견)은 청구서에서 거의 늘 '의료기관'·'양성기관'의 일부다.
# 붙은 낱말에 따라 코드 하나가 보장종목 판별(질병/상해)을 바꿨다(검증 MEDIUM-1).

# 병명과 일상어의 동음이의. `주사`(L71, 酒皶 — 딸기코)는 청구서에서 거의
# 언제나 주사(注射)다. '주사를 맞았다'가 피부병 코드를 물어 왔다.
# 딸기코는 `딸기코증`(L71.1) 전체 병명 열쇠로 여전히 찾는다.
HOMONYM_KEYS = frozenset({"주사", "유착", "가스"})
# `유착`(K00.22 치아의 유착) — 청구서의 유착은 거의 늘 장기 유착(복막 K66.0 등)이다.
#   '복부유착 박리술'이 치과치료(K00∼K08) **확실 면책**으로 부지급됐다(검증 HIGH-1).
# `가스`(T59 가스의 독성 효과) — '복부가스'(고창)는 질병인데 T가 상해 규칙을 골랐다.

# 병명 열쇠를 **품은** 병명 아닌 낱말. 이 범위와 겹쳐 맞은 열쇠는 **약한 코드**다.
#
# 긴 열쇠는 부분 일치만 보므로 앞뒤에 무엇이 붙었는지 모른다. 병명이 아닌 낱말이
# 병명을 품으면 그대로 확실 코드가 됐다(2026-10-21 반박 검증, notes/046).
#   '세계자연유산 관광 중 넘어져'  ->  `자연유산`(O03)
#   '세계 유산 탐방 중 넘어짐'     ->  `유산`(O03·O05·O06) — 띄어 써서 앞 글자 검사를 지난다
#   '유산소 운동 중 무릎 통증'     ->  `유산` — 有酸素
#   '유산균 복용 후 설사'          ->  `유산` — 乳酸菌
#   '유산슬 먹고 식중독'           ->  `유산` — 중식 요리
#   '장유착방지제 도포'            ->  `장유착`(K56) — 짧은 `유착`은 HOMONYM_KEYS가 막는다
# O03은 임신·출산(O00∼O99) 면책 코드라 **확실 면책 -> 부지급**이었다.
#
# HOMONYM_KEYS처럼 열쇠를 버리지 않는 이유 — `자연유산`·`유산`은 진짜 병명이다.
# 문맥 판단이 틀려도 약한 코드는 지급으로 새지 않고 불확실 면책으로 사람에게 간다.
#   - '자연유산'은 2024년부터 국가유산의 한 갈래(문화·자연·무형유산)라 앞말 없이도
#     '자연유산 탐방'처럼 쓴다. 의료 문맥(후·으로·진단·소파술)과 겹치지 않는 뒷말만 둔다.
#   - 띄어 쓴 앞말에 '자연'은 넣지 않는다 — '자연 유산 후 소파술'은 진짜 병명이다.
#   - '유산소'는 **뒤에 올 말을 정해 둔다.** '유산 소파술'·'유산 소견'·'유산 소독'을 붙여
#     쓰면 글자가 '유산소…'라, 뒷말을 막는 쪽으로 두면 진짜 병명이 약해진다(검토 MEDIUM-1).
HERITAGE_VISITS = (
    "탐방", "관광", "답사", "명소", "여행", "트레킹", "방문", "지구", "공원", "지역",
    "등재", "유적", "견학", "체험", "투어", "관람",
)
AEROBIC_TAILS = (
    "운동", "성", "능력", "훈련", "활동", "를", "가", "는", "도", "에", "로", "와", "만",
)
HOMONYM_CONTEXTS = (
    re.compile(r"(?:세계|국가|유네스코|지정)\s*자연\s*유산"),
    re.compile(rf"자연\s*유산\s*(?:{'|'.join(HERITAGE_VISITS)})"),
    re.compile(r"(?:문화|세계|국가|무형|역사)\s*유산"),
    re.compile(r"유산(?:균|슬)"),
    re.compile(rf"유산소(?:{'|'.join(AEROBIC_TAILS)}|(?![가-힣]))"),
    re.compile(r"유착\s*(?:방지|예방|차단)"),
)

# 이 길이 이하의 열쇠는 앞에 한글이 붙어 있으면, 그 한글이 **병명 수식어로
# 끝날 때만** 확실한 코드로 친다. 그 밖에 붙어 나온 것은 **약한 코드**다.
#
# 2글자 열쇠는 앞 낱말의 끝에 걸린다. '검사비만 청구합니다'(검사비 + 만)의
# `비만`이 E66을 물어 왔고, E66은 면책 코드라 **확실** 면책 -> 부지급으로
# 뒤집혔다(2026-10-20, 2회 재현).
#
# 처음에는 붙은 것을 전부 버렸다. 그러자 붙여 쓴 **진짜 병명**을 잃었다 —
# '수술후섬망'(F05) 입원이 급여 실손에서 DENIED에서 PARTIAL 2,400,000원이
# 됐다(검토 MEDIUM-1). 수식어 목록으로 되살렸지만 목록은 닫혀 있어 '야간섬망'
# '금단섬망'이 또 빠졌다(검증 REG-1). 목록을 아무리 늘려도 이 모양은 남는다.
#
# 그래서 **버리지 않는다.** 목록 밖 앞말에 붙어 나온 코드는 `lookup_weak`로
# 따로 돌려준다. 약한 코드는 판정에서 두 가지만 한다.
#   - 면책 코드 범위에 들면 **불확실** 면책 -> 사람 확인 (확실 면책이 아니다)
#   - 보장종목 판별(상해/질병)과 지급 규칙 선택에는 쓰지 않는다
# '검사비만'은 그래서 부지급도 지급도 아니고 사람에게 간다.
#
# 목록은 실측으로 고른 수식어만 둔다. '양성'(養成 — '양성기관'), '자연'
# ('자연가스'), '수술 후'(공백이 들어가 _glued_prefix와 맞을 수 없다)는 뺐다.
BOUNDED_KEY_MAX_LEN = 2
DISEASE_PREFIXES = (
    "급성", "만성", "아급성", "초기", "말기", "조기", "진행성", "재발성", "반복성",
    "습관성", "선천성", "후천성", "외상성", "노인성", "소아", "영아", "신생아", "산후",
    "임신성", "알코올성", "약물성", "세균성", "바이러스성", "흡인성", "혈관성", "이차성",
    "원발성", "속발성", "전이성", "악성", "수술후", "금단", "야간", "중환자실", "고도",
    "병적", "중증", "경증", "경도", "중등도", "복부", "과다",
)
_HANGUL_RE = re.compile(r"[가-힣]")

# 병명 앞에 붙는 수식어. 떼어 내면 청구서의 말과 맞을 확률이 올라간다.
_MODIFIER_RE = re.compile(
    r"^(?:상세불명의?|기타의?|기타\s+명시된|명시되지\s*않은|달리\s*분류되지\s*않은|"
    r"그\s*밖의?|미상의?|불명의?)\s*"
)
# 이름 **중간**의 괄호는 잘라 내는 것이 아니라 **덜어 낸다.**
#
# 한때 `[,(].*$`로 첫 괄호부터 끝까지 잘랐다. 그러면 괄호가 중간에 있는
# 병명이 앞머리 수식어만 남는다.
#
#     "만성 (소아기) 육아종성 질환"  ->  "만성"      -> 열쇠 '만성' = D71
#     "특정(개별) 공포증"            ->  "특정"      -> 열쇠 '특정' = F40
#
# 그 결과 청구 서술의 `특정부위`가 F40(공포성 불안장애)을 물어 왔고,
# 코드가 붙으면 **확실(certain) 면책**이 된다 — 근거 없는 부지급이다.
# 실제 분쟁조정사례에서 이 오발동을 잡았다(notes/025).
#
# 괄호만 덜어 내면 남는 이름이 오히려 좋아진다.
#     "만성 (소아기) 육아종성 질환"  ->  "만성 육아종성 질환"
_PARENTHETICAL_RE = re.compile(r"[(\[][^)\]]*[)\]]")
# 쉼표 뒤는 자른다 — `질환, 상세불명` 꼴의 뒷가지는 수식어다.
#
# 다만 쉼표가 **나열**일 때가 있다. 그때 자르면 또 앞머리 수식어만 남는다.
#     "급성, 재발성 또는 아급성 전방포도막염"  ->  "급성"  -> 열쇠 '급성'
# 그래서 **자른 결과가 조각이면 자르지 않은 이름을 쓴다.** 조각인지는
# 길이로 본다 — 두 글자 이하로 줄었으면 병명이 아니라 수식어다.
_TRAILING_RE = re.compile(r",.*$")
MIN_TRUNCATED_LEN = 3
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
        return self._scan(narrative)[0]

    def lookup_weak(self, narrative: str) -> tuple[str, ...]:
        """확실하지 않게 맞은 열쇠의 코드. 둘이다 — 병명 수식어가 아닌 앞말에
        붙어 나온 짧은 열쇠, 그리고 병명 아닌 낱말(`HOMONYM_CONTEXTS`) 안에
        든 열쇠.

        `lookup`과 겹치는 코드는 뺀다. 판정에서는 불확실 면책으로만 쓴다
        (`BOUNDED_KEY_MAX_LEN` 설명).
        """
        strong, weak = self._scan(narrative)
        return tuple(code for code in weak if code not in strong)

    def _scan(self, narrative: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        text = narrative
        for everyday, official in SYNONYMS.items():
            if everyday in text:
                text = f"{text} {official}"

        found: list[str] = []
        weak: list[str] = []
        matched: list[str] = []
        shadows = _homonym_spans(text)
        for term in self._terms:
            occurrence = _occurrence(term, text, shadows)
            if occurrence is None:
                continue
            # 이미 맞은 더 긴 열쇠의 부분 문자열이면 건너뛴다 —
            # `복사의 골절`이 맞았는데 `골절`까지 누적하면 M·P·S·T가 섞인다.
            if any(term in longer for longer in matched):
                continue
            if occurrence == "weak":
                weak.extend(code for code in self._by_term[term] if code not in weak)
                continue
            matched.append(term)
            for code in self._by_term[term]:
                if code not in found:
                    found.append(code)
        return tuple(found), tuple(weak)


def _occurrence(
    term: str, text: str, shadows: tuple[tuple[int, int], ...] = ()
) -> str | None:
    """열쇠가 서술에 어떻게 나타나는가 — "strong", "weak", 없으면 None.

    병명 아닌 낱말(`shadows`, `HOMONYM_CONTEXTS`)과 겹친 출현은 weak다.
    짧은 열쇠는 앞에 붙은 한글이 병명 수식어(`DISEASE_PREFIXES`)로 끝나거나
    아무것도 안 붙었을 때 strong이다. 한 번이라도 strong으로 나타나면 strong이다.
    """
    # 열쇠 2만여 개 대부분은 안 맞는다. `in`이 `find` 호출보다 싸다.
    if term not in text:
        return None
    seen = None
    start = text.find(term)
    while start != -1:
        if _shadowed(start, start + len(term), shadows):
            seen = "weak"
        elif len(term) > BOUNDED_KEY_MAX_LEN:
            return "strong"
        else:
            glued = _glued_prefix(text, start)
            if not glued or glued.endswith(DISEASE_PREFIXES):
                return "strong"
            seen = "weak"
        start = text.find(term, start + 1)
    return seen


def _homonym_spans(text: str) -> tuple[tuple[int, int], ...]:
    """서술에서 병명 아닌 낱말이 차지한 범위."""
    return tuple(
        match.span() for pattern in HOMONYM_CONTEXTS for match in pattern.finditer(text)
    )


def _shadowed(start: int, end: int, shadows: tuple[tuple[int, int], ...]) -> bool:
    """[start, end)가 병명 아닌 낱말의 범위와 겹치는가."""
    return any(start < finish and begin < end for begin, finish in shadows)


def _glued_prefix(text: str, start: int) -> str:
    """`start` 바로 앞에 띄어쓰기 없이 붙은 한글 덩어리."""
    begin = start
    while begin > 0 and _HANGUL_RE.match(text[begin - 1]):
        begin -= 1
    return text[begin:start]


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
    without_parens = _WS_RE.sub(" ", _PARENTHETICAL_RE.sub(" ", name)).strip()
    cleaned = _WS_RE.sub(" ", _TRAILING_RE.sub("", without_parens)).strip()
    # 잘랐더니 조각만 남았으면 자르지 않은 쪽을 쓴다.
    if cleaned != without_parens and len(cleaned) < MIN_TRUNCATED_LEN:
        cleaned = without_parens
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
        blocked = NON_DISEASE_KEYS | HOMONYM_KEYS
        full_keys = [key for key in full_keys if key not in blocked]
        tail_keys = [key for key in tail_keys if key not in blocked]
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
