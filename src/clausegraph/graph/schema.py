"""그래프 스키마.

    (:Product {name})                    약관 상품 14개
      <-[:OF_PRODUCT]- (:Article)
    (:Version {effective_from, effective_to, sha})
      <-[:IN_VERSION]- (:Article)
    (:Article)-[:HAS_ITEM]->(:Item)      호 — 면책 사유가 열거되는 단위
    (:Article)-[:HAS_COVERAGE]->(:Coverage)-[:EXCLUDES]->(:Item)
        실손 계열은 면책을 보장종목별 표로 적는다. 같은 조문 안에서도
        상해급여와 질병급여의 사유가 다르므로 보장종목을 노드로 둔다.
    (:Article|:Item)-[:REFERS_TO {in_proviso}]->(:Article)
        조문 간 참조. 같은 판본·같은 상품 안에서만 잇는다 — 같은 '제3조'가
        판본 4개 × 상품 16개에 흩어져 있으므로 번호만으로는 못 잇는다.
        `in_proviso`는 그 참조가 '다만' 뒤에 있다는 뜻이다. 면책의 예외가
        보상 조문을 가리키는 자리가 여기다 (notes/022).
    (:Article)-[:SUPERSEDES]->(:Article) 같은 상품·같은 번호의 이전 버전
    (:Version)-[:SUPERSEDES]->(:Version)
    (:Version)-[:HAS_PROVISION]->(:Provision)
        부칙 적용례. 세칙 시행일과 약관 적용일이 다르고, 적용 대상이
        신계약으로 한정되는 경우가 있다 (notes/015).

면책 조문에는 `:Exclusion` 라벨을 덧붙인다. 보장을 무효화하는 조항은
따로 짚을 수 있어야 하고, 그게 이 프로젝트의 핵심이다.

**조문은 버전에 매인다.** 같은 '제5조'라도 시행일자에 따라 내용이 다르므로
Article은 (버전, 상품, 번호)로 식별한다. 버전 없이 조문 하나를 두고
내용만 갈아끼우면 "가입 시점의 약관"을 되살릴 수 없다.
"""

from __future__ import annotations

# effective_to가 열린 버전을 비교 가능한 값으로 다룬다.
OPEN_ENDED = "99991231"

CONSTRAINTS = (
    "CREATE CONSTRAINT product_name IF NOT EXISTS "
    "FOR (p:Product) REQUIRE p.name IS UNIQUE",
    "CREATE CONSTRAINT version_from IF NOT EXISTS "
    "FOR (v:Version) REQUIRE v.effective_from IS UNIQUE",
    "CREATE CONSTRAINT article_uid IF NOT EXISTS "
    "FOR (a:Article) REQUIRE a.uid IS UNIQUE",
    "CREATE CONSTRAINT item_uid IF NOT EXISTS "
    "FOR (i:Item) REQUIRE i.uid IS UNIQUE",
    "CREATE CONSTRAINT coverage_uid IF NOT EXISTS "
    "FOR (c:Coverage) REQUIRE c.uid IS UNIQUE",
    "CREATE CONSTRAINT provision_uid IF NOT EXISTS "
    "FOR (p:Provision) REQUIRE p.uid IS UNIQUE",
)

INDEXES = (
    "CREATE INDEX article_number IF NOT EXISTS FOR (a:Article) ON (a.number)",
    "CREATE INDEX article_unit IF NOT EXISTS FOR (a:Article) ON (a.unit)",
    "CREATE INDEX version_range IF NOT EXISTS "
    "FOR (v:Version) ON (v.effective_from, v.effective_to)",
)


def article_uid(effective_from: str, unit: str, number: str) -> str:
    return f"{effective_from}/{unit}/제{number}조"


def item_uid(article: str, paragraph_no: int, item_no: int) -> str:
    return f"{article}#{paragraph_no}-{item_no}"


def coverage_uid(article: str, coverage: str) -> str:
    return f"{article}#{coverage}"


def provision_uid(promulgated_on: str) -> str:
    """부칙 적용례. 공포일자로 식별한다."""
    return f"부칙/{promulgated_on}"


def table_item_uid(article: str, coverage: str, paragraph_no: int, item_no: int) -> str:
    """표에서 뽑은 면책 사유. 같은 조문 안에서도 보장종목마다 다르다."""
    return f"{article}#{coverage}#{paragraph_no}-{item_no}"
