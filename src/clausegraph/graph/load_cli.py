"""표준약관 그래프 적재 CLI.

    uv run python -m clausegraph.graph.load_cli

manifest.json의 **버전**(약관이 실제로 바뀐 시점)만 적재한다. 세칙 개정마다
넣으면 같은 약관이 중복된다 (notes/004).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from neo4j import GraphDatabase

from ..law.parse_cli import parse_file
from ..law.table_parser import Lexicon
from .loader import apply_schema, link_history, load_version
from .queries import COUNTS
from .schema import OPEN_ENDED

MANIFEST_FILENAME = "manifest.json"
TERMS_DIRNAME = "terms"


def run(data_dir: Path) -> int:
    manifest_path = data_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        print(f"manifest가 없다: {manifest_path} — law.collect를 먼저 돌릴 것", file=sys.stderr)
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    versions = manifest["versions"]

    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    try:
        driver.verify_connectivity()
        apply_schema(driver)
        print(f"적재 대상 {len(versions)}개 버전\n")

        lexicon = Lexicon.from_terms_dir(data_dir / TERMS_DIRNAME)
        totals = {"articles": 0, "items": 0, "exclusions": 0, "table_items": 0}
        for version in versions:
            doc = parse_file(data_dir / TERMS_DIRNAME / version["file"])
            result = load_version(
                driver,
                doc,
                effective_to=version["effective_to"],
                sha=version["content_sha256"],
                admrul_seqs=version["admrul_seqs"],
                lexicon=lexicon,
            )
            totals["articles"] += result.articles
            totals["items"] += result.items
            totals["exclusions"] += result.exclusions
            totals["table_items"] += result.table_items
            end = version["effective_to"] or "현재"
            print(
                f"  {version['effective_from']} ~ {end}  "
                f"조문 {result.articles:4d}  호 {result.items:4d}  면책 {result.exclusions:2d}  "
                f"표 사유 {result.table_items:3d}(보장종목 {result.coverages:2d})"
            )

        link_history(driver)
        print("\n버전 계보 연결 완료")

        with driver.session() as session:
            print("\n=== 노드 ===")
            for record in session.run(COUNTS):
                print(f"  {record['count']:6d}  {':'.join(record['labels'])}")
    finally:
        driver.close()

    print(f"\n합계 조문 {totals['articles']}, 호 {totals['items']}, 면책 {totals['exclusions']}")
    print(f"열린 버전 표기: effective_to = {OPEN_ENDED}")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="표준약관 그래프 적재")
    parser.add_argument("--data", type=Path, default=Path("data/law"))
    args = parser.parse_args()
    return run(args.data)


if __name__ == "__main__":
    raise SystemExit(main())
