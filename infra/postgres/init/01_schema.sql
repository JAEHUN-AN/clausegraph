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

-- 어휘 검색용 어간 색인 (notes/038).
--
-- `content`를 그대로 to_tsvector에 넣지 않는다. Postgres에 한국어 형태소
-- 규칙이 없어 'simple' 설정은 어절을 통째로 넣는데, 그러면 '대상'과
-- '대상에'가 다른 말이 된다 — notes/025에서 이미 값을 치른 실패다.
-- 그래서 색인 전에 파이썬 쪽(agents/exclusion.stem)에서 조사를 떼고,
-- 여기에는 어간만 공백으로 이어 붙인 문자열이 들어온다.
--
-- 생성 컬럼(GENERATED)으로 두지 않는 이유가 그것이다. 어간 규칙이 DB 밖에
-- 있으므로 DB가 스스로 만들 수 없다.
ALTER TABLE clause_chunk ADD COLUMN IF NOT EXISTS lexeme tsvector;

CREATE INDEX IF NOT EXISTS clause_chunk_lexeme ON clause_chunk USING GIN (lexeme);
