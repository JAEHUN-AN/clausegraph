"""표준약관 평문을 조문 구조로 파싱한다.

계층은 이렇다.

    □ 생명보험                     <- 섹션 (9개)
      <화재보험>                   <- 하위구분 (손해보험 안에만 있다)
        제1관 목적 및 용어의 정의    <- 관 또는 절
          제5조(보험금을 지급하지 않는 사유)
            ① 항
              1. 호               <- 면책 사유가 이 단위로 열거된다
                가. 목

조문 번호는 상품마다 새로 시작한다. 화재보험 제4조와 자동차보험 제5조가
둘 다 '보상하지 않는 손해'다 — 번호만으로는 조문을 가리킬 수 없다.

문서의 물리적 배치가 목차와 어긋난다는 점도 걸림돌이다. 목차상 배상책임·
채무이행보증·신용·신원보증보험은 손해보험(Ⅱ)의 6~9번이지만, 본문에서는
해외여행 실손 뒤에 `□` 없이 `<배상책임보험>`으로 나온다. 그래서 조문의
소속은 `section`이 아니라 **`unit`(가장 구체적인 상품 표기)** 으로 잡는다.
"""

from __future__ import annotations

import re

from .models import Article, Item, Paragraph, Subitem, TermsDocument

# 상품 이름은 첫 `<` 앞까지다. 뒤에 붙는 개정 표기는 이름이 아니다.
#
# 예전에는 `<개정 …>` 한 짝이 그 줄에서 닫힌다고 보고 잘랐는데, PDF
# 조판본에서는 개정 날짜가 길어 **표기가 다음 줄로 넘어간다.** 그러면 짝이
# 닫히지 않아 통째로 이름에 남고, '생명보험 <개정 2005.2.15., …'가 상품이
# 된다 (notes/036). 닫히든 말든 첫 `<`에서 끊으면 두 경우가 같아진다.
_SECTION_RE = re.compile(r"^\s*□\s*([^<]+?)\s*(?:<.*)?$")
# 개정·신설 표기는 상품 구분이 아니다. XML 평문에서는 이 표기가 언제나
# 앞 문장 뒤에 붙어 나와서 걸린 적이 없었는데, 같은 문서의 PDF 조판본에서는
# 줄이 접히면서 `<개정 2014.12.26.>` 한 줄이 통째로 남는다. 그러면 '개정
# 2014.12.26.'이 상품 이름이 되고, 뒤따르는 조문이 전부 그 밑으로 샌다
# (notes/036 — PDF 대조에서 드러났다).
#
# 뒤에 붙는 표기는 `□`와 같은 이유로 닫히지 않아도 받는다 — `<화재보험>
# <개정 2005.2.15., …,`처럼 개정 날짜가 다음 줄로 넘어간다. 다만 `<`로
# 시작하지 않는 꼬리는 받지 않는다. `<별표 1> 대인배상 지급 기준`은 상품
# 구분이 아니라 표 제목이다.
_SUBSECTION_RE = re.compile(
    r"^\s*<(?!개\s*정|신\s*설|단서\s*신설)([가-힣][^>]{0,30})>\s*(?:<.*)?$"
)
_CHAPTER_RE = re.compile(r"^\s*(제\s?\d+\s?[관절]\s+.+?)\s*$")
# 조문 머리글의 **번호 부분만** 정규식으로 잡는다. 제목은 괄호 깊이를 세어
# 따로 끊는다 — 이유는 `match_article` 참고.
_ARTICLE_HEAD_RE = re.compile(r"^\s*제\s?(\d+)\s?조(?:의\s?(\d+))?\s*(?=[(\[])")

# 제목을 묶는 괄호. 표준약관은 두 짝을 섞어 쓴다.
_TITLE_BRACKETS = {"(": ")", "[": "]"}

# 항 번호에 쓰이는 글자가 한 벌이 아니다. 조판본에는 `➄`(U+2784) 계열이
# 섞여 있는데 `⑤`(U+2464)와 **다른 글자**다. 한쪽만 보면 그 항을 못 읽는다.
CIRCLED_PRIMARY = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
CIRCLED_DINGBAT = "➀➁➂➃➄➅➆➇➈➉"
CIRCLED_CHARS = CIRCLED_PRIMARY + CIRCLED_DINGBAT


def circled_number(char: str) -> int:
    """원 숫자 글자를 번호로. 두 벌 중 어느 쪽이든 받는다."""
    if char in CIRCLED_PRIMARY:
        return CIRCLED_PRIMARY.index(char) + 1
    return CIRCLED_DINGBAT.index(char) + 1


_PARAGRAPH_RE = re.compile(rf"^\s*([{CIRCLED_CHARS}])\s*(.*)$")

# 항 번호가 통째로 깨져 물음표만 남은 줄.
#
#   XML  `? 회사가 제2항에 따라 일부보장 제외조건을 …`
#   PDF  `➄ 회사가 제2항에 따라 일부보장 제외조건을 …`
#
# 별표내용을 평문으로 옮기는 과정에서 원 숫자 계열 글자가 사라진다.
# 이 줄을 항으로 읽지 않으면 **그 항이 통째로 앞 항에 붙는다** (notes/036).
_LOST_PARAGRAPH_RE = re.compile(r"^\s*\?\s+(\S.*)$")
_ITEM_RE = re.compile(r"^\s*(\d+)\.\s+(.*)$")
_SUBITEM_RE = re.compile(r"^\s*([가-힣])\.\s+(.*)$")

_REVISION_RE = re.compile(r"<(?:개정|신설)\s*([^>]*)>")
_DATE_RE = re.compile(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})")

# 상품 구분이 아닌 <...> 표기.
#
# `<예 시>`는 자동차보험 약관을 통째로 감싸는 래퍼다 — 이걸 상품으로 읽으면
# 자동차보험 조문이 전부 '예 시' 밑으로 새어 나간다. 부표·붙임·별표·목차도
# 조문 구조를 따르지 않는다. 이 표기들을 만나면 진행 중인 조문만 끊고
# **상품 문맥은 그대로 둔다**.
_NON_PRODUCT_RE = re.compile(r"^\s*<\s*(?:부표|붙\s*임|별표|목\s*차|예\s*시)")

# 같은 표기가 제목으로도 쓰이고 본문에서 가리키는 말로도 쓰인다.
#
#   제목  `<부표 4> 재해분류표`
#   본문  `<부표 4-1> ‘보험금을 지급할 때의 적립이율 계산’에 따릅니다.`
#
# 본문 쪽을 제목으로 읽으면 **진행 중인 조문이 거기서 끊긴다.** 법령문의
# 문장은 `…다.`로 끝나므로, 문장으로 끝나는 줄은 제목이 아니라고 본다.
_SENTENCE_TAIL_RE = re.compile(r"다\.\s*$")

# 표 안의 줄은 조문 구조로 읽으면 안 된다.
_TABLE_CHARS = frozenset("┌┐└┘├┤┬┴┼─│")



def match_article(line: str) -> tuple[str, str, str] | None:
    r"""조문 머리글을 (번호, 제목, 남은 본문)으로 끊는다. 아니면 None.

    한때 이걸 정규식 하나로 했다.

        r"^\s*제\s?(\d+)\s?조(?:의\s?(\d+))?\s*\(([^)]*)\)\s*(.*)$"

    두 군데서 틀렸고, 둘 다 조용히 틀렸다(notes/022).

    **1. 대괄호 제목을 못 읽어 조문을 통째로 버렸다.** 표준약관은 제목 안에
    괄호가 겹칠 때 대괄호로 바꿔 쓴다.

        제27조[보험료의 납입이 연체되는 경우 납입최고(독촉)와 계약의 해지]

    소괄호만 인정했으므로 이 줄은 조문 머리글로 인식되지 않았고, 내용은
    **앞 조문의 본문에 붙었다.** 판본 10개에서 60개 조문이 이렇게 사라졌다.

    **2. 제목 안의 괄호에서 제목이 잘렸다.** `[^)]*`는 첫 `)`에서 멈춘다.

        제26조(보험료의 납입이 연체되는 경우 납입최고(독촉)와 계약의 해지)
        -> 제목 "보험료의 납입이 연체되는 경우 납입최고(독촉"
           본문 "와 계약의 해지) ① 계약자가 …"

    제목이 잘리고 남은 조각이 본문 앞에 붙었다. 60개가 이 상태였다.

    그래서 **여는 괄호의 짝을 찾을 때까지 깊이를 센다.** 정규식으로 균형
    괄호를 세려 하지 않는다.
    """
    head = _ARTICLE_HEAD_RE.match(line)
    if head is None:
        return None

    opener = line[head.end()]
    closer = _TITLE_BRACKETS[opener]

    depth = 0
    for index in range(head.end(), len(line)):
        char = line[index]
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                number, branch = head.group(1), head.group(2)
                return (
                    f"{number}의{branch}" if branch else number,
                    line[head.end() + 1 : index].strip(),
                    line[index + 1 :].strip(),
                )
    # 짝이 그 줄에서 닫히지 않으면 조문 머리글로 보지 않는다. 제목이 여러
    # 줄로 넘어가는 경우는 없었고, 추측해서 자르면 제목과 본문이 섞인다.
    return None

def parse_terms(text: str, effective_on: str, admrul_seq: int) -> TermsDocument:
    """표준약관 평문에서 조문을 뽑는다."""
    articles: list[Article] = []
    sections: list[str] = []
    section: str | None = None
    subsection: str | None = None
    chapter: str | None = None
    buffer: list[str] = []
    header: tuple[str, str] | None = None  # (번호, 제목)

    def flush() -> None:
        if header is None or section is None:
            return
        articles.append(_build_article(section, subsection, chapter, header, buffer))

    for line in text.split("\n"):
        if _is_table_line(line):
            buffer.append(line)
            continue

        if (match := _SECTION_RE.match(line)) and "□" in line:
            flush()
            header, buffer = None, []
            section = match.group(1).strip()
            subsection = chapter = None
            sections.append(section)
            continue

        if _NON_PRODUCT_RE.match(line) and not _SENTENCE_TAIL_RE.search(line):
            flush()
            header, buffer = None, []
            continue

        if (match := _SUBSECTION_RE.match(line)) and section is not None:
            flush()
            header, buffer = None, []
            subsection = match.group(1).strip()
            chapter = None
            continue

        if (match := _CHAPTER_RE.match(line)) and match_article(line) is None:
            flush()
            header, buffer = None, []
            chapter = match.group(1).strip()
            continue

        if (parsed := match_article(line)) and section is not None:
            flush()
            number, title, rest = parsed
            header = (number, title)
            buffer = [rest] if rest else []
            continue

        if header is not None:
            buffer.append(line)

    flush()
    return TermsDocument(
        effective_on=effective_on,
        admrul_seq=admrul_seq,
        articles=tuple(articles),
        sections=tuple(sections),
    )


def extract_revisions(text: str) -> tuple[str, ...]:
    """`<개정 2014.12.26., 2018.3.2.>`에서 날짜를 뽑아 YYYY-MM-DD로 돌려준다."""
    dates: list[str] = []
    for marker in _REVISION_RE.findall(text):
        for year, month, day in _DATE_RE.findall(marker):
            iso = f"{year}-{int(month):02d}-{int(day):02d}"
            if iso not in dates:
                dates.append(iso)
    return tuple(sorted(dates))


def _build_article(
    section: str,
    subsection: str | None,
    chapter: str | None,
    header: tuple[str, str],
    buffer: list[str],
) -> Article:
    number, title = header
    body = "\n".join(buffer).rstrip()
    return Article(
        section=section,
        subsection=subsection,
        chapter=chapter,
        number=number,
        title=title,
        text=body,
        paragraphs=_parse_paragraphs(body),
        revised_on=extract_revisions(f"{title}\n{body}"),
    )


def _parse_paragraphs(body: str) -> tuple[Paragraph, ...]:
    """항 표기로 나눈다. 표기가 없으면 통째로 1항 하나로 담는다."""
    blocks: list[tuple[int, list[str], bool]] = []
    for line in body.split("\n"):
        if _is_table_line(line):
            if blocks:
                blocks[-1][1].append(line)
            continue

        if match := _PARAGRAPH_RE.match(line):
            blocks.append((circled_number(match.group(1)), [match.group(2)], False))
            continue

        # 항 번호가 물음표로 깨져 있으면 **순서로** 되살린다. 원천이 잃은
        # 글자를 다른 문서에서 가져오지 않는다 — ④ 다음의 깨진 자리는 ⑤다.
        if (lost := _LOST_PARAGRAPH_RE.match(line)) and blocks:
            blocks.append((blocks[-1][0] + 1, [lost.group(1)], True))
            continue

        if blocks:
            blocks[-1][1].append(line)

    if not blocks:
        return (
            Paragraph(number=1, text=body.strip(), items=_parse_items(body), implicit=True),
        )

    leading = body.split("\n")[0] if body else ""
    paragraphs = [
        Paragraph(
            number=number,
            text="\n".join(lines).strip(),
            items=_parse_items("\n".join(lines)),
            recovered=recovered,
        )
        for number, lines, recovered in blocks
    ]
    # 항 앞에 붙은 도입 문장은 버리지 않는다.
    if leading.strip() and not _PARAGRAPH_RE.match(leading):
        paragraphs.insert(
            0, Paragraph(number=0, text=leading.strip(), items=(), implicit=True)
        )
    return tuple(paragraphs)


def _parse_items(text: str) -> tuple[Item, ...]:
    blocks: list[tuple[int, list[str]]] = []
    for line in text.split("\n"):
        match = None if _is_table_line(line) else _ITEM_RE.match(line)
        if match:
            blocks.append((int(match.group(1)), [match.group(2)]))
        elif blocks:
            blocks[-1][1].append(line)

    return tuple(
        Item(
            number=number,
            text="\n".join(lines).strip(),
            subitems=_parse_subitems("\n".join(lines)),
        )
        for number, lines in blocks
    )


def _parse_subitems(text: str) -> tuple[Subitem, ...]:
    blocks: list[tuple[str, list[str]]] = []
    for line in text.split("\n"):
        match = None if _is_table_line(line) else _SUBITEM_RE.match(line)
        if match:
            blocks.append((match.group(1), [match.group(2)]))
        elif blocks:
            blocks[-1][1].append(line)

    return tuple(
        Subitem(label=label, text="\n".join(lines).strip()) for label, lines in blocks
    )


def _is_table_line(line: str) -> bool:
    return any(char in _TABLE_CHARS for char in line)
