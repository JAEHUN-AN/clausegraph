"""그래프가 핵심 질문에 답하는지 확인한다.

    uv run --extra graph python -m clausegraph.graph.verify_cli

이 프로젝트가 "벡터 검색으로는 구조적으로 안 된다"고 주장하는 두 가지를
그대로 물어본다 — 가입 시점의 약관, 그리고 면책 조항.
"""

from __future__ import annotations

import argparse
import os
import sys

from neo4j import GraphDatabase

from .queries import (
    ARTICLE_HISTORY,
    EXCLUSION_ITEM_COUNTS,
    EXCLUSIONS_AT,
    PRODUCT_LIFESPAN,
    VERSION_AT,
)
from .schema import OPEN_ENDED

SAMPLE_DATES = ("20250501", "20251001", "20260601", "20260915")
SAMPLE_PRODUCT = "질병·상해보험(손해보험 회사용)"
SAMPLE_ARTICLE = "5"


def run() -> int:
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    try:
        driver.verify_connectivity()
        with driver.session() as session:
            print("=== 가입일에 적용되던 약관 버전 ===")
            for day in SAMPLE_DATES:
                record = session.run(VERSION_AT, on_date=day).single()
                if record is None:
                    print(f"  가입일 {day} -> 해당 버전 없음 (수집 범위 밖)")
                    continue
                end = record["effective_to"]
                end = "현재" if end == OPEN_ENDED else end
                print(
                    f"  가입일 {day} -> {record['effective_from']} ~ {end}  "
                    f"조문 {record['article_count']}"
                )

            print("\n=== 5세대 개편으로 사라진 상품 ===")
            for record in session.run(PRODUCT_LIFESPAN, keyword="특별약관"):
                versions = sorted(record["versions"])
                print(f"  {record['product'][:42]:44s} {versions}")

            print(f"\n=== {SAMPLE_PRODUCT} 면책 조문 (가입일 20260915) ===")
            for record in session.run(
                EXCLUSIONS_AT, on_date="20260915", product=SAMPLE_PRODUCT
            ):
                print(
                    f"  제{record['number']}조({record['title']})  "
                    f"사유 {record['item_count']}개"
                )

            print(f"\n=== {SAMPLE_PRODUCT} 제{SAMPLE_ARTICLE}조의 시점별 변화 ===")
            for record in session.run(
                ARTICLE_HISTORY, product=SAMPLE_PRODUCT, number=SAMPLE_ARTICLE
            ):
                print(
                    f"  {record['effective_from']}  {record['length']:6d}자  "
                    f"개정표기 {record['revised_on']}"
                )

            print("\n=== 최신 버전 상품별 면책 사유 수 ===")
            for record in session.run(EXCLUSION_ITEM_COUNTS, open_ended=OPEN_ENDED):
                note = "  ← 사유가 표 안에 있어 아직 못 푼다" if record["items"] == 0 else ""
                print(
                    f"  {record['product'][:42]:44s} 조문 {record['articles']:2d}  "
                    f"사유 {record['items']:3d}{note}"
                )
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

    argparse.ArgumentParser(description="그래프 검증 질의").parse_args()
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
