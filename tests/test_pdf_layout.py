"""PDF 레이아웃 파서 테스트.

실제 PDF를 열지 않는다. 492쪽짜리 원본은 리포에 담지 않고(`docs/data-sources.md`),
여기서 고정해야 할 것은 **좌표를 어떻게 읽느냐**지 pdfplumber가 동작하느냐가
아니다. 그래서 페이지 객체를 흉내 낸 대역을 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass

from clausegraph.law.models import Article, Paragraph, TermsDocument
from clausegraph.law.pdf.acquire import find_standard_terms, parse_byeolpyo_list
from clausegraph.law.pdf.compare import body_text, compare, depth, first_difference
from clausegraph.law.pdf.layout import Line, _page_lines
from clausegraph.law.pdf.reconstruct import locate_pages, parse_pdf_terms, reconstruct
from clausegraph.law.pdf.tables import PdfRow, join_cell, stitch
from clausegraph.law.table_parser import Lexicon

PAGE_WIDTH = 595.0
PAGE_HEIGHT = 841.0


@dataclass
class FakeTable:
    bbox: tuple[float, float, float, float]


class FakePage:
    """pdfplumber 페이지 중 레이아웃 파서가 쓰는 부분만."""

    def __init__(self, page_number, rows, *, rects=(), tables=(), lines=()):
        self.page_number = page_number
        self.width = PAGE_WIDTH
        self.height = PAGE_HEIGHT
        self.rects = list(rects)
        self.lines = list(lines)
        self._rows = rows
        self._tables = list(tables)

    def extract_text_lines(self, layout=False):  # noqa: ARG002 - 서명만 맞춘다
        return [
            {"text": text, "x0": 60.0, "x1": 500.0, "top": top, "bottom": top + 14}
            for text, top in self._rows
        ]

    def find_tables(self):
        return self._tables


def _filled(x0, top, x1, bottom):
    return {"x0": x0, "top": top, "x1": x1, "bottom": bottom, "fill": True, "stroke": False}


def _vertical(x, top, bottom):
    return {"x0": x, "x1": x, "top": top, "bottom": bottom}


def test_footer_becomes_page_number_and_leaves_the_flow() -> None:
    page = FakePage(12, [("제1조(목적) 이 계약은 위험을 보장합니다.", 100.0), ("- 12 -", 800.0)])

    lines, printed = _page_lines(page, detect_tables=False)

    assert printed == 12
    assert [line.text for line in lines] == ["제1조(목적) 이 계약은 위험을 보장합니다."]
    assert lines[0].printed_page == 12
    assert lines[0].page_index == 11


def test_footer_shaped_line_in_the_middle_stays() -> None:
    # 본문 한가운데의 `- 3 -`은 꼬리말이 아니다.
    page = FakePage(4, [("- 3 -", 300.0), ("- 4 -", 800.0)])

    lines, printed = _page_lines(page, detect_tables=False)

    assert printed == 4
    assert [line.text for line in lines] == ["- 3 -"]


def test_lines_inside_a_ruled_table_are_marked() -> None:
    page = FakePage(
        5,
        [("보장종목 보상하지 않는 사항", 200.0), ("제5조(용어) 이 계약에서", 500.0)],
        tables=[FakeTable((57.0, 190.0, 537.0, 260.0))],
    )

    lines, _ = _page_lines(page, detect_tables=True)

    assert [line.in_table for line in lines] == [True, False]


def test_filled_box_counts_as_a_table_region() -> None:
    # 용어 설명 상자는 괘선이 아니라 칠한 사각형이다.
    page = FakePage(
        6,
        [("【전문금융소비자】보험계약에 관한 전문성", 520.0)],
        rects=[_filled(59.0, 502.0, 536.0, 654.0)],
    )

    lines, _ = _page_lines(page, detect_tables=True)

    assert lines[0].in_table


def test_small_or_full_page_rects_are_not_boxes() -> None:
    page = FakePage(
        7,
        [("본문입니다", 400.0)],
        rects=[
            _filled(60.0, 398.0, 90.0, 404.0),  # 글자 하나 음영
            _filled(0.0, 0.0, PAGE_WIDTH, PAGE_HEIGHT),  # 쪽 전체 배경
        ],
    )

    lines, _ = _page_lines(page, detect_tables=True)

    assert not lines[0].in_table


def test_two_verticals_sharing_a_span_make_a_box() -> None:
    # 글줄마다 밑줄이 깔린 설명 상자 — 가로선은 많고 세로 칸막이는 없다.
    page = FakePage(
        8,
        [("[직업] 생계유지 등을 위하여", 120.0)],
        lines=[_vertical(159.0, 97.0, 242.0), _vertical(451.0, 97.0, 242.0)],
    )

    lines, _ = _page_lines(page, detect_tables=True)

    assert lines[0].in_table


def test_a_lone_vertical_is_not_a_box() -> None:
    page = FakePage(
        9,
        [("본문입니다", 120.0)],
        lines=[_vertical(159.0, 97.0, 242.0)],
    )

    lines, _ = _page_lines(page, detect_tables=True)

    assert not lines[0].in_table


def test_article_title_wrapped_across_lines_is_rejoined() -> None:
    # PDF에서는 긴 제목이 접힌다. 그 줄에서 괄호가 닫히지 않는다.
    lines = [
        Line(0, 89, "□ 손해보험", 60, 500, 60),
        Line(0, 89, "제11조(음주운전, 무면허운전 또는 사고발생 시의 조치의무", 60, 500, 80),
        Line(0, 89, "위반 관련 사고부담금) ① 피보험자 본인이", 60, 500, 100),
    ]

    doc, _ = parse_pdf_terms(lines, "20260910", 1)

    assert doc.articles[0].number == "11"
    assert doc.articles[0].title.endswith("조치의무 위반 관련 사고부담금")


def test_reconstruct_marks_table_lines_with_a_box_character() -> None:
    lines = [
        Line(0, 1, "제5조(보상하지 않는 사항) 다음과 같습니다.", 60, 500, 100),
        Line(0, 1, "1. 고의로 자신을 해친 경우", 60, 500, 120, in_table=True),
    ]

    built = reconstruct(lines)

    assert built.text.split("\n")[1].startswith("│")
    assert built.pages == (1, 1)


def test_table_line_does_not_become_an_item() -> None:
    lines = [
        Line(0, 3, "□ 기본형 실손의료보험(급여 실손의료비)", 60, 500, 80),
        Line(0, 3, "제4조(보상하지 않는 사항) 보장종목별로 다음과 같습니다.", 60, 500, 100),
        Line(0, 3, "1. 고의로 자신을 해친 경우", 60, 500, 120, in_table=True),
    ]

    doc, _ = parse_pdf_terms(lines, "20260910", 1)

    article = doc.articles[0]
    assert sum(len(paragraph.items) for paragraph in article.paragraphs) == 0


def test_locate_pages_maps_each_article_to_its_printed_page() -> None:
    lines = [
        Line(0, 55, "□ 손해보험", 60, 500, 80),
        Line(0, 55, "제1조(목적) 이 계약은 위험을 보장합니다.", 60, 500, 100),
        Line(1, 56, "제2조(용어의 정의) 다음과 같습니다.", 60, 500, 100),
    ]

    doc, built = parse_pdf_terms(lines, "20260910", 1)

    assert locate_pages(doc, built) == {"손해보험/제1조": 55, "손해보험/제2조": 56}


def _article(number: str, title: str, text: str) -> Article:
    return Article(
        section="생명보험",
        number=number,
        title=title,
        text=text,
        paragraphs=(Paragraph(number=1, text=text),),
    )


def _doc(*articles: Article) -> TermsDocument:
    return TermsDocument(
        effective_on="20260910", admrul_seq=1, articles=articles, sections=("생명보험",)
    )


def test_body_text_drops_table_lines_and_whitespace() -> None:
    article = _article("5", "면책", "본문 첫 줄\n│표 안의 줄│\n둘째 줄")

    assert body_text(article) == "본문첫줄둘째줄"


def test_compare_counts_shared_articles_and_body_agreement() -> None:
    # 줄바꿈 자리가 달라도 글자가 같으면 일치로 본다.
    xml = _doc(_article("1", "목적", "이 계약은\n위험을 보장합니다."))
    pdf = _doc(_article("1", "목적", "이 계약은 위험을\n보장합니다."))

    report = compare(xml, pdf, pages={"생명보험/제1조": 3})

    assert report.recall == 1.0
    assert report.body_agreement == 1.0
    assert report.pages_found == 1


def test_compare_reports_a_real_character_difference() -> None:
    xml = _doc(_article("1", "목적", "? 회사는 보험금을 지급합니다."))
    pdf = _doc(_article("1", "목적", "➄ 회사는 보험금을 지급합니다."))

    report = compare(xml, pdf, pages={})

    assert len(report.body_mismatches) == 1
    assert "XML:'?" in first_difference(
        *[
            report.body_mismatches[0].xml_value,
            report.body_mismatches[0].pdf_value,
        ]
    )


def test_depth_counts_paragraphs_items_and_subitems() -> None:
    assert depth(_article("1", "목적", "본문")) == (1, 0, 0)


BYEOLPYO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<admRulBylSearch>
<admrulbyl id="1">
<별표일련번호>3295735</별표일련번호>
<현행연혁행정규칙일련번호>2200000108939</현행연혁행정규칙일련번호>
<별표명><![CDATA[등록사항 변경 신고서]]></별표명>
<별표번호>001500</별표번호>
<별표서식파일링크>/LSW/flDownload.do?flSeq=168886253</별표서식파일링크>
</admrulbyl>
<admrulbyl id="2">
<별표일련번호>3295613</별표일련번호>
<현행연혁행정규칙일련번호>2200000108939</현행연혁행정규칙일련번호>
<별표명><![CDATA[표준약관(제5-13조제1항관련)]]></별표명>
<별표번호>001500</별표번호>
<별표서식파일링크>/LSW/flDownload.do?flSeq=168884315</별표서식파일링크>
</admrulbyl>
</admRulBylSearch>
"""


def test_finds_the_standard_terms_not_the_form_with_the_same_number() -> None:
    # 별표번호 001500이 두 건이다. 번호만 보고 첫 건을 집으면 신고서를 받는다.
    refs = parse_byeolpyo_list(BYEOLPYO_XML)

    assert len(refs) == 2
    chosen = find_standard_terms(refs)
    assert chosen.seq == 3295613
    assert chosen.url.endswith("flSeq=168884315")


def _row(page, *cells):
    return PdfRow(printed_page=page, cells=cells)


def test_stitch_joins_rows_that_continue_on_the_next_page() -> None:
    # 2열 표: 보장종목이 빈 행은 앞 행의 이어짐이다.
    rows = [
        _row(213, "보장종목", "보상하지 않는 사항"),
        _row(213, "(1)상해급여", "① 회사는 다음의 사유로 생긴 의료비는 보상하지 않습니다."),
        _row(214, "보장종목", "보상하지 않는 사항"),
        _row(214, "", "1. 전문등반"),
        _row(214, "(2)질병급여", "① 회사는"),
    ]

    stitched = stitch(rows, Lexicon("보상하지 않습니다. 1. 전문등반"))

    assert len(stitched) == 2
    assert stitched[0].cells[0] == "(1)상해급여"
    assert stitched[0].cells[-1].endswith("1. 전문등반")
    assert stitched[1].cells[0] == "(2)질병급여"


def test_join_cell_uses_the_lexicon_for_wrapped_words() -> None:
    lexicon = Lexicon("(1)상해급여 보험계약 대출이율")

    assert join_cell("(1)\n상해급여", lexicon) == "(1)상해급여"
    assert join_cell("보험계약\n대출이율", lexicon) == "보험계약 대출이율"


def test_stitch_folds_label_only_rows_into_the_row_above() -> None:
    # 내용 칸이 세로 병합이면 아래 행은 이름만 남는다.
    rows = [
        _row(316, "(1)상해의료비", "해외", "① 회사는 보상하지 않습니다."),
        _row(316, "", "국내(급여)", ""),
    ]

    stitched = stitch(rows, Lexicon(""))

    assert len(stitched) == 1
    assert stitched[0].cells[1] == "해외 국내(급여)"
