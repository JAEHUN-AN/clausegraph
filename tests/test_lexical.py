"""어휘 검색 토큰화와 순위 융합 테스트.

DB가 필요한 경로(`search_lexical`)는 CI에서 돌지 않는다. 대신 그 경로가
의존하는 규칙 — 어간 토큰화, 질의 조립, 문서빈도 필터, RRF — 을 고정한다.
"""

from __future__ import annotations

from clausegraph.agents.exclusion import stemmed_tokens
from clausegraph.rag.lexical import lexemes, to_tsquery_or
from clausegraph.rag.retriever import Hit, fuse_rrf


def _hit(uid: str, score: float = 0.0, source: str = "vector") -> Hit:
    return Hit(
        node_uid=uid,
        node_kind="item",
        product="실손",
        article_number="4",
        article_title="보상하지 않는 사항",
        is_exclusion=True,
        content="본문",
        score=score,
        source=source,
    )


def test_stemmed_tokens_keeps_duplicates() -> None:
    # 집합으로 세면 빈도를 잃는다. 어휘 점수는 빈도를 본다.
    assert stemmed_tokens("치료를 받고 치료를 마쳤다").count("치료") == 2


def test_lexemes_strips_particles_so_index_and_query_agree() -> None:
    # notes/025 — '대상'과 '대상에'가 다른 말이면 어휘 검색이 어절에 걸린다.
    assert lexemes("지급 대상에 해당") == lexemes("지급 대상 해당")


def test_short_stems_are_not_over_trimmed() -> None:
    # '치과'가 '치'가 되면 아무 데나 걸린다.
    assert "치과" in stemmed_tokens("치과 치료")


def test_tsquery_joins_with_or_not_and() -> None:
    # AND로 이으면 한 낱말만 어긋나도 0건이 된다.
    query = to_tsquery_or("간병비 증명서")

    assert " | " in query
    assert "&" not in query


def test_tsquery_filter_drops_tokens_that_fail_the_predicate() -> None:
    query = to_tsquery_or("간병비 보험금", keep=lambda token: token == "간병비")

    assert query == "간병비"


def test_tsquery_prefix_marks_long_stems_only() -> None:
    # 한 글자 앞자리는 아무 데나 걸린다.
    query = to_tsquery_or("오토바이 가", prefix=True)

    assert "오토바:*" in query
    assert "가:*" not in query


def test_tsquery_is_empty_when_nothing_survives() -> None:
    # 빈 질의를 그대로 to_tsquery에 넘기면 예외가 난다. 호출부가 막는다.
    assert to_tsquery_or("", ) == ""


def test_rrf_ranks_agreement_above_either_ranking_alone() -> None:
    # 두 순위 모두에 있는 것이 한쪽에서만 1위인 것을 이긴다.
    both = _hit("both")
    vector_only = _hit("vector-only")
    lexical_only = _hit("lexical-only", source="lexical")

    fused = fuse_rrf([[vector_only, both], [lexical_only, both]])

    assert fused[0].node_uid == "both"


def test_rrf_does_not_duplicate_a_chunk_seen_in_both_rankings() -> None:
    hit = _hit("same")

    fused = fuse_rrf([[hit], [hit]])

    assert [item.node_uid for item in fused] == ["same"]


def test_rrf_respects_the_limit() -> None:
    ranking = [_hit(f"n{index}") for index in range(5)]

    assert len(fuse_rrf([ranking], limit=2)) == 2


def test_rrf_ignores_score_scale() -> None:
    """점수 단위가 달라도 결과가 같아야 한다 — 순위만 쓰기 때문이다.

    코사인은 0~1이고 ts_rank_cd는 상한이 없다. 이 성질이 깨지면 정규화
    가중치를 어디선가 정해야 하고, 그 값을 정할 근거가 이 프로젝트에 없다.
    """
    small = [_hit("a", 0.9), _hit("b", 0.8)]
    huge = [_hit("a", 900.0), _hit("b", 800.0)]

    assert [h.node_uid for h in fuse_rrf([small])] == [
        h.node_uid for h in fuse_rrf([huge])
    ]
