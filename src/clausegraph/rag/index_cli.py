"""약관 조문·면책 사유를 pgvector에 색인한다.

    uv run --extra rag --extra onnx python -m clausegraph.rag.index_cli

기본은 최신 버전만 넣는다. 시점별 검색까지 재려면 --all-versions.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

from ..law.parse_cli import parse_file
from ..law.table_parser import Lexicon
from .chunks import Chunk, build_chunks
from .embed import BACKEND, get_embedder
from .lexical import lexemes

MANIFEST_FILENAME = "manifest.json"
TERMS_DIRNAME = "terms"
UPSERT_BATCH = 200

# `lexeme`은 어간을 떼어 넘긴 문자열을 tsvector로 만든 것이다. 어간 규칙이
# 파이썬에 있어 DB가 스스로 만들 수 없으므로 생성 컬럼이 아니라 여기서
# 함께 넣는다 (notes/038).
_UPSERT = """
INSERT INTO clause_chunk (
    node_uid, node_kind, effective_from, product, coverage,
    article_number, article_title, is_exclusion, chunk_index, content, embedding,
    lexeme
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, to_tsvector('simple', %s))
ON CONFLICT (node_uid, chunk_index) DO UPDATE
SET content = EXCLUDED.content, embedding = EXCLUDED.embedding,
    lexeme = EXCLUDED.lexeme
"""

# 이미 색인된 행에 어간만 채운다. 임베딩을 다시 만들지 않는다 — bge-m3
# CPU로 1,689청크를 다시 도는 데 10분이 걸리고, 바뀐 것은 어휘 쪽뿐이다.
_BACKFILL_SELECT = "SELECT id, content FROM clause_chunk WHERE lexeme IS NULL"
_BACKFILL_UPDATE = "UPDATE clause_chunk SET lexeme = to_tsvector('simple', %s) WHERE id = %s"


def connect() -> psycopg.Connection:
    connection = psycopg.connect(os.environ["PG_DSN"])
    register_vector(connection)
    return connection


def upsert(connection: psycopg.Connection, chunks: list[Chunk], vectors) -> None:
    rows = [
        (
            chunk.node_uid,
            chunk.node_kind,
            chunk.effective_from,
            chunk.product,
            chunk.coverage,
            chunk.article_number,
            chunk.article_title,
            chunk.is_exclusion,
            chunk.chunk_index,
            chunk.content,
            vector,
            lexemes(chunk.content),
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    with connection.cursor() as cursor:
        cursor.executemany(_UPSERT, rows)
    connection.commit()


def backfill_lexemes(connection: psycopg.Connection) -> int:
    """`lexeme`이 빈 행을 채운다. 어휘 색인을 나중에 더했으므로 필요하다."""
    with connection.cursor() as cursor:
        cursor.execute(_BACKFILL_SELECT)
        rows = cursor.fetchall()
        if rows:
            cursor.executemany(
                _BACKFILL_UPDATE,
                [(lexemes(content), row_id) for row_id, content in rows],
            )
    connection.commit()
    return len(rows)


def run(data_dir: Path, all_versions: bool) -> int:
    manifest = json.loads((data_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    versions = manifest["versions"] if all_versions else manifest["versions"][:1]
    lexicon = Lexicon.from_terms_dir(data_dir / TERMS_DIRNAME)

    print(f"색인 대상 {len(versions)}개 버전, 백엔드 {BACKEND}")
    embedder = get_embedder()

    with connect() as connection:
        for version in versions:
            doc = parse_file(data_dir / TERMS_DIRNAME / version["file"])
            chunks = build_chunks(doc, lexicon)
            kinds = {"article": 0, "item": 0}
            for chunk in chunks:
                kinds[chunk.node_kind] += 1
            print(
                f"\n  {doc.effective_on}  청크 {len(chunks)} "
                f"(조문 {kinds['article']}, 호 {kinds['item']})"
            )

            started = time.perf_counter()
            done = 0
            for start in range(0, len(chunks), UPSERT_BATCH):
                batch = chunks[start : start + UPSERT_BATCH]
                vectors = embedder.encode([chunk.content for chunk in batch])
                upsert(connection, batch, vectors)
                done += len(batch)
                elapsed = time.perf_counter() - started
                print(
                    f"    {done}/{len(chunks)}  {done / max(elapsed, 1e-9):.1f} chunks/s",
                    flush=True,
                )

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT node_kind, is_exclusion, count(*) FROM clause_chunk "
                "GROUP BY 1, 2 ORDER BY 3 DESC"
            )
            print("\n=== 색인 현황 ===")
            for kind, exclusion, count in cursor.fetchall():
                label = "면책" if exclusion else "일반"
                print(f"  {count:6d}  {kind:8s} {label}")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="약관 조문 벡터 색인")
    parser.add_argument("--data", type=Path, default=Path("data/law"))
    parser.add_argument("--all-versions", action="store_true")
    parser.add_argument(
        "--backfill-lexemes",
        action="store_true",
        help="임베딩은 그대로 두고 어간 색인만 채운다",
    )
    args = parser.parse_args()
    if args.backfill_lexemes:
        with connect() as connection:
            filled = backfill_lexemes(connection)
        print(f"어간 색인 {filled}행 채움")
        return 0
    return run(args.data, args.all_versions)


if __name__ == "__main__":
    raise SystemExit(main())
