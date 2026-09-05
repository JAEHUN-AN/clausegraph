"""심사 지연 측정.

    uv run --extra graph python -m clausegraph.agents.bench --claims 200

같은 청구를 반복하면 캐시가 다 데워져 실제보다 빠르게 나온다. 그래서
상품·가입일·진단코드를 섞어 돌린다. 첫 몇 건은 캐시가 비어 있어 느린데,
그것도 사용자가 겪는 지연이므로 버리지 않고 **웜업 구간으로 따로 표시한다.**

청구는 코드 안에서 합성한다. 실제 청구 데이터나 개인정보를 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import date, timedelta

from neo4j import GraphDatabase

from ..observability import REGISTRY, Registry
from .extract import extract_claim
from .orchestrator import adjudicate
from .terminology import lookup

WARMUP_CLAIMS = 5
SEED = 20260905

# 수집 범위(2025-04-01 이후) 안에서만 고른다 — 범위 밖은 즉시 NEEDS_DOCS로
# 끝나 심사 경로를 재지 못한다.
ENROLL_BASE = date(2025, 5, 1)
ENROLL_SPAN_DAYS = 480

PRODUCTS = (
    "실손의료보험 특별약관1(중증 비급여 실손의료비)",
    "실손의료보험 특별약관2(비중증 비급여 실손의료비)",
    "기본형 실손의료보험(급여 실손의료비)",
    "질병·상해보험(손해보험 회사용)",
    "생명보험",
)

# 면책에 걸리는 코드와 걸리지 않는 코드를 섞는다. 걸리면 금액산정을
# 건너뛰므로 한쪽만 돌리면 경로가 편향된다.
CASES = (
    ("우울증(F32) 진단으로 정신과 통원치료를 받았습니다", "480,000원"),
    ("발목 골절(S82) 진단으로 7일간 입원했습니다", "900,000원"),
    ("충치로 임플란트 시술을 받았습니다", "1,200,000원"),
    ("늑골 골절(S22) 진단, 5일간 입원했습니다", "600,000원"),
    ("고도비만(E66)으로 위 절제 수술을 받았습니다", "3,000,000원"),
    ("폐렴(J18) 치료로 4일간 입원했습니다", "700,000원"),
)


def synthesize(count: int, rng: random.Random) -> list[tuple[str, date, str]]:
    """상품·가입일·사유를 섞은 청구를 만든다."""
    claims: list[tuple[str, date, str]] = []
    for index in range(count):
        product = rng.choice(PRODUCTS)
        enrolled_on = ENROLL_BASE + timedelta(days=rng.randrange(0, ENROLL_SPAN_DAYS))
        incident = enrolled_on + timedelta(days=rng.randrange(10, 300))
        narrative, amount = CASES[index % len(CASES)]
        claims.append(
            (
                product,
                enrolled_on,
                f"{incident.year}.{incident.month}.{incident.day} "
                f"{narrative}. {amount} 청구합니다.",
            )
        )
    return claims


def run(count: int) -> int:
    if count <= WARMUP_CLAIMS:
        print(f"청구 수가 웜업({WARMUP_CLAIMS})보다 많아야 한다", file=sys.stderr)
        return 1

    rng = random.Random(SEED)
    claims = synthesize(count, rng)

    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    warmup = Registry()
    REGISTRY.reset()
    try:
        print(f"청구 {count}건 (웜업 {WARMUP_CLAIMS}건 별도 집계)\n")
        for index, (product, enrolled_on, narrative) in enumerate(claims):
            if index == WARMUP_CLAIMS:
                for stats in REGISTRY.steps():
                    for sample in stats.samples:
                        warmup.record(stats.name, sample)
                REGISTRY.reset()
            claim = extract_claim(
                f"BENCH-{index:04d}", product, enrolled_on, narrative, enrich=lookup
            )
            adjudicate(driver, claim)
    finally:
        driver.close()

    print(warmup.report(f"웜업 {WARMUP_CLAIMS}건 — 캐시가 비어 있는 상태"))
    print()
    measured = count - WARMUP_CLAIMS
    print(REGISTRY.report(f"본 측정 {measured}건"))

    per_claim = sum(stats.total_ms for stats in REGISTRY.steps()) / measured
    print(f"\n청구 1건당 스텝 합계 평균 {per_claim:.1f}ms")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="심사 지연 측정")
    parser.add_argument("--claims", type=int, default=200)
    args = parser.parse_args()
    return run(args.claims)


if __name__ == "__main__":
    raise SystemExit(main())
