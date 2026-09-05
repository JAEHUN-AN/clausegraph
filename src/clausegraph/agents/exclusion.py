"""3. 면책검증 — 걸릴 수 있는 사유를 전부 세운 뒤 걸러낸다.

notes/008에서 나온 결론을 그대로 구조로 옮겼다. **면책 조회는 랭킹 문제가
아니라 열거 문제다.** 닮은 것을 순서대로 주는 방식으로는 절반을 놓친다.
그래서 이 에이전트는 먼저 그 상품·그 버전의 면책 사유를 빠짐없이 세우고,
거기서 걸러낸다.

거르는 근거는 두 가지다.

1. **질병분류 코드 범위** — 약관이 못박은 구간에 진단코드가 들어가는가.
   결정론적이고, 왜 걸렸는지 코드로 설명된다.
2. **변별력 있는 표현** — 코드가 없는 면책(보조기, 간병비, 영양제 …)은
   조항의 낱말이 청구 서술에 나타나는지로 본다. 다만 아무 낱말이나 쓰면
   안 된다. '치료', '진료', '비급여'는 약관 어디에나 나오므로 무엇에나
   걸린다. 그래서 **약관 전체에서 문서빈도가 낮은 낱말만** 쓴다. 면책
   조항들 안에서만 재면 안 된다 — 조항이 짧아 흔한 말도 드물어 보인다.
   이 경로는 확실하지 않으므로 `certain=False`로 표시해 사람에게 넘긴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from neo4j import Driver

from .kcd import matches
from .models import Claim, Evidence

_ALL_ARTICLE_TEXTS = """
MATCH (v:Version {effective_from: $version})<-[:IN_VERSION]-(a:Article)
RETURN a.text AS text
"""

_ENUMERATE = """
MATCH (v:Version {effective_from: $version})<-[:IN_VERSION]-(a:Article:Exclusion)
MATCH (a)-[:OF_PRODUCT]->(p:Product {name: $product})
MATCH (a)-[:HAS_ITEM]->(i:Item)
RETURN i.uid AS node_uid, a.number AS number, a.title AS title,
       i.text AS text, i.coverage AS coverage
"""

QUOTE_CHARS = 180
MIN_KEYWORD_LEN = 2
# 약관 조문의 이 비율을 넘게 나오는 낱말은 변별력이 없다.
#
# 조문 452개로 실측한 값에서 두 무리가 갈린다.
#   변별어  간병 0.22% · 치과치료 0.88% · 보조기/영양제/비만 1.11% · 유산 1.55%
#   일반어  진료 1.77% · 치료 3.98% · 통원 4.20% · 진단 4.65% · 비급여 5.75%
# 그 사이를 문턱으로 잡았다. 다만 두 무리가 0.2%p밖에 안 벌어져 있어
# 이 규칙은 약하다 — 형태소 분석이나 손으로 만든 도메인 불용어가 있어야
# 제대로 갈린다. 지금은 확실하지 않은 히트를 사람에게 넘기는 용도라
# 이 정도로 둔다.
MAX_DOCUMENT_FREQUENCY = 0.016
# 조항 앞머리의 상품·조문 이름은 표현 일치에서 빼야 한다 — 아무 청구에나 걸린다.
_PREFIX_RE = re.compile(r"^.*?제\d+조\([^)]*\)\s*")
_TOKEN_RE = re.compile(r"[가-힣]{2,}")


@dataclass(frozen=True)
class ExclusionHit:
    evidence: Evidence
    reason: str
    certain: bool
    matched_codes: tuple[str, ...] = ()


def enumerate_exclusions(driver: Driver, product: str, version: str) -> list[dict[str, str]]:
    """그 상품·그 버전의 면책 사유를 전부. 유사도를 쓰지 않는다."""
    with driver.session() as session:
        return [dict(record) for record in session.run(
            _ENUMERATE, product=product, version=version
        )]


def screen(driver: Driver, claim: Claim, version: str) -> tuple[list[ExclusionHit], int]:
    """열거한 뒤 걸러낸다. (걸린 것, 전체 검토 수)를 돌려준다."""
    candidates = enumerate_exclusions(driver, claim.product, version)
    haystack = f"{claim.narrative} {claim.procedure or ''}"
    distinctive = _distinctive_tokens(driver, version)

    hits: list[ExclusionHit] = []
    for candidate in candidates:
        text = candidate["text"]
        evidence = Evidence(
            node_uid=candidate["node_uid"],
            product=claim.product,
            article_number=candidate["number"],
            article_title=candidate["title"],
            quote=_quote(text),
            role="exclusion",
        )

        matched = matches(text, claim.diagnosis_codes)
        if matched:
            hits.append(
                ExclusionHit(
                    evidence=evidence,
                    reason=f"진단코드 {', '.join(matched)}가 약관이 정한 면책 범위에 든다",
                    certain=True,
                    matched_codes=matched,
                )
            )
            continue

        keyword = _distinctive_overlap(text, haystack, distinctive)
        if keyword:
            hits.append(
                ExclusionHit(
                    evidence=evidence,
                    reason=f"청구 내용에 '{keyword}'가 나타난다 — 사람 확인 필요",
                    certain=False,
                )
            )
    return hits, len(candidates)


@lru_cache(maxsize=8)
def _distinctive_tokens_for(version: str, corpus: tuple[str, ...]) -> frozenset[str]:
    frequency: dict[str, int] = {}
    for document in corpus:
        for token in set(_TOKEN_RE.findall(document)):
            frequency[token] = frequency.get(token, 0) + 1

    limit = max(1, int(len(corpus) * MAX_DOCUMENT_FREQUENCY))
    return frozenset(token for token, count in frequency.items() if count <= limit)


def _distinctive_tokens(driver: Driver, version: str) -> frozenset[str]:
    """약관 전체를 기준으로 변별력 있는 낱말만 남긴다.

    버전마다 한 번만 센다 — 조문 450개를 매 청구마다 훑을 이유가 없다.
    """
    with driver.session() as session:
        records = session.run(_ALL_ARTICLE_TEXTS, version=version)
        corpus = tuple(record["text"] for record in records)
    return _distinctive_tokens_for(version, corpus)


def _quote(text: str) -> str:
    return _PREFIX_RE.sub("", text).strip()[:QUOTE_CHARS]


def _distinctive_overlap(clause: str, haystack: str, distinctive: frozenset[str]) -> str | None:
    """가장 긴 것부터 본다 — 긴 낱말일수록 우연히 겹칠 일이 적다."""
    body = _PREFIX_RE.sub("", clause)
    tokens = sorted(set(_TOKEN_RE.findall(body)), key=len, reverse=True)
    for token in tokens:
        if len(token) < MIN_KEYWORD_LEN or token not in distinctive:
            continue
        if token in haystack:
            return token
    return None
