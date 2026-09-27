"""권한 스코프 판정 테스트.

DB가 필요한 경로(누수 전수 측정)는 CI에서 돌지 않는다. 대신 그 경로가
의존하는 규칙 — 빈 스코프의 뜻, 지목과 검색의 차이, 교집합 — 을 고정한다.
"""

from __future__ import annotations

import dataclasses

import pytest

from clausegraph.access import AccessDeniedError, Principal, visible

ALL = frozenset({"실손", "자동차", "생명"})


def _row(product: str) -> dict[str, str]:
    return {"product": product}


def test_empty_scope_sees_nothing_not_everything() -> None:
    """빈 집합은 '전체'가 아니라 '아무것도'다.

    비어 있음을 전체로 읽는 기본값은 조용한 권한 상승이다 — notes/027에서
    `0`을 '모른다'가 아니라 '받은 것이 없다'로 읽어 과다지급을 낸 것과
    같은 모양이다.
    """
    nobody = Principal(name="설정 안 함")

    assert not nobody.can_see("실손")
    assert nobody.allowed(ALL) == frozenset()


def test_unrestricted_must_be_explicit() -> None:
    assert Principal.everything("적재").can_see("무엇이든")


def test_named_product_outside_scope_is_refused_not_emptied() -> None:
    """지목한 상품은 거절한다. 빈 결과로 뭉개지 않는다.

    '조항이 없다'와 '볼 수 없다'는 다른 말이고, 앞의 말로 뭉개면 심사자가
    없는 약관을 찾아 헤맨다.
    """
    who = Principal(name="실손심사", products=frozenset({"실손"}))

    with pytest.raises(AccessDeniedError):
        who.require("자동차")


def test_named_product_inside_scope_passes() -> None:
    who = Principal(name="실손심사", products=frozenset({"실손"}))

    who.require("실손")  # 예외가 나지 않아야 한다


def test_searched_rows_are_filtered_quietly() -> None:
    """검색으로 걸려 나온 것은 거절하지 않고 뺀다.

    벡터 검색은 어느 상품이 나올지 호출부가 미리 모른다. '검색했는데
    권한 오류'는 쓸모가 없으므로 안 보이는 것은 없는 것으로 둔다.
    """
    who = Principal(name="실손심사", products=frozenset({"실손"}))
    rows = [_row("실손"), _row("자동차"), _row("생명")]

    kept = visible(who, rows, product_of=lambda row: row["product"])

    assert kept == [_row("실손")]


def test_allowed_intersects_with_what_actually_exists() -> None:
    """스코프에 없는 상품명이 있어도 질의로 흘러가지 않는다."""
    who = Principal(name="오타", products=frozenset({"실손", "없는상품"}))

    assert who.allowed(ALL) == frozenset({"실손"})


def test_unrestricted_allowed_is_the_whole_universe() -> None:
    assert Principal.everything().allowed(ALL) == ALL


def test_unrestricted_visible_keeps_everything() -> None:
    rows = [_row("실손"), _row("자동차")]

    assert visible(Principal.everything(), rows, product_of=lambda r: r["product"]) == rows


def test_principal_is_frozen() -> None:
    """스코프를 도중에 넓힐 수 없어야 한다."""
    who = Principal(name="실손심사", products=frozenset({"실손"}))

    with pytest.raises(dataclasses.FrozenInstanceError):
        who.products = ALL  # type: ignore[misc]


def test_every_data_path_requires_a_principal() -> None:
    """조항을 돌려주는 함수는 `principal`을 **키워드 필수**로 받아야 한다.

    notes/023의 재발 방지다 — 게이트를 한 곳에 걸고 "막았다"고 말하면
    나머지 경로가 조용히 샌다. 기본값이 있으면 넘기는 것을 잊어도 통과하고,
    잊었다는 것이 드러나지 않는다.

    경로를 새로 더하면 이 목록에도 더해야 한다. 목록에 더하는 것을 잊으면
    이 테스트가 못 잡지만, 적어도 **있는 경로가 기본값을 갖는 것**은 막는다.
    """
    import inspect

    from clausegraph.agents.coverage import (
        article_scoped_notes,
        find_coverage,
        resolve_version,
    )
    from clausegraph.agents.exclusion import enumerate_exclusions, screen
    from clausegraph.agents.orchestrator import adjudicate
    from clausegraph.rag.lexical import search_lexical
    from clausegraph.rag.retriever import search_graph, search_hybrid, search_vector

    paths = (
        find_coverage, resolve_version, article_scoped_notes,
        enumerate_exclusions, screen, adjudicate,
        search_vector, search_graph, search_hybrid, search_lexical,
    )
    for path in paths:
        parameter = inspect.signature(path).parameters.get("principal")
        assert parameter is not None, f"{path.__name__}에 principal이 없다"
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, (
            f"{path.__name__}의 principal이 키워드 전용이 아니다"
        )
        assert parameter.default is inspect.Parameter.empty, (
            f"{path.__name__}의 principal에 기본값이 있다 — 잊어도 통과한다"
        )


def test_every_mcp_tool_is_guarded() -> None:
    """MCP 도구는 전부 `guarded`를 거쳐야 한다.

    권한 거절이 스택트레이스로 나가면 호출한 쪽이 그것을 사용자에게
    그대로 보여 준다. 도구를 새로 더하고 이 데코레이터를 빠뜨리는 것을
    막는다.
    """
    from pathlib import Path

    source = Path("src/clausegraph/mcp_server/server.py").read_text(encoding="utf-8")

    assert source.count("@mcp.tool()") == source.count("@guarded")
    assert "@mcp.tool()\n@guarded\n" in source
