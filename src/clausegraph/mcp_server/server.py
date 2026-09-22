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
import time
from datetime import date

from mcp.server.mcpserver import MCPServer
from neo4j import GraphDatabase

from ..agents.coverage import article_scoped_notes, resolve_version
from ..agents.definition_terms import TermIndex, terms_from_articles
from ..agents.definition_triage import triage
from ..agents.exclusion import enumerate_exclusions, screen
from ..agents.extract import extract_claim
from ..agents.followup import answer
from ..agents.kcd import matches
from ..agents.models import Adjudication, Claim, ClaimHistory
from ..agents.orchestrator import adjudicate
from ..agents.quote import prose_quote
from ..agents.session import STORE, TurnKind
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

# 부칙 적용례 — 세칙 시행일과 약관 적용일이 다르고, 적용 대상이 신계약으로
# 한정되는 경우가 있다. 버전을 알려 줄 때 함께 보여야 한다.
_PROVISIONS_AT = """
MATCH (v:Version)
WHERE v.effective_from <= $on_date AND $on_date < v.effective_to
MATCH (v)-[:HAS_PROVISION]->(p:Provision)
WHERE p.new_contracts_only
RETURN p.promulgated_on AS promulgated_on,
       p.candidate_dates AS candidate_dates,
       p.text AS text
ORDER BY p.promulgated_on DESC
LIMIT 5
"""


# 정의 조문. 제목에 '정의'가 든 조문을 그대로 가져와 색인을 만든다
# (notes/035). 판본을 가리지 않는다 — 어느 판본에도 없을 때만 "없다"고
# 말해야 하므로 합집합이 맞다.
_DEFINITION_ARTICLES = """
MATCH (a:Article)
WHERE a.title CONTAINS '정의'
RETURN a.number AS number, a.title AS title, a.text AS text
"""

_term_index: TermIndex | None = None


def term_index() -> TermIndex:
    """용어 색인. 한 번 만들고 들고 있는다 — 약관은 프로세스 도는 동안
    바뀌지 않는다."""
    global _term_index
    if _term_index is None:
        with driver().session() as session:
            rows = [dict(record) for record in session.run(_DEFINITION_ARTICLES)]
        _term_index = terms_from_articles(rows)
    return _term_index


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
def resolve_terms_version(enrolled_on: str, product: str = "") -> str:
    """가입일에 적용되던 약관 버전을 찾는다. `enrolled_on`은 YYYY-MM-DD.

    조항을 인용하기 **전에** 호출한다. 보험은 가입 당시 약관이 적용되므로,
    버전을 정하지 않고 조문을 읽으면 그 계약에 없는 조항을 들이댈 수 있다.
    2026-05-06 개편으로 실손 특별약관이 중증/비중증 둘로 갈렸고, 그 전에
    가입한 사람의 '실손 특별약관'은 지금 문서에 없는 상품이다.

    **`product`를 함께 주는 것이 맞다.** 부칙이 약관의 적용일을 따로 정하고
    그 적용일이 상품마다 다르다. 같은 날 가입해도 실손은 새 약관, 생명·
    질병상해는 옛 약관인 구간이 있다. 상품을 주지 않으면 시행일자만 보고
    답하며, 그 답이 상품에 따라 달라질 수 있다고 함께 알린다.
    """
    parsed = _parse_date(enrolled_on)
    if parsed is None:
        return f"가입일 형식이 올바르지 않다: {enrolled_on!r} (YYYY-MM-DD)"

    if product:
        return _version_for_product(parsed, enrolled_on, product)

    with driver().session() as session:
        record = session.run(_VERSION_AT, on_date=parsed.strftime("%Y%m%d")).single()

    if record is None:
        return (
            f"가입일 {enrolled_on}에 적용되던 약관을 수집 범위에서 찾지 못했다. "
            "list_products로 수집 범위를 확인하고, 범위 밖이면 판정할 수 "
            "없음을 알려야 한다."
        )
    end = "현재" if record["effective_to"] == OPEN_ENDED else record["effective_to"]
    lines = [
        f"가입일 {enrolled_on} -> 적용 약관 {record['effective_from']} ~ {end} "
        f"(조문 {record['article_count']}개)",
        "이 답은 세칙 시행일자만 본 것이다. 부칙이 정한 약관 적용일은 "
        "상품마다 다르므로, 상품이 정해지면 product를 주고 다시 부를 것.",
    ]

    with driver().session() as session:
        provisions = list(
            session.run(_PROVISIONS_AT, on_date=parsed.strftime("%Y%m%d"))
        )
    if provisions:
        lines.append(
            "\n주의: 이 버전에는 신계약부터 적용된다고 정한 부칙이 있다. "
            "가입일이 그 시점 이전이면 옛 약관이 남아 있을 수 있으므로 "
            "단정하지 말고 심사자 확인이 필요하다고 전할 것."
        )
        for provision in provisions:
            dates = ", ".join(provision["candidate_dates"]) or "날짜 미상"
            lines.append(f"  공포 {provision['promulgated_on']} / 날짜 후보 {dates}")
            lines.append(f"    {_shorten(provision['text'])}")
    return "\n".join(lines)


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

    version = _version_of(parsed, product)
    if version is None:
        return f"가입일 {enrolled_on}에 {product}로 적용되던 약관을 찾지 못했다."

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

    version = _version_of(parsed, product)
    if version is None:
        return f"가입일 {enrolled_on}에 {product}로 적용되던 약관을 찾지 못했다."

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

    version = _version_of(parsed, product)
    if version is None:
        return f"가입일 {enrolled_on}에 {product}로 적용되던 약관을 찾지 못했다."

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
            # 이 면책의 '다만' 단서가 가리키는 조문. 면책에 걸렸다는 말만
            # 전하면 절반만 답한 것이다 — 예외가 다시 보상을 열 수 있다.
            for exception in hit.exceptions:
                lines.append(
                    f"    >>> 예외: 제{exception.article_number}조"
                    f"({exception.article_title}) {_shorten(exception.quote)}"
                )
                lines.append(f"        근거 {exception.node_uid}")
    return "\n".join(lines)


@mcp.tool()
def adjudicate_claim(
    product: str,
    enrolled_on: str,
    narrative: str,
    paid_this_year: int = -1,
    outpatient_visits_this_year: int = -1,
    self_paid_this_year: int = -1,
    room_charge: int = 0,
) -> str:
    """청구 한 건을 심사해 판정과 근거, 발동한 가드레일을 반환한다.

    청구 내용이 자연어로 들어왔을 때 호출한다. 사실추출 -> 보장탐색 ->
    면책검증 -> 금액산정 -> 검증 순으로 돌고, 근거 조항을 특정하지 못하면
    결론을 내지 않는다.

    **판정은 보조다.** `HUMAN_REVIEW`나 `NEEDS_DOCS`가 나오면 그대로
    사용자에게 전하고, 지급/부지급을 단정하지 말 것. 지급액도 계산 근거가
    갖춰졌을 때만 나온다.

    뒤의 셋은 **그 계약의 올해 누적**이다. 약관에도 청구서에도 없는 값이므로
    보험사 시스템에서 받아 넣어야 한다.

    - `paid_this_year` — 연간한도에서 이미 지급한 금액을 뺀다
    - `outpatient_visits_this_year` — 통원 횟수 한도(특약 100회) 소진 판정
    - `self_paid_this_year` — 급여 **입원**의 자기부담 연 200만원 상한.
      이것만 방향이 반대다. 넘긴 만큼을 **더** 지급한다.

    **모르면 넣지 말 것.** 값을 지어내면 한도를 다 쓴 계약에 그대로 지급하게
    된다. 넣지 않으면 지급액 대신 **상한**(앞의 둘) 또는 **하한**(마지막)이
    나오고 판정은 `HUMAN_REVIEW`로 간다 — 그게 맞는 답이다
    (notes/027 · notes/028).

    `room_charge`는 청구한 **비급여 병실료**다. 상급병실료 차액은 제3조 표의
    별도 행이라 입원의료비와 따로 계산해 더한다. 상급병실을 쓰지 않았거나
    청구에 포함하지 않았으면 0으로 둔다 — 이건 계약 상태가 아니라 **청구의
    내용**이므로 0이 곧 "청구하지 않았다"는 뜻이고, 추측이 아니다(notes/029).
    """
    parsed = _parse_date(enrolled_on)
    if parsed is None:
        return f"가입일 형식이 올바르지 않다: {enrolled_on!r} (YYYY-MM-DD)"

    # 음수는 "주지 않았다"는 뜻이다. 0은 "올해 아무것도 안 받았다"이고,
    # 둘은 전혀 다른 말이다.
    totals = (paid_this_year, outpatient_visits_this_year, self_paid_this_year)
    history = (
        None
        if all(value < 0 for value in totals)
        else ClaimHistory(
            paid_this_year=max(0, paid_this_year),
            outpatient_visits_this_year=max(0, outpatient_visits_this_year),
            self_paid_this_year=max(0, self_paid_this_year),
        )
    )
    claim = extract_claim(
        "MCP",
        product,
        parsed,
        narrative,
        enrich=lookup,
        history=history,
        room_charge=max(0, room_charge),
    )
    result = adjudicate(driver(), claim)
    rendered = _render(result, claim.diagnosis_codes)
    session = STORE.open(claim, result, rendered)
    return f"{rendered}\n대화 id {session.session_id} — 후속 질문은 `follow_up`."


@mcp.tool()
def triage_definition(issue: str) -> str:
    """약관 **용어의 뜻**을 다투는 쟁점을 받아, 판단에 무엇이 필요한지 알려준다.

    *"이 시술이 약관에서 정한 수술에 해당하나요"*, *"이 입원이 암의 치료를
    직접 목적으로 한 입원인가요"* 처럼 **조문을 찾는 문제가 아니라 조문을
    해석하는 문제**일 때 호출한다.

    **판정하지 않는다.** 실제 분쟁 21건을 세어 보니 이런 쟁점은 상품 약관의
    정의, 상품 분류표, 판례, 의무기록으로 갈렸고 그중 표준약관에 있는 것은
    거의 없다(notes/034). 근거 없이 해당/미해당을 말하면 지어내는 것이다.

    대신 이것들을 돌려준다.

    - 다투는 용어가 **표준약관에 정의돼 있는가** — 조회 결과이며 추측이 아니다
    - 정의는 있는데 **그 정의가 또 다른 문서를 가리키는가** (`장해` → `<부표 3>`)
    - 무엇을 더 가져와야 하는가

    **판례가 필요한지는 말하지 않는다.** 쟁점 문장만으로는 알 수 없다.

    이 답을 사용자에게 전할 때 지급/부지급을 단정하지 말 것. 무엇을 확인해야
    하는지만 전하고 최종 판단은 심사자에게 남긴다.
    """
    text = issue.strip()
    if not text:
        return "쟁점 문장을 달라. 무엇을 다투는지 있어야 무엇이 필요한지 말할 수 있다."
    return triage(text, term_index()).render()


@mcp.tool()
def follow_up(session_id: str, question: str) -> str:
    """앞서 심사한 건에 이어 묻는다. 판정 이유·근거 조항·필요 서류·지급액.

    `adjudicate_claim`이 돌려준 `대화 id`로 호출한다. 청구 내용을 다시
    넣지 않는다 — 그 대화가 청구와 판정을 들고 있다.

    **답은 저장된 판정 구조에서 나온다.** 앞 턴의 답변 문장을 이어 쓰지
    않으므로 인용한 조항이 원본에서 떠내려가지 않는다.

    질문에 **판정을 바꾸는 새 사실**(누적 지급액, 다른 진단코드, 입원일수
    변경 등)이 섞여 있으면 답하지 않고 `revise_claim`으로 보낸다. 낡은
    판정을 근거까지 붙여 설명하는 것이 이 도구에서 가장 위험한 실패다.

    대화는 30분이 지나면 사라진다. 없으면 `adjudicate_claim`부터 다시 한다.
    """
    session = STORE.get(session_id)
    if session is None:
        return (
            f"그 대화를 찾지 못했다 ({session_id}). 30분이 지나 사라졌거나"
            " 없는 id다. `adjudicate_claim`으로 다시 시작할 것."
        )

    kind, text = answer(session, question)
    STORE.save(session.with_turn(TurnKind.FOLLOW_UP, question, text, now=time.monotonic()))
    return f"[{kind}] {text}"


@mcp.tool()
def revise_claim(
    session_id: str,
    paid_this_year: int = -1,
    outpatient_visits_this_year: int = -1,
    self_paid_this_year: int = -1,
    room_charge: int = -1,
    narrative: str = "",
) -> str:
    """새 사실을 넣어 **같은 건을 다시 심사한다.** 앞 판정과의 차이를 낸다.

    `follow_up`이 "판정을 바꾸는 값이 들어 있다"고 돌려보냈을 때 호출한다.
    청구인이 뒤늦게 말한 누적 지급액, 빠뜨린 진단, 고쳐 말한 입원일수가
    여기로 들어온다.

    주지 않은 값은 **그대로 둔다.** 음수는 "주지 않았다"는 뜻이고 0은
    "올해 아무것도 없다"는 뜻이라 서로 다른 말이다. `narrative`를 주면
    사실추출을 다시 돌려 진단코드·일수·금액을 새로 뽑는다.

    판정은 갈아 끼우지 않고 **판(revision)을 올린다.** 어느 판을 근거로
    답했는지 되짚을 수 있어야 하기 때문이다.
    """
    session = STORE.get(session_id)
    if session is None:
        return f"그 대화를 찾지 못했다 ({session_id}). `adjudicate_claim`부터 다시 할 것."

    before = session.adjudication
    claim = _revise(session.claim, paid_this_year, outpatient_visits_this_year,
                    self_paid_this_year, room_charge, narrative)
    result = adjudicate(driver(), claim)
    rendered = _render(result, claim.diagnosis_codes)
    STORE.save(
        session.with_turn(
            TurnKind.REVISE, narrative or "값 갱신", rendered,
            now=time.monotonic(), claim=claim, adjudication=result,
        )
    )
    return f"{_diff(before, result)}\n\n{rendered}"


def _revise(
    claim: Claim,
    paid: int,
    visits: int,
    self_paid: int,
    room_charge: int,
    narrative: str,
) -> Claim:
    """준 값만 갈아 끼운 새 청구. 주지 않은 값은 건드리지 않는다."""
    totals = (paid, visits, self_paid)
    history = claim.history
    if any(value >= 0 for value in totals):
        base = history or ClaimHistory()
        history = ClaimHistory(
            paid_this_year=paid if paid >= 0 else base.paid_this_year,
            outpatient_visits_this_year=(
                visits if visits >= 0 else base.outpatient_visits_this_year
            ),
            self_paid_this_year=(
                self_paid if self_paid >= 0 else base.self_paid_this_year
            ),
        )

    if not narrative:
        return claim.model_copy(
            update={
                "history": history,
                "room_charge": room_charge if room_charge >= 0 else claim.room_charge,
            }
        )

    # 서술이 새로 오면 사실추출을 다시 돌린다. 앞의 코드에 덧붙이지 않는다 —
    # 청구인이 고쳐 말한 것이면 옛 값이 남으면 안 된다.
    return extract_claim(
        claim.claim_id,
        claim.product,
        claim.enrolled_on,
        narrative,
        enrich=lookup,
        history=history,
        room_charge=room_charge if room_charge >= 0 else claim.room_charge,
    )


def _diff(before: Adjudication, after: Adjudication) -> str:
    """무엇이 달라졌는가. 안 달라졌으면 안 달라졌다고 말한다."""
    changes = []
    if before.decision != after.decision:
        changes.append(f"판정 {before.decision} -> {after.decision}")
    if before.amount != after.amount:
        changes.append(f"지급액 {before.amount:,}원 -> {after.amount:,}원")
    gone = set(before.guardrails) - set(after.guardrails)
    added = set(after.guardrails) - set(before.guardrails)
    if gone:
        changes.append(f"풀린 가드레일 {', '.join(sorted(gone))}")
    if added:
        changes.append(f"걸린 가드레일 {', '.join(sorted(added))}")
    if not changes:
        return "다시 심사했고 **달라진 것이 없다.**"
    return "다시 심사한 결과: " + " / ".join(changes)


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


def _version_for_product(parsed: date, enrolled_on: str, product: str) -> str:
    """상품을 아는 경우의 답. 부칙 적용일까지 반영한다."""
    version = _version_of(parsed, product)
    if version is None:
        return (
            f"가입일 {enrolled_on}에 {product}로 적용되던 약관을 수집 범위에서 "
            "찾지 못했다. list_products로 수집 범위를 확인하고, 범위 밖이면 "
            "판정할 수 없음을 알려야 한다."
        )
    plain = None
    with driver().session() as session:
        record = session.run(_VERSION_AT, on_date=parsed.strftime("%Y%m%d")).single()
        if record is not None:
            plain = record["effective_from"]

    lines = [f"가입일 {enrolled_on} / {product} -> 적용 약관 {version}"]
    if plain is not None and plain != version:
        lines.append(
            f"주의: 세칙 시행일자로만 보면 {plain}이지만, 부칙이 이 상품의 "
            f"약관 적용일을 따로 정해 {version}이 적용된다."
        )

    # **판본이 정해져도 그 안의 조문 몇 개는 아직 옛 내용일 수 있다.**
    # 부칙이 조문 단위로 시행일을 따로 정하는 경우가 있고, 조문 단위 버전이
    # 없는 지금 구조로는 그 조문만 되돌릴 수 없다(notes/030).
    scoped = article_scoped_notes(driver(), version, product)
    if scoped:
        lines.append(
            "주의: 이 판본에는 **조문 일부만** 시행일을 따로 정한 부칙이 있다. "
            "아래 조문을 인용할 때는 그 시행일을 함께 확인해야 하고, "
            "단정하지 말고 심사자 확인이 필요하다고 전할 것."
        )
        lines.extend(f"  {note}" for note in scoped)
    return "\n".join(lines)


def _version_of(enrolled_on: date, product: str) -> str | None:
    """가입일에 **그 상품에** 적용되던 약관 버전.

    시행일자만 보고 고르면 안 된다. 부칙이 약관의 적용일을 따로 정하고
    그 적용일이 상품마다 다르다(notes/016).

        2026-05-06 개정의 부칙:
        "[별표15] 표준약관(개인실손의료보험은 제외한다) 개정내용은
         2026년 6월 6일 이후 체결되는 보험계약부터 적용한다"

    상품을 무시하면 2026-05-06 ~ 06-06 가입자에게 실손이 아닌 상품까지
    새 약관을 한 달 일찍 들이댄다. 실측으로 가입일×상품 1,472쌍 중 32쌍
    (2.2%)이 어긋났다 — 심사 에이전트와 MCP 도구가 서로 다른 답을 냈다.
    """
    return resolve_version(driver(), enrolled_on, product)


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None


def _shorten(text: str) -> str:
    """도구가 돌려줄 인용문. 심사 에이전트와 같은 규칙을 쓴다.

    표 테두리로 인용 예산을 채우지 않는다 — 같은 문장을 사람이 읽든
    모델이 읽든 근거로 쓸 수 있어야 한다(notes/021).
    """
    return prose_quote(text, QUOTE_CHARS)


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
