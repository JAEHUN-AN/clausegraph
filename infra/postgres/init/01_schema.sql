-- 표준약관 조문·면책 사유의 벡터 색인.
-- 그래프(Neo4j)가 구조를, 이쪽이 문장 유사도를 맡는다.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS clause_chunk (
    id              BIGSERIAL PRIMARY KEY,
    -- 그래프 노드와 잇는 키. Article.uid 또는 Item.uid 그대로다.
    node_uid        TEXT        NOT NULL,
    node_kind       TEXT        NOT NULL CHECK (node_kind IN ('article', 'item')),
    effective_from  CHAR(8)     NOT NULL,
    product         TEXT        NOT NULL,
    coverage        TEXT,
    article_number  TEXT        NOT NULL,
    article_title   TEXT        NOT NULL,
    is_exclusion    BOOLEAN     NOT NULL DEFAULT FALSE,
    chunk_index     INT         NOT NULL DEFAULT 0,
    content         TEXT        NOT NULL,
    embedding       vector(1024),
    UNIQUE (node_uid, chunk_index)
);

CREATE INDEX IF NOT EXISTS clause_chunk_product ON clause_chunk (product);
CREATE INDEX IF NOT EXISTS clause_chunk_version ON clause_chunk (effective_from);
CREATE INDEX IF NOT EXISTS clause_chunk_exclusion ON clause_chunk (is_exclusion);
