"""MCP 서버 — DB 없이 확인할 수 있는 부분."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from clausegraph.mcp_server.server import _parse_date, _shorten, mcp

EXPECTED_TOOLS = {
    "list_products",
    "resolve_terms_version",
    "list_exclusions",
    "check_diagnosis_codes",
    "search_clauses",
    "screen_exclusions",
    "adjudicate_claim",
}


@pytest.fixture(scope="module")
def tools():
    return {tool.name: tool for tool in asyncio.run(mcp.list_tools())}


def test_all_tools_are_registered(tools) -> None:
    assert set(tools) == EXPECTED_TOOLS


def test_every_tool_has_a_description(tools) -> None:
    # 설명이 없으면 모델이 도구를 고르지 못한다.
    assert all(tool.description and tool.description.strip() for tool in tools.values())


def test_tools_taking_an_enrollment_date_say_why_it_matters(tools) -> None:
    # 가입일을 받는 도구는 시점이 왜 중요한지 설명해야 모델이 되묻는다.
    for name in ("resolve_terms_version", "list_exclusions", "check_diagnosis_codes"):
        assert "enrolled_on" in tools[name].input_schema["properties"]


def test_every_tool_taking_a_date_also_takes_a_product() -> None:
    # 부칙이 정한 약관 적용일은 상품마다 다르다. 상품 없이 버전을 정하면
    # 2026-05-06~06-06 가입자에게 실손이 아닌 상품까지 새 약관을 한 달
    # 일찍 들이댄다 — 가입일x상품 1,472쌍 중 32쌍(2.2%)이 어긋났다.
    tool_map = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    for name in (
        "resolve_terms_version",
        "list_exclusions",
        "check_diagnosis_codes",
        "screen_exclusions",
        "adjudicate_claim",
    ):
        properties = tool_map[name].input_schema["properties"]
        assert "product" in properties, name


def test_version_resolution_is_the_one_the_agents_use() -> None:
    # 도구와 심사 에이전트가 서로 다른 버전을 고르면 같은 청구에 두 답이 나온다.
    import inspect

    from clausegraph.agents import coverage
    from clausegraph.mcp_server import server

    source = inspect.getsource(server._version_of)

    assert "resolve_version" in source
    assert server.resolve_version is coverage.resolve_version


def test_product_is_optional_only_where_the_answer_can_be_generic(tools) -> None:
    # resolve_terms_version만 상품 없이 부를 수 있다. 그때는 답이 상품에
    # 따라 달라질 수 있다는 것을 함께 말해야 한다.
    required = set(tools["resolve_terms_version"].input_schema.get("required", []))

    assert "product" not in required
    assert "상품마다 다르다" in tools["resolve_terms_version"].description


def test_exclusion_tool_says_it_enumerates(tools) -> None:
    # 골라 주는 도구로 오해하면 모델이 결과를 잘라 읽는다.
    assert "전부" in tools["list_exclusions"].description


def test_similarity_search_warns_against_using_it_for_exclusions(tools) -> None:
    assert "면책" in tools["search_clauses"].description


def test_adjudication_tool_says_the_decision_is_advisory(tools) -> None:
    assert "보조" in tools["adjudicate_claim"].description


def test_server_instructions_tell_the_model_to_ask_for_the_date() -> None:
    assert "가입일" in (mcp.instructions or "")


# --- 입력 처리 ---


def test_iso_date_is_parsed() -> None:
    assert _parse_date("2026-07-01") == date(2026, 7, 1)


def test_surrounding_space_is_tolerated() -> None:
    assert _parse_date("  2026-07-01 ") == date(2026, 7, 1)


# 20260701은 파이썬이 ISO 기본형으로 받아 준다 — 굳이 막지 않는다.
@pytest.mark.parametrize("value", ["작년", "2026/07/01", "", "2026-02-31", "2026-13-01"])
def test_unparseable_date_returns_none_instead_of_guessing(value: str) -> None:
    assert _parse_date(value) is None


def test_quote_is_flattened_and_capped() -> None:
    result = _shorten("첫 줄입니다.\n  둘째   줄입니다.\n" + "가" * 300)

    assert "\n" not in result
    assert result.endswith("…")
    assert len(result) <= 121


def test_short_quote_is_left_alone() -> None:
    assert _shorten("비만(E66)") == "비만(E66)"
