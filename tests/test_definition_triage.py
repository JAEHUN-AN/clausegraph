"""용어 색인과 트리아지.

막고 싶은 사고는 하나다 — **없는 정의를 있다고 말하는 것.** 그러면
트리아지가 "그 조문을 보라"고 하는데 가 보면 아무것도 없다. 이 저장소가
`grounding_gate`로 막아 온 것과 같은 모양의 실패이고, 다만 여기서는
가드레일이 아니라 색인의 정확도가 막아야 한다.
"""

from __future__ import annotations

from clausegraph.agents.definition_terms import TermIndex, terms_from_articles
from clausegraph.agents.definition_triage import Need, extract_term, triage


def article(title: str, text: str) -> dict:
    return {"title": title, "text": text, "number": "2"}


LIST_ARTICLE = article(
    "용어의 정의",
    "가. 계약자: 회사와 계약을 체결하는 사람을 말합니다.\n"
    "나. 장해: <부표 3> 장해분류표에서 정한 기준에 따른 장해상태를 말합니다.\n"
    "다. 재해: <부표 4> 재해분류표에서 정한 재해를 말합니다.\n",
)

# 실손 특별약관 제2조가 이 모양이다 — 한 용어가 여러 줄에 걸쳐 적힌다.
TABLE_ARTICLE = article(
    "용어의 정의",
    "┏━━━━━━┯━━━━━━┓\n"
    "┃용 어  │정  의 ┃\n"
    "┣━━━━━━┿━━━━━━┫\n"
    "┃입원의료비│「국민건강보험법」에서  ┃\n"
    "┃      │정한 요양급여 중 …    ┃\n"
    "┃3대   │「근골격계 이학요법     ┃\n"
    "┃비급여│치료」 …              ┃\n",
)


# --- 색인 -------------------------------------------------------------------


def test_list_definitions_are_collected() -> None:
    index = terms_from_articles([LIST_ARTICLE])

    assert "계약자" in index.defined
    assert "장해" in index.defined


def test_table_definitions_are_collected() -> None:
    index = terms_from_articles([TABLE_ARTICLE])

    assert "입원의료비" in index.defined


def test_table_fragments_are_not_terms() -> None:
    """표는 한 용어를 여러 줄에 걸쳐 적는다.

    줄마다 첫 칸을 주우면 `비급여`처럼 잘린 조각이 용어로 앉는다. 조각
    하나가 색인에 들어가면 `입원비`가 `비`에 걸려 "정의가 있다"가 된다.
    """
    index = terms_from_articles([TABLE_ARTICLE])

    assert "용 어" not in index.defined
    assert all(len(term) >= 2 for term in index.defined)
    assert not any("┃" in term or "━" in term for term in index.defined)


def test_knows_is_exact() -> None:
    # 부분 일치를 허용하면 `입원비`가 `입원의료비`에 걸린다. 둘은 다른
    # 용어이고, 섞으면 상품 약관을 봐야 할 건을 표준약관으로 답하게 된다.
    index = terms_from_articles([TABLE_ARTICLE])

    assert index.knows("입원의료비")
    assert not index.knows("입원비")
    assert not index.knows("입원")


def test_definition_pointing_elsewhere_is_hollow() -> None:
    # 조문은 있지만 기준은 <부표 3>에 있고, 그 표는 수집본에 없다.
    index = terms_from_articles([LIST_ARTICLE])

    assert index.knows("장해")
    assert index.is_hollow("장해")
    assert not index.is_hollow("계약자")


def test_article_deferring_everything_is_recorded() -> None:
    # "용어의 뜻은 <붙임1>과 같습니다" — 정의 조문의 탈을 쓴 포인터다.
    index = terms_from_articles(
        [article("용어의 정의", "이 약관에서 사용하는 용어의 뜻은 <붙임1>과 같습니다.")]
    )

    assert index.defined == frozenset()
    assert "붙임1" in index.deferred_to


# --- 용어 뽑기 ---------------------------------------------------------------


def test_quoted_term_wins() -> None:
    issue = '피보험자가 받은 케모포트삽입술이 본건 보험약관에서 정하는 "수술"에 해당되는지 여부'

    assert extract_term(issue) == "수술"


def test_known_term_is_found_without_quotes() -> None:
    assert extract_term("IPL시술이 약관이 정하는 수술의 정의와 범위에 포함되는지 여부") == "수술"


def test_unknown_issue_yields_nothing() -> None:
    # 못 가리면 빈 문자열이다. 지어내면 엉뚱한 용어의 정의를 조회한다.
    assert extract_term("오늘 날씨는 어떻습니까") == ""


# --- 트리아지 ---------------------------------------------------------------


def test_missing_definition_asks_for_the_product_terms() -> None:
    index = terms_from_articles([LIST_ARTICLE])

    result = triage('약관에서 정한 "수술"에 해당하는지 여부', index)

    assert result.term == "수술"
    assert not result.defined_in_standard_terms
    assert Need.TERMS in result.needs


def test_hollow_definition_asks_for_the_table_not_the_article() -> None:
    """가장 조심한 자리다.

    `장해`는 표준약관이 정의한다. 그래서 "그 조문을 보라"고 답하기 쉬운데,
    그 정의는 <부표 3>을 가리키고 그 표가 없다. 빈 곳으로 보내게 된다.
    """
    index = terms_from_articles([LIST_ARTICLE])

    result = triage("장해로 판단 가능한지 여부", index)

    assert result.defined_in_standard_terms
    assert result.hollow
    assert Need.TABLE in result.needs
    assert Need.TERMS not in result.needs


def test_fact_bound_issue_asks_for_records() -> None:
    index = terms_from_articles([LIST_ARTICLE])

    result = triage("암의 치료를 직접적인 목적으로 입원한 경우에 해당하는지 여부", index)

    assert Need.RECORDS in result.needs


def test_precedent_is_never_predicted() -> None:
    """판례는 쟁점 문장만으로 알 수 없다.

    넷 중 셋만 답하고 하나를 비워 두는 쪽이, 넷을 다 답하고 하나를
    지어내는 쪽보다 낫다.
    """
    index = terms_from_articles([LIST_ARTICLE])

    for issue in (
        '약관에서 정한 "수술"에 해당하는지',
        "장해로 판단 가능한지 여부",
        "암의 치료를 직접적인 목적으로 입원한 경우인지",
    ):
        assert Need.PRECEDENT not in triage(issue, index).needs


def test_triage_never_decides() -> None:
    # 판정을 내지 않는다는 것이 이 도구의 약속이다.
    index = terms_from_articles([LIST_ARTICLE])

    text = triage('약관에서 정한 "수술"에 해당하는지 여부', index).render()

    assert "판단하지 않는다" in text
    assert "해당함" not in text
    assert "해당하지 않음" not in text


def test_empty_index_does_not_claim_knowledge() -> None:
    result = triage('약관에서 정한 "수술"에 해당하는지', TermIndex())

    assert not result.defined_in_standard_terms
    assert Need.TERMS in result.needs
