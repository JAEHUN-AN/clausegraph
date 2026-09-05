"""표준약관 조문 파서 테스트.

문구와 배치는 실제 별표15에서 가져왔다. 특히 상품 구분은 이 파서가 가장
틀리기 쉬운 지점이라 함정을 그대로 고정해 둔다.
"""

from __future__ import annotations

from clausegraph.law.terms_parser import extract_revisions, parse_terms

EFFECTIVE_ON = "20260910"
SEQ = 2200000108939


def _parse(text: str):
    return parse_terms(text, EFFECTIVE_ON, SEQ)


def test_parses_section_and_article() -> None:
    doc = _parse(
        "□ 생명보험 <개정 2005.2.15.>\n"
        "제1관  목적 및 용어의 정의\n"
        "제1조(목적) 이 보험계약은 위험을 보장하기 위하여 체결됩니다.\n"
    )

    assert doc.sections == ("생명보험",)
    assert len(doc.articles) == 1
    article = doc.articles[0]
    assert article.number == "1"
    assert article.title == "목적"
    assert article.chapter == "제1관  목적 및 용어의 정의"
    assert article.unit == "생명보험"


def test_article_numbers_restart_per_product() -> None:
    # 화재보험 제4조와 자동차보험 제4조는 다른 조문이다.
    doc = _parse(
        "□ 손해보험\n"
        "<화재보험>\n"
        "제4조(보상하지 않는 손해) 회사는 아래의 사유로 인한 손해는 보상하지 않습니다.\n"
        "<자동차보험>\n"
        "제4조(보험금의 지급) 회사는 보험금을 지급합니다.\n"
    )

    keys = [article.key for article in doc.articles]
    assert keys == ["화재보험/제4조", "자동차보험/제4조"]


def test_example_wrapper_does_not_become_a_product() -> None:
    # <예 시>는 자동차보험 약관을 감싸는 래퍼다. 상품으로 읽으면 뒤따르는
    # 조문이 전부 '예 시' 밑으로 새어 나간다.
    doc = _parse(
        "□ 손해보험\n"
        "<자동차보험>\n"
        "<예 시>\n"
        "제5조(보상하지 않는 손해) 고의로 인한 손해는 보상하지 않습니다.\n"
    )

    assert doc.articles[0].unit == "자동차보험"


def test_attachment_markers_do_not_change_product() -> None:
    doc = _parse(
        "□ 생명보험\n"
        "<부표 3>\n"
        "제10조(보험료의 납입) 계약자는 보험료를 납입합니다.\n"
    )

    assert doc.articles[0].unit == "생명보험"


def test_product_marker_without_its_own_section_still_identifies_the_unit() -> None:
    # 목차상 배상책임보험은 손해보험 6번인데, 본문에서는 해외여행 실손 뒤에
    # □ 없이 나온다. section이 아니라 unit으로 소속을 잡아야 하는 이유다.
    doc = _parse(
        "□ 해외여행 실손의료보험 특별약관2(비중증 비급여 실손의료비)\n"
        "제1조(보장종목) 회사가 판매하는 특별약관2는 다음과 같습니다.\n"
        "<배상책임보험>\n"
        "제4조(보상하지 않는 손해) 회사는 아래 손해를 보상하지 않습니다.\n"
    )

    assert doc.articles[-1].unit == "배상책임보험"
    assert doc.articles[-1].section.startswith("해외여행")


def test_branch_article_number_is_kept() -> None:
    doc = _parse("□ 생명보험\n제3조의2(보험금 지급에 관한 세부규정) 세부 내용입니다.\n")

    assert doc.articles[0].number == "3의2"


def test_parses_paragraphs_items_and_subitems() -> None:
    doc = _parse(
        "□ 생명보험\n"
        "제5조(보험금을 지급하지 않는 사유) 회사는 다음의 경우 보험금을 지급하지 않습니다.\n"
        "  1. 피보험자가 고의로 자신을 해친 경우\n"
        "   가. 심신상실 상태에서 자신을 해친 경우\n"
        "  2. 보험수익자가 고의로 피보험자를 해친 경우\n"
    )

    article = doc.articles[0]
    assert len(article.paragraphs) == 1
    assert article.paragraphs[0].implicit is True
    items = article.paragraphs[0].items
    assert [item.number for item in items] == [1, 2]
    assert items[0].subitems[0].label == "가"


def test_explicit_paragraph_markers_split_the_article() -> None:
    doc = _parse(
        "□ 생명보험\n"
        "제7조(보험금의 청구) ① 보험수익자는 다음의 서류를 제출하여야 합니다.\n"
        "  1. 청구서\n"
        "② 회사는 서류를 접수합니다.\n"
    )

    numbers = [paragraph.number for paragraph in doc.articles[0].paragraphs]
    assert numbers == [1, 2]


def test_table_lines_are_not_read_as_structure() -> None:
    # 표 안의 '1.'은 호가 아니다 — 셀 안에서 줄이 잘려 있을 뿐이다.
    doc = _parse(
        "□ 기본형 실손의료보험(급여 실손의료비)\n"
        "제4조(보상하지 않는 사항) 보장종목별로 다음과 같습니다.\n"
        "┏━━━━┳━━━━━━━━┓\n"
        "┃보장종목┃  1. 고의로 자신을 해친 경우  ┃\n"
        "┗━━━━┻━━━━━━━━┛\n"
    )

    article = doc.articles[0]
    assert sum(len(paragraph.items) for paragraph in article.paragraphs) == 0
    assert "┃" in article.text  # 표는 원문 그대로 남긴다


def test_extracts_revision_dates_sorted_and_deduplicated() -> None:
    assert extract_revisions("<개정 2014.12.26., 2018.3.2.> ... <신설 2014.12.26.>") == (
        "2014-12-26",
        "2018-03-02",
    )


def test_article_without_revision_marker_has_no_dates() -> None:
    doc = _parse("□ 생명보험\n제1조(목적) 이 계약은 위험을 보장합니다.\n")

    assert doc.articles[0].revised_on == ()
