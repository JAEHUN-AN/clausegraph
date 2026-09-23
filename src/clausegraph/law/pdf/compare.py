"""같은 문서의 두 파싱 결과를 맞대고 채점한다.

XML 평문과 PDF 조판본은 **같은 별표 15**다. 그러니 조문 집합도, 조문 본문의
글자도 같아야 한다. 다르면 둘 중 하나가 틀린 것이다 — 이게 이 모듈의 전부다.

## 표 줄은 양쪽에서 걷어내고 비교한다

XML은 표를 괘선 문자로 그리고 셀을 고정 폭으로 채운다. PDF는 선으로 그린다.
같은 표라도 **글자열이 애초에 다르다**. 표의 내용은 `pdf.tables`에서 따로
맞대고, 조문 본문 비교에서는 양쪽 다 표 줄을 뺀다.

## 공백을 지우고 비교한다

줄바꿈 위치가 조판마다 다르다. XML은 monospace 폭에서, PDF는 실제 글꼴 폭에서
접힌다. 줄바꿈 자리가 다르다는 사실은 결함이 아니므로, 공백을 전부 지운
글자열로 비교한다. **남는 차이는 글자가 실제로 다른 경우뿐이다.**
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Article, TermsDocument

# 괘선 — 굵은 표(외곽)와 가는 표(중첩) 두 벌이 다 쓰인다.
BOX_CHARS = frozenset("┏┓┗┛┣┫┳┻╋━┃┌┐└┘├┤┬┴┼─│")

_WHITESPACE_RE = re.compile(r"\s+")


def has_box(line: str) -> bool:
    return any(char in BOX_CHARS for char in line)


def body_text(article: Article) -> str:
    """표 줄을 뺀 조문 본문을 공백 없이."""
    kept = [line for line in article.text.split("\n") if not has_box(line)]
    return _WHITESPACE_RE.sub("", "\n".join(kept))


def depth(article: Article) -> tuple[int, int, int]:
    """(항, 호, 목) 개수."""
    items = sum(len(p.items) for p in article.paragraphs)
    subitems = sum(len(i.subitems) for p in article.paragraphs for i in p.items)
    return len(article.paragraphs), items, subitems


@dataclass(frozen=True)
class Mismatch:
    key: str
    field: str
    xml_value: str
    pdf_value: str


@dataclass(frozen=True)
class Report:
    xml_only: tuple[str, ...]
    pdf_only: tuple[str, ...]
    shared: tuple[str, ...]
    title_mismatches: tuple[Mismatch, ...]
    depth_mismatches: tuple[Mismatch, ...]
    body_mismatches: tuple[Mismatch, ...]
    pages_found: int
    recovered_total: int = 0
    """평문에서 항 번호가 깨져 순서로 되살린 항의 수."""
    recovered_confirmed: int = 0
    """그중 PDF에 같은 번호의 항이 실제로 있는 것."""

    @property
    def xml_total(self) -> int:
        return len(self.shared) + len(self.xml_only)

    @property
    def recall(self) -> float:
        """XML 조문 중 PDF에서도 같은 키로 잡힌 비율."""
        return len(self.shared) / self.xml_total if self.xml_total else 0.0

    @property
    def body_agreement(self) -> float:
        if not self.shared:
            return 0.0
        return 1 - len(self.body_mismatches) / len(self.shared)


def _by_key(document: TermsDocument) -> dict[str, Article]:
    """같은 키가 두 번 나오면 뒤엣것을 버린다.

    본문에 같은 상품·같은 번호가 두 번 나오는 구간이 있다(자동차보험의
    `<예 시>` 등). 비교는 첫 등장만 본다 — 양쪽에서 같은 규칙을 쓰므로
    한쪽만 유리해지지 않는다.
    """
    table: dict[str, Article] = {}
    for article in document.articles:
        table.setdefault(article.key, article)
    return table


def compare(xml_doc: TermsDocument, pdf_doc: TermsDocument, pages: dict[str, int]) -> Report:
    left = _by_key(xml_doc)
    right = _by_key(pdf_doc)

    shared = sorted(set(left) & set(right))
    titles: list[Mismatch] = []
    depths: list[Mismatch] = []
    bodies: list[Mismatch] = []
    recovered = confirmed = 0

    for key in shared:
        a, b = left[key], right[key]
        # 평문에서 깨진 항 번호를 순서로 되살렸다. 그 추론이 맞았는지는
        # 조판본이 답을 갖고 있다 — 같은 번호의 항이 거기 있으면 맞다.
        pdf_numbers = {p.number for p in b.paragraphs}
        for paragraph in a.paragraphs:
            if not paragraph.recovered:
                continue
            recovered += 1
            confirmed += paragraph.number in pdf_numbers
        if a.title != b.title:
            titles.append(Mismatch(key, "title", a.title, b.title))
        if depth(a) != depth(b):
            depths.append(Mismatch(key, "depth", str(depth(a)), str(depth(b))))
        left_body, right_body = body_text(a), body_text(b)
        if left_body != right_body:
            bodies.append(Mismatch(key, "body", left_body, right_body))

    return Report(
        xml_only=tuple(sorted(set(left) - set(right))),
        pdf_only=tuple(sorted(set(right) - set(left))),
        shared=tuple(shared),
        title_mismatches=tuple(titles),
        depth_mismatches=tuple(depths),
        body_mismatches=tuple(bodies),
        pages_found=sum(1 for key in shared if key in pages),
        recovered_total=recovered,
        recovered_confirmed=confirmed,
    )


# 사용자 정의 영역. 글꼴 안에서만 뜻이 있는 자리라 글자로는 못 읽는다.
_PRIVATE_USE = (0xE000, 0xF8FF)

# 상자 안 내용이 본문에 섞였을 때 첫 글자로 자주 나오는 표기.
_BOX_OPENERS = "【[‣▶◦※"


def _is_private(char: str) -> bool:
    return _PRIVATE_USE[0] <= ord(char) <= _PRIVATE_USE[1]


def divergence(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    return next((i for i in range(limit) if left[i] != right[i]), limit)


def classify(mismatch: Mismatch) -> str:
    """불일치의 원인을 가른다 — 고칠 수 있는 것과 원천이 잃은 것을 구분하려고."""
    left, right = mismatch.xml_value, mismatch.pdf_value
    index = divergence(left, right)
    after_left = left[index : index + 1]
    after_right = right[index : index + 1]

    if after_left == "?":
        return "XML 문자 손실"
    if after_right and _is_private(after_right):
        return "PDF 글꼴 전용 글자"
    if after_right in _BOX_OPENERS:
        return "PDF 상자 미탐지"
    if not after_left:
        return "XML이 먼저 끝남"
    if not after_right:
        return "PDF가 먼저 끝남"
    return "기타"


def breakdown(report: Report) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for mismatch in report.body_mismatches:
        kind = classify(mismatch)
        counts[kind] = counts.get(kind, 0) + 1
    return sorted(counts.items(), key=lambda item: -item[1])


def first_difference(left: str, right: str, window: int = 30) -> str:
    """어디서부터 갈라지는지 한 줄로 보여 준다."""
    index = divergence(left, right)
    head = left[max(0, index - window) : index]
    return f"…{head}‖XML:{left[index : index + window]!r} / PDF:{right[index : index + window]!r}"
