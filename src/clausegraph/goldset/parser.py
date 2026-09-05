"""FSS 분쟁조정사례 HTML 파서.

목록은 `table.list-data`, 상세 본문은 `div.n-dbdata`에 있다. 상세 본문은
HWP 내보내기 결과라 한 문장이 인라인 스타일 span 수십 개로 쪼개져 있어,
태그를 걷어내고 다시 이어붙이는 과정이 필요하다.
"""

from __future__ import annotations

import html
import re

from .models import CaseRef

_TOTAL_RE = re.compile(r"전체\s*<em>(\d+)</em>\s*건")
_PAGES_RE = re.compile(r"페이지\s*<em>\d+</em>\s*/\s*(\d+)")
_TBODY_RE = re.compile(
    r'<table[^>]*class="[^"]*list-data[^"]*"[^>]*>.*?<tbody>(.*?)</tbody>', re.S
)
_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELLS_RE = re.compile(
    r"<td>(\d+)</td>\s*"          # 번호
    r"<td>(.*?)</td>\s*"          # 권역
    r"<td>(.*?)</td>\s*"          # 유형
    r'<td><a href="(.*?)">(.*?)</a></td>\s*'  # 제목 + 상세 링크
    r"<td>(.*?)</td>",            # 등록일
    re.S,
)
_SLNO_RE = re.compile(r"caseSlno=(\d+)")
_SECTION_RE = re.compile(r"▣\s*([^\n]{1,40})")
_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_END_RE = re.compile(r"</(?:p|div|li|tr|h\d)\s*>|<br\s*/?>", re.I)
_BLANKS_RE = re.compile(r"[ \t ]+")
_NEWLINES_RE = re.compile(r"\n{3,}")

# 본문 컨테이너. 중첩 div가 있어 정규식으로는 닫는 태그를 못 맞춘다.
_BODY_OPEN = '<div class="n-dbdata">'
_DIV_TOKEN_RE = re.compile(r"<div\b|</div\s*>", re.I)


def parse_total(list_html: str) -> tuple[int, int]:
    """목록 페이지에서 (전체 건수, 전체 페이지 수)를 읽는다."""
    total = _TOTAL_RE.search(list_html)
    pages = _PAGES_RE.search(list_html)
    if not total or not pages:
        raise ValueError("목록 페이지에서 건수/페이지 수를 찾지 못했다 — 마크업이 바뀌었을 수 있다")
    return int(total.group(1)), int(pages.group(1))


def parse_list(list_html: str) -> list[CaseRef]:
    """목록 페이지의 행들을 CaseRef로 변환한다."""
    tbody = _TBODY_RE.search(list_html)
    if not tbody:
        raise ValueError("목록 테이블(table.list-data > tbody)을 찾지 못했다")

    refs: list[CaseRef] = []
    for row in _ROW_RE.findall(tbody.group(1)):
        cells = _CELLS_RE.search(row)
        if not cells:
            continue
        seq, rgnl, cvpl, href, title, registered = cells.groups()
        slno = _SLNO_RE.search(href)
        if not slno:
            continue
        refs.append(
            CaseRef(
                case_slno=int(slno.group(1)),
                seq=int(seq),
                rgnl=_clean(rgnl),
                cvpl=_clean(cvpl),
                title=_clean(title),
                registered_on=_clean(registered),
            )
        )
    return refs


def extract_body(view_html: str) -> str:
    """상세 페이지 본문 컨테이너의 내부 HTML을 잘라낸다.

    중첩 div를 세어 여는 태그와 짝이 맞는 지점까지 읽는다.
    """
    start = view_html.find(_BODY_OPEN)
    if start < 0:
        raise ValueError("본문 컨테이너(div.n-dbdata)를 찾지 못했다")

    inner_start = start + len(_BODY_OPEN)
    depth = 1
    for token in _DIV_TOKEN_RE.finditer(view_html, inner_start):
        depth += 1 if token.group(0).lower().startswith("<div") else -1
        if depth == 0:
            return view_html[inner_start : token.start()]
    raise ValueError("본문 컨테이너의 닫는 태그를 찾지 못했다")


def html_to_text(fragment: str) -> str:
    """인라인 span으로 쪼개진 HWP 산출 HTML을 평문으로 되돌린다."""
    with_breaks = _BLOCK_END_RE.sub("\n", fragment)
    stripped = _TAG_RE.sub("", with_breaks)
    unescaped = html.unescape(stripped).replace(" ", " ")
    lines = (_BLANKS_RE.sub(" ", line).strip() for line in unescaped.split("\n"))
    return _NEWLINES_RE.sub("\n\n", "\n".join(lines)).strip()


def split_sections(body_text: str) -> dict[str, str]:
    """'▣ 민원내용' 같은 마커로 본문을 나눈다.

    마커가 없으면 빈 dict를 돌려준다 — 호출부가 평문으로 폴백하도록.
    """
    markers = list(_SECTION_RE.finditer(body_text))
    if not markers:
        return {}

    sections: dict[str, str] = {}
    for idx, marker in enumerate(markers):
        end = markers[idx + 1].start() if idx + 1 < len(markers) else len(body_text)
        name = marker.group(1).strip()
        content = body_text[marker.end() : end].strip()
        # 같은 이름이 두 번 나오면 이어붙인다 — 덮어써서 잃지 않도록.
        sections[name] = f"{sections[name]}\n\n{content}" if name in sections else content
    return sections


def _clean(raw: str) -> str:
    return html.unescape(_TAG_RE.sub("", raw)).replace(" ", " ").strip()
