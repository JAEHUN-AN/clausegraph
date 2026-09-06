"""부칙에서 표준약관 적용례를 뽑는다.

## 왜 필요한가

지금까지 그래프는 세칙의 **시행일자**로만 버전을 갈랐다(notes/006). 그런데
부칙을 읽으면 약관의 적용은 그것과 다르다.

> 별표 14 및 별표 15의 개정규정은 **2003년 10월 1일부터 적용한다**
> 제5-13조 `<별표 15>` … 개정규정은 **2011. 4. 1. 이후 신계약부터 적용한다**
> 시행일 이후 **체결되는 보험계약부터** 적용할 수 있다

두 가지가 시행일자만으로는 안 잡힌다.

1. **적용일이 시행일과 다르다.** 세칙은 3월에 시행되고 약관은 4월부터
   적용될 수 있다.
2. **적용 대상이 신계약으로 한정된다.** 기존 계약에는 옛 약관이 그대로 남는다.

## 적용일을 단정하지 않는다

부칙 하나에 날짜가 여럿 나온다 — 세칙 시행일, 별표 적용일, 경과조치 기한이
한 문장에 섞인다. 어느 것이 "그 적용일"인지 기계가 고르면 틀린다.

그래서 **날짜를 후보로 모으고 원문을 함께 남긴다.** 적용 대상이 신계약으로
한정되는지는 표현이 분명해서 판정한다. 나머지는 심사자가 원문을 보고
정하도록 넘긴다 — 이 프로젝트가 계속 지켜 온 방식이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 부칙 블록과 그 공포일자를 짝으로 읽는다.
_APPENDIX_RE = re.compile(
    r"<부칙공포일자>(\d{8})</부칙공포일자>"
    r"<부칙공포번호>\d+</부칙공포번호>"
    r"<부칙내용\s*><!\[CDATA\[([\s\S]*?)\]\]></부칙내용>"
)

# 표준약관은 보험업감독업무시행세칙의 별표15다.
_STANDARD_TERMS_RE = re.compile(r"별표\s*15|별표15")

# `2003년 10월 1일`, `2011. 4. 1.` 두 표기가 섞여 있다.
_DATE_RE = re.compile(r"(\d{4})\s*[.년]\s*(\d{1,2})\s*[.월]\s*(\d{1,2})")

# 적용 대상이 신계약으로 한정되는 표현.
_NEW_CONTRACTS_RE = re.compile(r"신계약|신규로\s*체결|체결되는\s*보험계약|이후\s*체결")

# 문장 경계. 법령문은 '…한다.'로 끝난다.
_SENTENCE_RE = re.compile(r"(?<=다\.)\s*")

# 적용 대상 상품을 한정하는 표현. 두 방향이 다 나온다.
#
#   제외 — `표준약관(개인실손의료보험은 제외한다) 개정내용은 …`
#   포함 — `별표15 중 <자동차보험> …`, `별표15 중 보증보험 표준약관 …`
#
# **제외를 놓치면 정반대로 판정한다.** 2026-05-06 개정의 부칙은 적용일을
# 2026-06-06으로 미루면서 개인실손의료보험을 빼 두었다. 이걸 버전 전체에
# 걸면 실손 가입자에게 틀린 약관을 들이댄다 (notes/016).
# 조각 안의 상품 표기. `<자동차보험>`, `□ 기본형 실손의료보험` 두 모양이다.
# `별표`·`별지`·`붙임`은 상품 이름이 아니다. 이걸 안 빼면 `<별표 15>`가
# 상품으로 잡혀 "상품=별표"라는 답이 나온다.
_PRODUCT_TAG_RE = re.compile(
    r"[<□]\s*(?!별표|별지|부표|붙임)([가-힣][가-힣ㆍ·\s]{1,30}?)\s*[>·]?(?=\s|$)"
)

# 조각 안의 `X 표준약관` 표기. 부칙은 한 문장에 상품을 여럿 든다.
#
#   "생명보험 표준약관 제7조 및 질병ㆍ상해보험 표준약관(손해보험 회사용)
#    제7조의 개정규정은 2011. 4. 1. 이후 신계약부터 적용한다"
#
# 별표15 바로 뒤만 보면 첫 상품만 잡힌다. 조각 전체에서 모은다.
_PRODUCT_TERMS_RE = re.compile(r"([가-힣][가-힣ㆍ·\s]{1,28}?)\s*표준약관")

_EXCLUDES_RE = re.compile(r"표준약관\s*\(([^)]{2,40}?)은?\s*제외한다\)")

# 포함 표현은 두 모양으로 나온다.
#
#   `별표15. 표준약관 중 <자동차보험> ‘<25> …`   — 꺾쇠로 묶인 상품명
#   `별표15 중 보증보험 표준약관(…)`             — 뒤에 '표준약관'이 붙는다
#
# 꺾쇠 안의 이름은 그 자체로 분명하므로 뒤에 무엇이 오는지 따지지 않는다.
# 원문은 `별표15`, `[별표 15]`, `<별표 15>` 세 모양을 섞어 쓴다. 닫는
# 괄호를 허용하지 않으면 `[별표 15] 실손의료보험 표준약관`에서 상품을
# 놓친다 — 그러면 조문 단위 한정이 상품 전체로 읽힌다(notes/030).
_INCLUDES_RE = re.compile(
    r"별표\s*15\s*[\]>]?\s*(?:\.|중)?\s*(?:표준약관\s*중\s*)?"
    r"(?:<([가-힣ㆍ·\s]{2,30}?)>|([가-힣ㆍ·\s]{2,30}?)\s*표준약관)"
)


@dataclass(frozen=True)
class Provision:
    """표준약관 적용례 하나."""

    promulgated_on: str
    new_contracts_only: bool
    candidate_dates: tuple[str, ...]
    text: str
    # 적용 대상을 한정하는 상품 이름. 비어 있으면 한정하지 않는다.
    included_products: tuple[str, ...] = ()
    excluded_products: tuple[str, ...] = ()
    # 날짜 단위로 쪼갠 적용 단위. 한 부칙이 조문마다 다른 날짜를 정한다.
    scopes: tuple[Scope, ...] = ()

    @property
    def version_scope(self) -> Scope | None:
        """버전 전체를 옮길 수 있는 적용 단위. 없으면 None.

        **조문 일부만 바꾼 부칙으로는 버전을 옮길 수 없다.** 그러면 그 상품의
        나머지 조문까지 새 판본으로 끌려간다. 조문 단위 버전이 없는 지금
        구조에서는 옮기지 않고 그대로 두는 편이 맞다(notes/030).

        후보가 둘 이상이면 어느 것을 쓸지 부칙만 보고 정할 수 없어 None이다.
        """
        candidates = [
            scope
            for scope in self.scopes
            if scope.new_contracts_only and not scope.article_scoped
        ]
        return candidates[0] if len(candidates) == 1 else None

    @property
    def article_scopes(self) -> tuple[Scope, ...]:
        """조문·별표만 바꾼 적용 단위. 사람이 봐야 하는 자리다."""
        return tuple(scope for scope in self.scopes if scope.article_scoped)

    @property
    def summary(self) -> str:
        scope = "신계약부터" if self.new_contracts_only else "적용 대상 미확정"
        dates = ", ".join(self.candidate_dates) or "날짜 없음"
        return f"공포 {self.promulgated_on} / {scope} / 날짜 후보 {dates}"

    def covers_product(self, product: str) -> bool:
        """이 적용례가 그 상품에 걸리는가.

        상품명이 약관마다 길고 다르므로(`기본형 실손의료보험(급여 실손의료비)`)
        부칙의 짧은 표기(`개인실손의료보험`)와 글자로 맞추기 어렵다. 그래서
        핵심어가 서로의 안에 들어 있는지로 본다.
        """
        if any(same_product(name, product) for name in self.excluded_products):
            return False
        if not self.included_products:
            return True
        return any(same_product(name, product) for name in self.included_products)



# --- 적용 단위 ---
#
# 부칙 하나가 날짜 하나를 말하지 않는다. **한 부칙 안에서 조문마다 날짜가
# 다르다.**
#
#   "다만 [별표 15]의 <자동차보험> 제9조, 제11조의 개정사항은
#    2020년 10월 22일부터 시행하고, [별표 15]의 <자동차보험> 제1조,
#    제20조, 제27조 … 의 개정사항은 2020년 11월 10일부터 시행한다."
#
# 그래서 부칙을 **날짜 단위로 쪼갠다.** 각 조각이 그 날짜에 걸리는 상품과
# 조문을 자기 안에 들고 있다. 쪼개지 않으면 날짜 후보만 둘 남고 어느 것이
# 어디에 걸리는지 알 수 없어, 지금까지 "판정할 수 없다"로 넘겼다(notes/030).

# 조각의 끝은 날짜다. 날짜 뒤의 `부터/이후`까지 삼켜 경계를 분명히 한다.
_SEGMENT_END_RE = re.compile(
    r"(\d{4})\s*[.년]\s*(\d{1,2})\s*[.월]\s*(\d{1,2})\s*일?\s*(?:부터|이후|까지)?"
)

# 표준약관의 조문 번호. `제5-13조`처럼 하이픈이 든 것은 **세칙**의 조문이고
# 표준약관 조문이 아니다.
#
#   "별표 15 표준약관(제5-13조제1항관련) 손해보험 <자동차보험> 제11조의 개정내용"
#
# 여기서 표준약관 조문은 제11조 하나다. 제5-13조는 별표15를 달고 있는 세칙
# 조항이라, 이걸 조문 범위로 읽으면 엉뚱한 조문에 부칙을 건다.
_ARTICLE_RE = re.compile(r"제\s*(\d+)(?:\s*의\s*(\d+))?\s*조(?!\s*의?\s*\d)")
_SECHIK_ARTICLE_RE = re.compile(r"제\s*\d+\s*-\s*\d+\s*조")

# **부칙이 자기 조문을 가리키는 표기.** 이걸 표준약관 조문으로 읽으면
# 거의 모든 부칙이 "제1조·제2조가 바뀌었다"고 말하게 된다.
#
#   제2조(적용례) …            부칙의 조문 제목
#   제1조에도 불구하고 …        부칙 제1조(시행일)를 가리킨다
#   부칙 제1조 및 제3조제1항    앞에 '부칙'이 붙는다
#
# 조문을 뽑기 전에 이 표기들을 지운다.
_SELF_REFERENCE_RE = re.compile(
    r"부칙\s*제\s*\d+\s*조(?:\s*제\s*\d+\s*항)?"
    r"|제\s*\d+\s*조(?:\s*제\s*\d+\s*항)?\s*에도"
    r"|제\s*\d+\s*조\s*\([^)]*(?:적용|경과|시행|특례|조치|규정)[^)]*\)"
)

# `…을 제외한 개정사항` — 나열한 **조문을 빼는** 표현.
# `표준약관(개인실손의료보험은 제외한다)`는 상품을 빼는 표현이라 다르다.
_EXCEPT_ARTICLES_RE = re.compile(r"제외한\s*(?:개정|나머지|사항)")

# 별표 참조. 조문이 아니라 별표만 바뀐 부칙도 있다 — 그때도 **그 상품
# 전체가 바뀐 것은 아니다.**
_TABLE_RE = re.compile(r"별표\s*(\d+)")
# 별표15는 표준약관 자신이고, 나머지 두 자리 별표는 세칙의 다른 별표다.
_TERMS_TABLES = frozenset({"4", "13", "14", "15", "18", "23", "27"})


@dataclass(frozen=True)
class Scope:
    """부칙의 적용 단위 하나 — 이 날짜가 무엇에 걸리는가."""

    applies_on: str
    new_contracts_only: bool
    # 비어 있으면 별표15 전체에 걸린다.
    products: tuple[str, ...] = ()
    # 비어 있으면 그 상품의 모든 조문에 걸린다. 값이 있으면 **그 조문만**
    # 바뀐 것이므로 버전 전체를 옮기면 안 된다.
    articles: tuple[str, ...] = ()
    # 조문이 아니라 별표만 가리키는 부칙도 있다(`<자동차보험> 별표 1, 별표 3`).
    tables: tuple[str, ...] = ()
    # 조문을 나열하되 "…을 제외한"이라고 적은 경우. 나열된 조문이 빠진다.
    excludes_articles: bool = False
    text: str = ""

    @property
    def article_scoped(self) -> bool:
        """그 상품의 **일부만** 바뀐 부칙인가.

        일부만 바뀌었으면 버전 전체를 그 날짜로 옮길 수 없다. 조문 단위
        버전이 없는 지금 구조에서는 사람이 봐야 한다(notes/030).
        """
        return bool(self.articles or self.tables)


def parse_scopes(text: str) -> tuple[Scope, ...]:
    """부칙 본문을 날짜 단위로 쪼갠다."""
    flat = " ".join(text.split())
    scopes: list[Scope] = []
    start = 0
    for match in _SEGMENT_END_RE.finditer(flat):
        segment = flat[start : match.end()]
        start = match.end()
        # **별표15를 가리키지 않는 조각은 앞 문장의 이어짐이다.**
        #
        #   "② … [별표 15]의 개정사항 중 '…'을 제외한 개정사항은
        #    2009년 10월 1일 이후에 신규로 체결된 … 계약에 대해서도
        #    2019년 1월 1일부터 적용한다."
        #
        # 한 문장에 체결 기준일과 적용 기준일이 함께 있다. 뒤 날짜에서
        # 끊으면 상품도 조문도 없는 조각이 생기는데, 그건 새 적용 단위가
        # 아니다. 별표15를 언급하는지로 가른다(notes/030).
        if not _STANDARD_TERMS_RE.search(segment):
            continue
        year, month, day = match.group(1), match.group(2), match.group(3)
        scopes.append(
            Scope(
                applies_on=f"{year}-{int(month):02d}-{int(day):02d}",
                new_contracts_only=bool(_NEW_CONTRACTS_RE.search(flat[match.start():])),
                products=_products(segment),
                articles=_articles(segment),
                tables=_tables(segment),
                excludes_articles=bool(_EXCEPT_ARTICLES_RE.search(segment)),
                text=segment.strip(),
            )
        )
    return tuple(scopes)


def _articles(segment: str) -> tuple[str, ...]:
    """조각에 적힌 표준약관 조문 번호. 세칙 조문과 부칙 자기 조문은 뺀다."""
    cleaned = _SELF_REFERENCE_RE.sub(" ", segment)
    without_sechik = _SECHIK_ARTICLE_RE.sub(" ", cleaned)
    numbers: list[str] = []
    for whole, branch in _ARTICLE_RE.findall(without_sechik):
        number = f"{whole}의{branch}" if branch else whole
        if number not in numbers:
            numbers.append(number)
    return tuple(numbers)

def parse_provisions(admrul_xml: str) -> tuple[Provision, ...]:
    """행정규칙 본문 XML에서 표준약관 적용례를 뽑는다."""
    provisions: list[Provision] = []
    for promulgated_on, body in _APPENDIX_RE.findall(admrul_xml):
        sentences = [
            sentence.strip()
            for sentence in _SENTENCE_RE.split(body)
            if _STANDARD_TERMS_RE.search(sentence)
        ]
        if not sentences:
            continue

        joined = " ".join(sentences)
        dates = tuple(
            dict.fromkeys(
                f"{year}-{int(month):02d}-{int(day):02d}"
                for year, month, day in _DATE_RE.findall(joined)
            )
        )
        provisions.append(
            Provision(
                promulgated_on=promulgated_on,
                new_contracts_only=bool(_NEW_CONTRACTS_RE.search(joined)),
                candidate_dates=dates,
                text=" ".join(joined.split()),
                included_products=_scoped(_INCLUDES_RE, joined),
                excluded_products=_scoped(_EXCLUDES_RE, joined),
                scopes=parse_scopes(joined),
            )
        )
    return tuple(provisions)


def _scoped(pattern: re.Pattern[str], text: str) -> tuple[str, ...]:
    """정규식이 여러 그룹을 쓰면 findall이 튜플을 준다. 빈 그룹을 걸러 낸다."""
    names: list[str] = []
    for found in pattern.findall(text):
        candidates = found if isinstance(found, tuple) else (found,)
        for candidate in candidates:
            name = candidate.strip()
            if name and name not in names:
                names.append(name)
    return tuple(names)


# 상품명을 맞출 때 쓸모없는 수식어. 떼어 내면 부칙의 짧은 표기와 맞는다.
_PRODUCT_NOISE_RE = re.compile(r"기본형|개인|단체|특별약관\d*|\(.*?\)|\s+")


def same_product(left: str, right: str) -> bool:
    """두 상품 표기가 같은 상품을 가리키는가.

    `개인실손의료보험`(부칙)과 `기본형 실손의료보험(급여 실손의료비)`(약관)을
    맞춰야 한다. 수식어를 떼어 낸 뒤 한쪽이 다른 쪽에 들어 있으면 같다고 본다.
    """
    a = _PRODUCT_NOISE_RE.sub("", left)
    b = _PRODUCT_NOISE_RE.sub("", right)
    if not a or not b:
        return False
    return a in b or b in a


def applies_to_enrollment(provision: Provision, enrolled_on: str) -> bool | None:
    """가입일이 이 적용례의 대상인가. `enrolled_on`은 YYYYMMDD.

    `None`은 "판정할 수 없다"는 뜻이다.

    한때 "날짜 후보가 여럿이면 판정하지 않는다"로 두었다. 그런데 후보가
    여럿인 이유는 대개 **한 부칙이 조문마다 다른 날짜를 정하기 때문**이다.
    날짜 단위로 쪼개고 나면 그중 버전 전체에 걸리는 것이 하나로 좁혀지는
    경우가 많다(notes/030).

    좁혀지지 않는 경우는 그대로 `None`이다 — 조문 일부만 바꾼 부칙이거나,
    버전 전체에 걸리는 단위가 둘 이상인 경우다.
    """
    scope = provision.version_scope
    if scope is None:
        return None
    return enrolled_on >= scope.applies_on.replace("-", "")


def _tables(segment: str) -> tuple[str, ...]:
    """조각에 적힌 별표 번호. 세칙의 별표(별표15 등)는 뺀다."""
    numbers: list[str] = []
    for number in _TABLE_RE.findall(segment):
        if number in _TERMS_TABLES or number in numbers:
            continue
        numbers.append(number)
    return tuple(numbers)


# 상품 이름이 아닌 것들. `표준약관` 앞에 붙어도 상품이 아니다.
_NOT_A_PRODUCT = frozenset({"별표", "별지", "부표", "붙임", "및", "중", "의", "이"})

# 상품명 앞에 딸려 오는 접속어와 조문 꼬리.
#
#   "제7조 **및** 질병ㆍ상해보험 표준약관"  ->  '조 및 질병ㆍ상해보험'
#   "표준약관 **및** 해외여행 실손의료보험"  ->  '및 해외여행 실손의료보험'
#
# 앞에서부터 반복해 떼어 낸다.
_PRODUCT_PREFIX_RE = re.compile(r"^(?:조|항|호|목|및|또는|중|의|이|그)(?:\s+|$)")


def _clean_product(name: str) -> str:
    cleaned = " ".join(name.split())
    while True:
        stripped = _PRODUCT_PREFIX_RE.sub("", cleaned).strip()
        if stripped == cleaned:
            return cleaned
        cleaned = stripped


def _products(segment: str) -> tuple[str, ...]:
    """조각이 가리키는 상품. 세 표기를 모두 모은다.

    `별표15 <자동차보험>` · `[별표 15] 실손의료보험 표준약관` · `□ 기본형 …`
    """
    names: list[str] = []
    for source in (_INCLUDES_RE, _PRODUCT_TERMS_RE, _PRODUCT_TAG_RE):
        for name in _scoped(source, segment):
            cleaned = _clean_product(name)
            if cleaned and cleaned not in _NOT_A_PRODUCT and cleaned not in names:
                names.append(cleaned)
    return tuple(names)
