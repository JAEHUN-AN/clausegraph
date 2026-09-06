"""지급심사 데모.

    uv run --extra graph python -m clausegraph.agents.cli

실제 분쟁조정사례에서 가져온 청구 시나리오를 흘려 보고, 스텝별 흐름과
가드레일 발동을 함께 찍는다.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date

from neo4j import GraphDatabase

from .extract import extract_claim
from .models import Adjudication, ClaimHistory
from .orchestrator import adjudicate
from .terminology import lookup

NON_BENEFIT = "실손의료보험 특별약관1(중증 비급여 실손의료비)"
ACCIDENT_HEALTH = "질병·상해보험(손해보험 회사용)"
BENEFIT = "기본형 실손의료보험(급여 실손의료비)"

SCENARIOS = (
    (
        "CLM-001",
        NON_BENEFIT,
        date(2026, 7, 1),
        "2026.8.10 우울증(F32) 진단으로 정신과 통원치료를 받았습니다."
        " 비급여 진료비 480,000원 청구합니다.",
    ),
    (
        "CLM-002",
        NON_BENEFIT,
        date(2026, 7, 1),
        "2026.8.2 충치로 임플란트 시술을 받았습니다. 비급여 1,200,000원 청구합니다."
        " 연락처 010-1234-5678",
    ),
    (
        "CLM-003",
        ACCIDENT_HEALTH,
        date(2023, 1, 5),
        "2026.6.20 계단에서 넘어져 발목 골절(S82) 진단을 받고 7일간 입원했습니다."
        " 900,000원 청구합니다.",
    ),
    (
        "CLM-004",
        ACCIDENT_HEALTH,
        date(2026, 5, 1),
        "2026.8.1 교통사고로 늑골 골절(S22) 진단, 5일간 입원했습니다."
        " 600,000원 청구합니다.",
    ),
    (
        "CLM-005",
        NON_BENEFIT,
        date(2020, 3, 1),
        "2026.5.5 어지럼증으로 검사를 받았습니다. 300,000원 청구합니다.",
    ),
    # 아래 둘은 실제로 지급액이 나오는 경로다. 급여와 비급여의 파라미터가
    # 어떻게 다른 금액을 만드는지 나란히 본다(notes/017).
    (
        "CLM-006",
        BENEFIT,
        date(2025, 4, 1),
        "2026.8.20 폐렴(J18) 진단으로 6일간 입원했습니다. 1,500,000원 청구합니다.",
    ),
    # 특약1은 20260506 판본에서 처음 생긴 상품이다. 그 앞으로 가입일을 두면
    # 보장 조항이 없다 — 상품이 판본마다 생기고 사라진다는 사실 그대로다.
    (
        "CLM-007",
        NON_BENEFIT,
        date(2026, 6, 1),
        "2026.8.20 유방암(C50)으로 비급여 항암치료 통원을 받았습니다."
        " 1,000,000원 청구합니다.",
    ),
    # 급여 통원은 공제가 세 항이고, 세 번째 항의 건강보험 본인부담률은
    # 약관에 없는 값이다(영수증에서 온다). 그래서 계산은 끝까지 되지만
    # 그 금액은 지급액이 아니라 상한이다(notes/018).
    (
        "CLM-008",
        BENEFIT,
        date(2025, 4, 1),
        "2026.8.25 급성 기관지염(J20)으로 통원치료를 받았습니다."
        " 500,000원 청구합니다.",
    ),
    # 면책에 걸리지만 그 면책의 '다만' 단서가 보상 조문을 가리키는 경우.
    # 근거에 [exception]으로 함께 나온다(notes/022).
    (
        "CLM-009",
        BENEFIT,
        date(2025, 4, 1),
        "2026.8.1 교통사고로 3일간 입원했습니다. 자동차보험에서 치료관계비를 일부"
        " 받았고 본인부담의료비 800,000원을 청구합니다.",
    ),
    # 아래 둘만 계약의 올해 누적을 함께 넣는다. 나머지는 그 값이 없어서
    # 지급액 대신 상한만 나온다 — 그 차이를 나란히 본다(notes/027).
    (
        "CLM-010",
        BENEFIT,
        date(2025, 4, 1),
        "2026.9.1 폐렴(J18)으로 5일간 입원했습니다. 2,000,000원 청구합니다.",
        ClaimHistory(paid_this_year=1_500_000),
    ),
    (
        "CLM-011",
        NON_BENEFIT,
        date(2026, 6, 1),
        "2026.9.2 유방암(C50)으로 비급여 항암치료 통원을 받았습니다."
        " 400,000원 청구합니다.",
        ClaimHistory(paid_this_year=0, outpatient_visits_this_year=100),
    ),
    # 자기부담 200만원 상한(제5조 ④)은 방향이 반대인 유일한 조항이다 —
    # 누적을 알수록 **더** 지급한다(notes/028).
    (
        "CLM-012",
        BENEFIT,
        date(2025, 4, 1),
        "2026.9.3 폐렴(J18)으로 10일간 입원했습니다. 3,000,000원 청구합니다.",
        ClaimHistory(paid_this_year=2_000_000, self_paid_this_year=1_500_000),
    ),
)


def show(result: Adjudication) -> None:
    print(f"\n{'=' * 74}")
    print(f"{result.claim_id}  ->  {result.decision}   지급액 {result.amount:,}원")
    print(f"  적용 약관 {result.applied_version}   총 {result.total_ms:.0f}ms")
    print(f"  사유: {result.reason}")
    if result.guardrails:
        print(f"  가드레일: {', '.join(result.guardrails)}")
    print("  --- 스텝 ---")
    for step in result.steps:
        mark = "o" if step.ok else "x"
        print(f"   {mark} {step.step:18s} {step.elapsed_ms:7.1f}ms  {step.summary}")
    if result.evidence:
        print("  --- 근거 ---")
        for item in result.evidence[:5]:
            print(f"   [{item.role}] 제{item.article_number}조({item.article_title})")
            print(f"       {item.quote[:96]}")


def run(use_terminology: bool) -> int:
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    try:
        for scenario in SCENARIOS:
            claim_id, product, enrolled_on, narrative = scenario[:4]
            history = scenario[4] if len(scenario) > 4 else None
            claim = extract_claim(
                claim_id, product, enrolled_on, narrative,
                enrich=lookup if use_terminology else None,
                history=history,
            )
            show(adjudicate(driver, claim))
    finally:
        driver.close()
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="지급심사 데모")
    parser.add_argument(
        "--no-terminology",
        action="store_true",
        help="용어 -> 코드 변환을 끄고 돌린다 (LLM 슬롯이 빈 상태)",
    )
    args = parser.parse_args()
    return run(use_terminology=not args.no_terminology)


if __name__ == "__main__":
    raise SystemExit(main())
