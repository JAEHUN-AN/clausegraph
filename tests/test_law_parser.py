"""국가법령정보 XML 파서 테스트.

픽스처는 실제 응답에서 잘라 왔다 — 본문 픽스처는 별표15 블록의 CDATA를
앞부분만 남긴 것이고, 태그 구조는 원본 그대로다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clausegraph.law.parser import (
    LawApiError,
    extract_standard_terms,
    parse_admrul_list,
    parse_total,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def list_xml() -> str:
    return (FIXTURES / "admrul_list_current.xml").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def body_xml() -> str:
    return (FIXTURES / "admrul_body_trimmed.xml").read_text(encoding="utf-8")


def test_parses_current_admrul_row(list_xml: str) -> None:
    refs = parse_admrul_list(list_xml)

    assert len(refs) == 1
    assert refs[0].name == "보험업감독업무시행세칙"
    assert refs[0].status == "현행"
    assert refs[0].seq > 0


def test_reads_total_count(list_xml: str) -> None:
    assert parse_total(list_xml) == 1


def test_extracts_standard_terms_metadata(body_xml: str) -> None:
    terms = extract_standard_terms(body_xml)

    assert terms.title.startswith("표준약관")
    assert len(terms.effective_on) == 8
    assert len(terms.promulgated_on) == 8
    assert terms.admrul_seq > 0


def test_cdata_chunks_are_joined_into_plain_text(body_xml: str) -> None:
    # 원문 한 줄이 CDATA 조각 하나다. 이어붙여야 읽을 수 있는 텍스트가 된다.
    terms = extract_standard_terms(body_xml)

    assert "표준약관" in terms.text
    assert "CDATA" not in terms.text
    assert terms.char_count > 0


def test_missing_byeolpyo_is_an_error(body_xml: str) -> None:
    with pytest.raises(LawApiError, match="0099"):
        extract_standard_terms(body_xml, byeolpyo_no="0099")


def test_not_subscribed_error_page_is_not_silently_accepted() -> None:
    page = "<html><body>미신청된 서비스입니다</body></html>"

    with pytest.raises(LawApiError, match="미신청"):
        parse_admrul_list(page)


def test_non_xml_response_raises() -> None:
    with pytest.raises(LawApiError):
        parse_total("<html>어떤 오류 페이지</html>")


# --- 버전 묶기 ---


def _revision(effective_on: str, digest: str, seq: int) -> dict[str, object]:
    return {
        "admrul_seq": seq,
        "effective_on": effective_on,
        "content_sha256": digest,
        "char_count": 100,
        "file": f"{effective_on}_{seq}.txt",
    }


def test_consecutive_identical_revisions_collapse_into_one_version() -> None:
    from clausegraph.law.collect import group_by_content

    # 세칙이 세 번 개정됐지만 별표15는 그대로인 경우.
    versions = group_by_content(
        [
            _revision("20260715", "aaa", 3),
            _revision("20260630", "aaa", 2),
            _revision("20260506", "aaa", 1),
        ]
    )

    assert len(versions) == 1
    assert versions[0]["effective_from"] == "20260506"
    assert versions[0]["admrul_seqs"] == [1, 2, 3]


def test_version_validity_ranges_are_chained() -> None:
    from clausegraph.law.collect import group_by_content

    versions = group_by_content(
        [
            _revision("20260910", "ccc", 3),
            _revision("20260506", "bbb", 2),
            _revision("20250401", "aaa", 1),
        ]
    )

    # 최신순으로 돌려주고, 가장 최근 버전의 종료일은 열려 있다.
    assert [v["effective_from"] for v in versions] == ["20260910", "20260506", "20250401"]
    assert versions[0]["effective_to"] is None
    assert versions[1]["effective_to"] == "20260910"
    assert versions[2]["effective_to"] == "20260506"


def test_same_content_returning_later_starts_a_new_version() -> None:
    from clausegraph.law.collect import group_by_content

    # 내용이 A -> B -> A로 돌아왔다면 구간이 셋이어야 한다.
    versions = group_by_content(
        [
            _revision("20260301", "aaa", 3),
            _revision("20251001", "bbb", 2),
            _revision("20250401", "aaa", 1),
        ]
    )

    assert len(versions) == 3
