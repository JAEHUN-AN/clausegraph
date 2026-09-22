"""표준약관이 **실제로 정의하는 용어**를 모은다.

트리아지가 "이 용어는 표준약관에 정의가 없다"고 말하려면 먼저 무엇이
정의돼 있는지 알아야 한다. 짐작으로 말하면 안 되는 자리다 — 있는 것을
없다고 하면 조문을 가진 채로 사람에게 떠넘기게 된다.

## 정의는 세 가지 모양으로 적혀 있다

    1. 가·나·다 목록   "가. 장해: <부표 3> 장해분류표에서 정한 …"
    2. 표              실손 특별약관 제2조는 10,791자짜리 표다
    3. **다른 문서로 미룸**  "이 약관에서 사용하는 용어의 뜻은 <붙임1>과 같습니다"

세 번째가 이 모듈이 따로 세는 것이다. 형식상으로는 정의 조문이 있지만
**내용은 수집본에 없다.** 있다고 세면 트리아지가 "정의가 있으니 그걸
보라"고 답하게 되는데, 가리킨 곳에 아무것도 없다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# "가. 계약자: 회사와 계약을 체결하고 …"
_LIST_RE = re.compile(r"^\s*[가-힣]\.\s*([^:：\n]{1,24})\s*[:：]", re.M)
# "용어의 뜻은 <붙임1>과 같습니다" / "<부표 3> 참조"
_DEFERRED_RE = re.compile(r"[<〈]\s*(붙임\s*\d*|부표\s*\d*|별표\s*\d*)\s*[>〉]")
# 한 용어의 정의 본문으로 볼 길이. 넘으면 다음 항목까지 삼킨다.
_DEFINITION_BODY_CHARS = 200
# 표의 첫 칸. 너무 긴 것은 용어가 아니라 설명이다.
_TABLE_CELL_MAX = 14
_TABLE_HEAD_SKIP = {"용 어", "용어", "정 의", "정의", "구 분", "구분"}
# 표 괘선. 칸 앞뒤에 붙어 온다.
_BORDER_RE = re.compile(r"[┃│┣┫┏┓┗┛━]+")


@dataclass(frozen=True)
class TermIndex:
    """표준약관이 정의하는 용어와, 정의를 다른 문서로 미룬 자리.

    `deferred_to`가 비어 있지 않으면 **그 조문은 정의를 갖고 있지 않다.**
    """

    defined: frozenset[str] = frozenset()
    deferred_to: frozenset[str] = frozenset()
    article_count: int = 0
    # 정의는 있는데 그 정의가 **수집본에 없는 문서를 가리키는** 용어.
    #   "장해: <부표 3> 장해분류표에서 정한 기준에 따른 장해상태"
    # 조문은 손에 있지만 기준은 없다. `knows`가 True를 주면 트리아지가
    # "그 조문을 보라"고 하는데, 가 보면 또 다른 문서를 가리킨다.
    hollow: frozenset[str] = frozenset()

    def is_hollow(self, term: str) -> bool:
        """정의가 형식만 있고 내용이 다른 문서에 있는가."""
        return term.strip() in self.hollow

    def knows(self, term: str) -> bool:
        """그 용어의 정의가 수집본 안에 있는가. **정확히 같을 때만 그렇다.**

        처음에는 부분 일치를 허용했다 — 쟁점은 `입원비`라고 적고 약관은
        `입원의료비`로 적으니까. 그런데 `입원비`가 표에서 잘려 나온 `비`에
        걸려 **"정의가 있다"고 답했다.** 없는 정의를 있다고 하는 쪽이
        훨씬 나쁘다. 그러면 트리아지가 "그 조문을 보라"고 하는데 가리킨
        곳에 정의가 없다.

        정확 일치로 좁히면 `입원비`는 "정의 없음"이 되고, 그건 맞는 답이다 —
        `입원비`는 상품 약관의 용어다(notes/035).
        """
        cleaned = term.strip()
        return bool(cleaned) and cleaned in self.defined


def terms_from_articles(articles: Iterable[dict]) -> TermIndex:
    """파싱된 조문에서 정의된 용어를 뽑는다."""
    defined: set[str] = set()
    deferred: set[str] = set()
    hollow: set[str] = set()
    counted = 0

    for article in articles:
        title = article.get("title") or ""
        if "정의" not in title:
            continue
        counted += 1
        text = article.get("text") or ""

        found = {match.strip() for match in _LIST_RE.findall(text)}
        found |= _table_terms(text)
        defined |= {term for term in found if term}
        hollow |= _hollow_terms(text)

        # 정의를 못 찾았는데 다른 문서를 가리키고 있으면, 그 조문은
        # 정의를 미룬 것이다.
        if not found:
            deferred |= {
                match.replace(" ", "") for match in _DEFERRED_RE.findall(text)
            }

    return TermIndex(
        defined=frozenset(defined),
        deferred_to=frozenset(deferred),
        article_count=counted,
        hollow=frozenset(hollow),
    )


def _hollow_terms(text: str) -> set[str]:
    """정의 본문이 다른 문서를 가리키는 용어를 모은다.

    `장해: <부표 3> 장해분류표에서 정한 기준에 따른 장해상태를 말합니다.`
    조문은 있지만 기준은 그 표에 있고, 표는 수집본에 없다. 이걸 "정의가
    있다"로 세면 트리아지가 사람을 빈 곳으로 보낸다(notes/035).
    """
    found: set[str] = set()
    for match in _LIST_RE.finditer(text):
        body = text[match.end() : match.end() + _DEFINITION_BODY_CHARS]
        # 다음 항목이 시작되면 거기서 끊는다.
        nxt = _LIST_RE.search(body)
        if nxt:
            body = body[: nxt.start()]
        if _DEFERRED_RE.search(body):
            found.add(match.group(1).strip())
    return found


def _table_terms(text: str) -> set[str]:
    """표로 적힌 정의에서 용어 칸만 모은다.

    표는 한 용어를 **여러 줄에 걸쳐** 적는다. 줄 단위로 첫 칸을 주우면
    `파치료」`, `료·증식치료`, `비` 같은 조각이 용어로 들어온다. 그 조각
    하나가 색인에 앉으면 `입원비`가 `비`에 걸려 "정의가 있다"가 된다.
    그래서 조각으로 보이는 것은 버린다.
    """
    if "│" not in text:
        return set()
    terms: set[str] = set()
    for line in text.splitlines():
        if "│" not in line:
            continue
        head = _BORDER_RE.sub("", line.split("│")[0]).strip()
        if _is_term(head):
            terms.add(head)
    return terms


def _is_term(candidate: str) -> bool:
    """용어로 받아들일 모양인가. 표에서 잘려 나온 조각을 거른다."""
    if not candidate or candidate in _TABLE_HEAD_SKIP:
        return False
    if not 2 <= len(candidate) <= _TABLE_CELL_MAX:
        return False
    # 표 괘선·따옴표가 붙어 있으면 잘린 조각이다.
    if any(ch in candidate for ch in "━┃│「」『』┏┓┗┛┣┫"):
        return False
    # 가운뎃점으로 시작하거나 끝나면 앞뒤가 잘린 것이다.
    if candidate[0] in "·・" or candidate[-1] in "·・":
        return False
    # 한글이 반은 넘어야 용어다. 숫자·기호 조각을 거른다.
    hangul = sum(1 for ch in candidate if "가" <= ch <= "힣")
    return hangul * 2 >= len(candidate)


@dataclass
class _Loaded:
    index: TermIndex = field(default_factory=TermIndex)


def load_index(parsed_dir: Path) -> TermIndex:
    """수집한 판본 전체에서 용어를 모은다.

    한 판본에만 있는 정의도 있으므로 합집합으로 둔다 — 트리아지는 "어느
    판본에도 없다"를 말할 때만 없다고 해야 한다.
    """
    files = sorted(parsed_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"파싱된 약관이 없다: {parsed_dir}")

    defined: set[str] = set()
    deferred: set[str] = set()
    hollow: set[str] = set()
    counted = 0
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        index = terms_from_articles(data.get("articles", []))
        defined |= index.defined
        deferred |= index.deferred_to
        hollow |= index.hollow
        counted += index.article_count
    return TermIndex(
        defined=frozenset(defined),
        deferred_to=frozenset(deferred),
        article_count=counted,
        hollow=frozenset(hollow),
    )
