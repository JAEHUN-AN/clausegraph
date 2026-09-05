"""평가셋의 정답 uid를 색인에서 확정한다.

    uv run --extra rag python -m clausegraph.rag.build_evalset

키워드로 지목한 조항이 색인의 어떤 면책 항목인지 굳혀 파일로 남긴다.
나중에 약관이 바뀌면 uid도 바뀌므로, 평가셋은 색인과 함께 갱신해야 한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .evalset import QUESTIONS
from .retriever import connect_pg

_RESOLVE = """
SELECT node_uid, product, coverage, left(content, 80)
FROM clause_chunk
WHERE is_exclusion AND node_kind = 'item'
  AND product = ANY(%s) AND content LIKE %s
ORDER BY node_uid
"""


def run(out_path: Path) -> int:
    entries = []
    missing = []
    with connect_pg() as connection, connection.cursor() as cursor:
        for question in QUESTIONS:
            cursor.execute(_RESOLVE, (list(question.products), f"%{question.gold_keyword}%"))
            rows = cursor.fetchall()
            if not rows:
                missing.append(question)
                print(
                    f"  [{question.qid:2d}] 정답 없음 — 키워드 {question.gold_keyword!r}",
                    file=sys.stderr,
                )
                continue
            entries.append(
                {
                    "qid": question.qid,
                    "query": question.query,
                    "note": question.note,
                    "products": list(question.products),
                    "gold_keyword": question.gold_keyword,
                    "gold": [row[0] for row in rows],
                }
            )
            print(f"  [{question.qid:2d}] 정답 {len(rows)}개  {question.gold_keyword}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"questions": entries}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\n확정 {len(entries)}문항, 정답 없음 {len(missing)}문항 -> {out_path}")
    return 1 if missing else 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="면책 recall 평가셋 정답 확정")
    parser.add_argument("--out", type=Path, default=Path("data/eval/exclusion_recall.json"))
    args = parser.parse_args()
    return run(args.out)


if __name__ == "__main__":
    raise SystemExit(main())
