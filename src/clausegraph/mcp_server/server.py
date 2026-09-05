"""clausegraph MCP 서버 — 약관 지식그래프와 지급심사를 LLM 도구로 노출한다.

실행(stdio): uv run --extra mcp --extra graph python -m clausegraph.mcp_server.server

도구 설계 원칙:

1. 설명에 "무엇을 하는지"가 아니라 **"언제 호출해야 하는지"** 를 쓴다.
2. 모든 결과에 **근거 조항**을 붙인다. 조항을 못 가리키는 답은 지급심사에서
   쓸 수 없다.
3. **면책은 열거해서 준다.** 닮은 것을 몇 개 골라 주면 절반을 놓친다
   (notes/008: 벡터 recall 32.4%). `list_exclusions`는 그 상품·그 시점의
   면책을 전부 돌려주고, 걸러내는 일은 호출한 쪽이 한다.
4. 가입일을 받는 도구는 **가입일이 없으면 답하지 않는다.** 같은 조문 번호가
   시점에 따라 다른 내용이므로, 모르는 채로 답하면 틀린 조항을 인용한다.
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date

from mcp.server.mcpserver import MCPServer
from neo4j import GraphDatabase

from ..agents.exclusion import enumerate_exclusions, screen
from ..agents.extract import extract_claim
from ..agents.kcd import matches
from ..agents.models import Adjudication
from ..agents.orchestrator import adjudicate
from ..agents.terminology import lookup
from ..graph.schema import OPEN_ENDED

logger = logging.getLogger(__name__)

MAX_ROWS = 40
QUOTE_CHARS = 120

mcp = MCPServer(
    name="clausegraph",
    version="0.1.0",
    instructions=(
        "보험 표준약관 지식그래프와 지급심사 보조. 가입 시점에 적용되던 약관을 "
        "되살려 보장·면책 조항을 조회한다. "
        "가입일을 모르면 먼저 물어야 한다 — 같은 조문 번호가 시점에 따라 "
        "다른 내용이다. 면책 여부는 list_exclusions로 전부 열거한 뒤 판단하고, "
        "진단코드가 있으면 check_diagnosis_codes로 결정론적으로 확인한다. "
        "판정은 보조이며 최종 결정은 사람이 한다."
    ),
)

_driver = None


def driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            os.environ["NEO4J_URI"],
            auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
        )
    return _driver


_PRODUCTS = """
MATCH (a:Article)-[:OF_PRODUCT]->(p:Product)
RETURN p.name AS product,
       min(a.effective_from) AS first_version,
       max(a.effective_from) AS last_version,
       count(DISTINCT a.effective_from) AS versions
ORDER BY product
"""

_VERSIONS = """
MATCH (v:Version)
RETURN v.effective_from AS effective_from, v.effective_to AS effective_to,
       v.article_count AS article_count
ORDER BY v.effective_from DESC
"""

_VERSION_AT = """
MATCH (v:Version)
WHERE v.effective_from <= $on_date AND $on_date < v.effective_to
RETURN v.effective_from AS effective_from, v.effective_to AS effective_to,
       v.article_count AS article_count
"""


@mcp.tool()
def list_products() -> str:
    """조회할 수 있는 약관 상품과 수집된 시행일자 범위를 반환한다.

    다른 도구를 부르기 전에 **어떤 상품·어느 시점을 다룰 수 있는지 모를 때**
    먼저 호출한다. 사용자의 가입일이 이 범위 밖이면 판정할 수 없음을 알려야
    한다 — 추측해서 답하면 안 된다.
    """
    with driver().session() as session:
        products = [dict(record) for record in session.run(_PRODUCTS)]
        versions = [dict(record) for record in session.run(_VERSIONS)]

    lines = [f"수집된 약관 버전 {len(versions)}개:"]
    for version in versions:
        end = "현재" if version["effective_to"] == OPEN_ENDED else version["effective_to"]
        lines.append(
            f"  {version['effective_from']} ~ {end}  조문 {version['article_count']}"
        )
    lines.append(f"약관 상품 {len(products)}개:")
    for product in products:
        lines.append(
            f"  {product['product']}  (버전 {product['versions']}개, "
            f"{product['first_version']}~{product['last_version']})"
        )
    return "\n".join(lines)


@mcp.tool()
def resolve_terms_version(enrolled_on: str) -> str:
    """가입일에 적용되던 약관 버전을 찾는다. `enrolled_on`은 YYYY-MM-DD.

    조항을 인용하기 **전에** 호출한다. 보험은 가입 당시 약관이 적용되므로,
    버전을 정하지 않고 조문을 읽으면 그 계약에 없는 조항을 들이댈 수 있다.
    2026-05-06 개편으로 실손 특별약관이 중증/비중증 둘로 갈렸고, 그 전에
    가입한 사람의 '실손 특별약관'은 지금 문서에 없는 상품이다.
    """
    parsed = _parse_date(enrolled_on)
    if parsed is None:
        return f"가입일 형식이 올바르지 않다: {enrolled_on!r} (YYYY-MM-DD)"

    with driver().session() as session:
        record = session.run(_VERSION_AT, on_date=parsed.strftime("%Y%m%d")).single()

    if record is None:
        return (
            f"가입일 {enrolled_on}에 적용되던 약관을 수집 범위에서 찾지 못했다. "
            "list_products로 수집 범위를 확인하고, 범위 밖이면 판정할 수 "
            "없음을 알려야 한다."
        )
    end = "현재" if record["effective_to"] == OPEN_ENDED else record["effective_to"]
    return (
        f"가입일 {enrolled_on} -> 적용 약관 {record['effective_from']} ~ {end} "
        f"(조문 {record['article_count']}개)"
    )


@mcp.tool()
def list_exclusions(product: str, enrolled_on: str) -> str:
    """그 상품·그 가입 시점의 면책 조항을 **전부** 반환한다.

    "이거 보상되나요"류 질문에 답할 때 호출한다. 면책은 보장을 무효로 만드는
    조건이라 하나만 놓쳐도 결론이 뒤집힌다. 그래서 이 도구는 닮은 것을 골라
    주지 않고 전부 돌려준다 — 걸러내는 판단은 호출한 쪽이 근거를 인용하며
    해야 한다.
    """
    parsed = _parse_date(enrolled_on)
    if parsed is None:
        return f"가입일 형식이 올바르지 않다: {enrolled_on!r} (YYYY-MM-DD)"

    version = _version_of(parsed)
    if version is None:
        return f"가입일 {enrolled_on}에 적용되던 약관을 찾지 못했다."

    rows = enumerate_exclusions(driver(), product, version)
    if not rows:
        return (
            f"{product}의 면책 조항을 찾지 못했다. "
            "상품명이 정확한지 list_products로 확인할 것."
        )

    lines = [f"{product} / 약관 {version} — 면책 사유 {len(rows)}건 (전부):"]
    for row in rows[:MAX_ROWS]:
        coverage = f"[{row['coverage']}] " if row.get("coverage") else ""
        lines.append(f"  제{row['number']}조 {coverage}{_shorten(row['text'])}")
        lines.append(f"    근거 {row['node_uid']}")
    if len(rows) > MAX_ROWS:
        lines.append(f"  ... {len(rows) - MAX_ROWS}건 더 있음 (전체 {len(rows)}건)")
    return "\n".join(lines)


@mcp.tool()
def check_diagnosis_codes(product: str, enrolled_on: str, codes: str) -> str:
    """진단코드가 면책 범위에 드는지 결정론적으로 확인한다.

    진단코드(KCD)를 알 때는 이 도구를 쓴다. 약관이 면책을 코드로 못박아
    두므로(`정신 및 행동장애(F04~F99)`) 낱말 유사도보다 정확하고, 왜 걸렸는지
    코드로 설명된다. `codes`는 쉼표로 구분한다: "F32, K08".

    코드를 모르면 먼저 코드로 옮겨야 한다 — '임플란트'나 '충치'는 약관에 없는
    낱말이라 그대로는 걸리지 않는다.
    """
    parsed = _parse_date(enrolled_on)
    if parsed is None:
        return f"가입일 형식이 올바르지 않다: {enrolled_on!r} (YYYY-MM-DD)"

    version = _version_of(parsed)
    if version is None:
        return f"가입일 {enrolled_on}에 적용되던 약관을 찾지 못했다."

    wanted = tuple(code.strip().upper() for code in codes.split(",") if code.strip())
    if not wanted:
        return "확인할 진단코드가 없다."

    rows = enumerate_exclusions(driver(), product, version)
    scanned = [(row, matches(row["text"], wanted)) for row in rows]
    hits = [(row, matched) for row, matched in scanned if matched]

    if not hits:
        return (
            f"{product} / 약관 {version}: 진단코드 {', '.join(wanted)}에 걸리는 "
            f"면책 조항이 없다 (면책 {len(rows)}건 전부 대조).\n"
            "다만 코드로 적히지 않은 면책(보조기·간병비·영양제 등)은 이 도구가 "
            "잡지 못한다. list_exclusions로 함께 확인할 것."
        )

    lines = [f"{product} / 약관 {version} — 걸리는 면책 {len(hits)}건:"]
    for row, matched in hits[:MAX_ROWS]:
        coverage = f"[{row['coverage']}] " if row.get("coverage") else ""
        lines.append(
            f"  {', '.join(matched)} -> 제{row['number']}조 "
            f"{coverage}{_shorten(row['text'])}"
        )
        lines.append(f"    근거 {row['node_uid']}")
    return "\n".join(lines)


@mcp.tool()
def search_clauses(query: str, limit: int = 8) -> str:
    """조문을 문장 유사도로 찾는다. 서술형 질문에 쓴다.

    "입원의 정의가 뭔가", "보험금 청구 서류가 뭔가"처럼 **조항의 내용을 묻는**
    질문에 호출한다.

    면책 여부를 가리는 데는 쓰지 말 것. 측정해 보니 이 방식은 지급을 뒤집는
    면책을 절반 놓친다(recall 32.4%, notes/008). 면책은 list_exclusions로
    열거해야 한다.
    """
    from ..rag.embed import get_embedder
    from ..rag.retriever import connect_pg, search_vector

    with connect_pg() as connection:
        hits = search_vector(connection, get_embedder(), query, k=min(limit, MAX_ROWS))

    if not hits:
        return "일치하는 조문이 없다."
    lines = [f"유사 조문 {len(hits)}건 (질의: {query}):"]
    for hit in hits:
        tag = "[면책] " if hit.is_exclusion else ""
        lines.append(
            f"  {hit.score:.3f} {tag}{hit.product} 제{hit.article_number}조"
            f"({hit.article_title})"
        )
        lines.append(f"    {_shorten(hit.content)}")
        lines.append(f"    근거 {hit.node_uid}")
    return "\n".join(lines)


@mcp.tool()
def screen_exclusions(product: str, enrolled_on: str, narrative: str) -> str:
    """청구 내용에 걸릴 수 있는 면책을 골라낸다. 확실/불확실을 나눠 준다.

    판정까지 가지 않고 **면책만 보고 싶을 때** 호출한다. 확실은 진단코드가
    약관 범위에 든 경우이고, 불확실은 표현이 겹친 경우다. 불확실은 근거로
    쓰지 말고 사람 확인이 필요하다고 전해야 한다.
    """
    parsed = _parse_date(enrolled_on)
    if parsed is None:
        return f"가입일 형식이 올바르지 않다: {enrolled_on!r} (YYYY-MM-DD)"

    version = _version_of(parsed)
    if version is None:
        return f"가입일 {enrolled_on}에 적용되던 약관을 찾지 못했다."

    claim = extract_claim("MCP", product, parsed, narrative, enrich=lookup)
    hits, considered = screen(driver(), claim, version)
    certain = [hit for hit in hits if hit.certain]
    uncertain = [hit for hit in hits if not hit.certain]

    lines = [
        f"{product} / 약관 {version} — 면책 {considered}건을 전부 검토",
        f"추출 진단코드: {', '.join(claim.diagnosis_codes) or '없음'}",
        f"확실 {len(certain)}건, 불확실 {len(uncertain)}건",
    ]
    for label, group in (("확실", certain), ("불확실 — 사람 확인 필요", uncertain)):
        for hit in group[:MAX_ROWS]:
            lines.append(f"  [{label}] {hit.reason}")
            lines.append(
                f"    제{hit.evidence.article_number}조 {_shorten(hit.evidence.quote)}"
            )
            lines.append(f"    근거 {hit.evidence.node_uid}")
    return "\n".join(lines)


@mcp.tool()
def adjudicate_claim(product: str, enrolled_on: str, narrative: str) -> str:
    """청구 한 건을 심사해 판정과 근거, 발동한 가드레일을 반환한다.

    청구 내용이 자연어로 들어왔을 때 호출한다. 사실추출 -> 보장탐색 ->
    면책검증 -> 금액산정 -> 검증 순으로 돌고, 근거 조항을 특정하지 못하면
    결론을 내지 않는다.

    **판정은 보조다.** `HUMAN_REVIEW`나 `NEEDS_DOCS`가 나오면 그대로
    사용자에게 전하고, 지급/부지급을 단정하지 말 것. 지급액도 계산 근거가
    갖춰졌을 때만 나온다.
    """
    parsed = _parse_date(enrolled_on)
    if parsed is None:
        return f"가입일 형식이 올바르지 않다: {enrolled_on!r} (YYYY-MM-DD)"

    claim = extract_claim("MCP", product, parsed, narrative, enrich=lookup)
    result = adjudicate(driver(), claim)
    return _render(result, claim.diagnosis_codes)


def _render(result: Adjudication, codes: tuple[str, ...]) -> str:
    lines = [
        f"판정 {result.decision}   지급액 {result.amount:,}원",
        f"적용 약관 {result.applied_version}",
        f"사유 {result.reason}",
        f"추출 진단코드 {', '.join(codes) or '없음'}",
    ]
    if result.guardrails:
        lines.append(f"발동한 가드레일 {', '.join(result.guardrails)}")
    lines.append("스텝:")
    for step in result.steps:
        lines.append(f"  {step.step} {step.elapsed_ms:.0f}ms — {step.summary}")
    if result.evidence:
        lines.append("근거:")
        for item in result.evidence[:6]:
            lines.append(
                f"  [{item.role}] 제{item.article_number}조({item.article_title}) "
                f"{_shorten(item.quote)}"
            )
            lines.append(f"    {item.node_uid}")
    lines.append("이 판정은 보조이며 최종 결정은 심사자가 한다.")
    return "\n".join(lines)


def _version_of(enrolled_on: date) -> str | None:
    with driver().session() as session:
        record = session.run(
            _VERSION_AT, on_date=enrolled_on.strftime("%Y%m%d")
        ).single()
    return record["effective_from"] if record else None


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None


def _shorten(text: str) -> str:
    flat = " ".join(text.split())
    return flat[:QUOTE_CHARS] + ("…" if len(flat) > QUOTE_CHARS else "")


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="clausegraph MCP 서버")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "http"])
    args = parser.parse_args()
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
