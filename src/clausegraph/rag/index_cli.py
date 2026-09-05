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

MANIFEST_FILENAME = "manifest.json"
TERMS_DIRNAME = "terms"
UPSERT_BATCH = 200

_UPSERT = """
INSERT INTO clause_chunk (
    node_uid, node_kind, effective_from, product, coverage,
    article_number, article_title, is_exclusion, chunk_index, content, embedding
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (node_uid, chunk_index) DO UPDATE
SET content = EXCLUDED.content, embedding = EXCLUDED.embedding
"""


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
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    with connection.cursor() as cursor:
        cursor.executemany(_UPSERT, rows)
    connection.commit()


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
    args = parser.parse_args()
    return run(args.data, args.all_versions)


if __name__ == "__main__":
    raise SystemExit(main())
