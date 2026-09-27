"""어휘 검색 — 벡터가 못 보는 쪽.

`retriever.py`가 이미 `hybrid`라는 이름을 쓰고 있지만 그것은 **벡터+그래프**
였다. 이 모듈이 더하는 것은 흔히 말하는 하이브리드의 나머지 절반, 즉
**어휘 검색**이다.

## 왜 어휘가 필요한가

notes/008의 결론은 "면책은 부정 조건이라 벡터가 놓친다"였다. 그런데 그
18문항을 다시 보면 벡터가 통째로 놓친 9개 중 몇은 **낱말이 실제로 겹친다.**

    질문  "입원 중 간병인을 쓰고 진단서 발급비도 냈습니다"
    조항  "간병비, 증명서 발급비용"

`간병`이 양쪽에 있다. 벡터는 이걸 놓쳤는데 문자열 일치는 놓칠 수 없다.
그러니 "벡터가 놓친다"와 "검색으로 못 찾는다"는 같은 말이 아니다. 어휘를
붙여 봐야 **어디까지가 임베딩의 한계이고 어디부터가 열거의 몫인지** 갈린다.

## 어절이 아니라 어간으로 센다

notes/025에서 이미 값을 치른 자리다. 변별어 4,528개의 **46.6%가 조사 붙은
어절**이었고, `대상`은 흔해서 걸러지는데 `대상에`는 희귀어로 통과했다.

어휘 색인에서도 같은 일이 일어난다. 질문의 `간병인을`과 조항의 `간병비`가
어절로는 다른 말이다. 그래서 색인과 질의 **양쪽에** `agents.exclusion.stem`을
건다 — 같은 규칙을 두 벌 만들면 notes/023의 드리프트가 재현된다.

## ts_rank_cd이지 BM25가 아니다

공고 문구는 "하이브리드 검색"이고 업계 통용 구현은 BM25지만, 여기서 쓰는
것은 Postgres의 `ts_rank_cd`다. pgvector 이미지에 BM25 확장(`pg_search` 등)이
없고, 폐쇄망 전제에서 확장을 더 얹는 것은 비용이다. **점수 공식이 다르므로
BM25라고 적지 않는다.**

대신 순위 융합을 **RRF**로 한다(`retriever.fuse_rrf`). RRF는 점수가 아니라
순위만 쓰므로 코사인 유사도와 `ts_rank_cd`를 정규화해 섞는 문제를 통째로
비켜 간다 — 공식이 다른 것이 문제가 되지 않는 자리에서 섞는다.

## ts_rank_cd에는 IDF가 없다

이게 BM25와의 진짜 차이다. 공식 이름이 다른 게 아니라 **말뭉치 통계를 안
쓴다.** 처음 붙여 보고 바로 드러났다.

    질문  "오토바이 경주 대회에 나갔다가 사고로 크게 다쳤습니다"
    1위   제8조(보험금의 지급절차)
    2위   제5조(보험금의 청구)

`보험금`·`청구`·`지급`이 약관 어디에나 있어서, 이 질문에서 실제로 변별력
있는 `경주`·`오토바` 가 흔한 말에 묻힌다. BM25라면 IDF가 알아서 눌러 준다.

notes/009가 같은 문제를 이미 풀어 뒀다 — **문서빈도가 낮은 낱말만 쓴다.**
거기서는 조문 452개로 쟀고, 여기서는 색인 그 자체가 말뭉치이므로 Postgres의
`ts_stat`으로 잰다. 재는 대상과 검색하는 대상이 같아야 한다.

그래서 어휘 검색을 **두 벌로 재고 둘 다 보고한다.**

- `lexical`     — 질문의 모든 어간으로 질의
- `lexical-df`  — 문서빈도가 문턱 아래인 어간으로만 질의

문턱을 하나 더 붙이는 것이 개선인지 아닌지는 재 보기 전에는 모른다.
"""

from __future__ import annotations

from collections.abc import Callable

import psycopg

# 색인·질의 양쪽이 같은 토큰 규칙을 써야 한다. 심사가 쓰는 것을 그대로
# 가져온다 — 두 벌 만들면 조용히 어긋난다(notes/023).
from ..agents.exclusion import stemmed_tokens
from .retriever import Hit

DEFAULT_K = 10

# 색인 청크의 이 비율을 넘게 나오는 어간은 변별력이 없다.
#
# notes/009는 조문 452개 기준 1.6%였다. 여기는 말뭉치가 다르다 — 청크
# 1,689개이고 조문 하나가 여러 청크로 쪼개져 같은 낱말이 여러 번 센다.
# 그래서 그 값을 그대로 옮기지 않고 이 말뭉치에서 다시 잡는다.
MAX_DOCUMENT_FREQUENCY = 0.05

_TS_STAT = """
SELECT word, ndoc FROM ts_stat('SELECT lexeme FROM clause_chunk')
"""

_CHUNK_COUNT = "SELECT count(*) FROM clause_chunk WHERE lexeme IS NOT NULL"

_LEXICAL_SEARCH = """
SELECT node_uid, node_kind, product, article_number, article_title,
       is_exclusion, content,
       ts_rank_cd(lexeme, to_tsquery('simple', %s)) AS score
FROM clause_chunk
WHERE lexeme @@ to_tsquery('simple', %s)
  AND (%s::text IS NULL OR effective_from = %s)
ORDER BY score DESC
LIMIT %s
"""


def lexemes(text: str) -> str:
    """색인·질의에 쓸 어간 토큰을 공백으로 이어 붙인다.

    `to_tsvector('simple', ...)`는 넘긴 문자열을 자기 규칙으로 또 자르는데,
    공백으로 갈라 둔 한글 토큰은 그대로 하나씩 남는다. 어간을 먼저 만들어
    넘기는 이유가 이것이다 — Postgres에 한국어 형태소 규칙이 없다.
    """
    return " ".join(stemmed_tokens(text))


MIN_PREFIX_LEN = 2


def to_tsquery_or(
    text: str,
    *,
    keep: Callable[[str], bool] | None = None,
    prefix: bool = False,
) -> str:
    """질문의 어간을 OR로 잇는다.

    AND로 이으면 한 낱말만 어긋나도 0건이 된다. 면책 조회는 **놓치지 않는
    것**이 목적이므로 넓게 걸고 순위로 가린다.

    `keep`을 주면 그 판정을 통과한 어간만 쓴다(문서빈도 필터).

    `prefix`면 각 어간에 `:*`를 붙여 앞자리 일치로 건다. 한국어 복합어를
    이어 주려는 것이다 — 청구인은 `간병인`이라 쓰고 약관은 `간병비`라
    적는다. 어간을 떼도 두 낱말은 다르고, 겹치는 것은 앞의 `간병`뿐이다.
    조사 목록으로는 이 간극을 못 메운다(notes/025의 한계와 같은 자리).
    """
    tokens = sorted(set(stemmed_tokens(text)))
    if keep is not None:
        tokens = [token for token in tokens if keep(token)]
    # to_tsquery는 메타문자를 해석한다. 한글 토큰만 남기므로 남을 것이 없지만,
    # 색인 대상이 늘면 여기가 주입 경로가 된다. 화이트리스트로 막는다.
    safe = [token for token in tokens if token.isalnum()]
    if prefix:
        # 한 글자짜리 앞자리는 아무 데나 걸린다. notes/009에서 '치과'가
        # '치'가 되는 것을 막은 것과 같은 이유로 길이를 본다.
        return " | ".join(
            f"{token}:*" if len(token) >= MIN_PREFIX_LEN else token for token in safe
        )
    return " | ".join(safe)


_stat_cache: tuple[dict[str, int], int] | None = None


def corpus_stats(connection: psycopg.Connection) -> tuple[dict[str, int], int]:
    """색인의 낱말별 문서 수와 전체 문서 수.

    `ts_stat`은 색인 자체를 훑는다. **검색하는 말뭉치와 재는 말뭉치가 같다**
    — notes/009에서 "면책 조항들 안에서만 재면 안 된다"고 적어 둔 것과 같은
    이유다. 말뭉치가 고정이면 결과도 고정이라 프로세스 안에 캐시한다.
    """
    global _stat_cache
    if _stat_cache is None:
        with connection.cursor() as cursor:
            cursor.execute(_CHUNK_COUNT)
            total = cursor.fetchone()[0] or 1
            cursor.execute(_TS_STAT)
            _stat_cache = ({word: ndoc for word, ndoc in cursor.fetchall()}, total)
    return _stat_cache


def distinctive_filter(
    connection: psycopg.Connection,
    *,
    max_df: float = MAX_DOCUMENT_FREQUENCY,
    prefix: bool = False,
) -> Callable[[str], bool]:
    """어간 하나가 변별력 있는지 판정하는 함수를 만든다.

    **앞자리 일치일 때는 셈이 달라진다.** `간병`은 색인에 그 꼴로 없을 수
    있지만 `간병비`·`간병인`을 합치면 문서 수가 생긴다. 앞자리로 걸 것이면
    앞자리로 세야 한다 — 정확 일치 기준으로 걸러 놓고 앞자리로 검색하면
    거른 기준과 쓰는 기준이 어긋난다(notes/023이 그 모양이었다).
    """
    stat, total = corpus_stats(connection)
    limit = max(1, int(total * max_df))

    if not prefix:
        return lambda token: 0 < stat.get(token, 0) <= limit

    words = sorted(stat.items())

    def prefix_ndoc(token: str) -> int:
        # 색인 낱말이 2만 개대라 훑어도 질의당 수 ms다. 질문 하나에 어간이
        # 열 개 남짓이라 미리 트라이를 세울 값어치가 없다.
        return sum(ndoc for word, ndoc in words if word.startswith(token))

    return lambda token: 0 < prefix_ndoc(token) <= limit


def clear_cache() -> None:
    """색인을 다시 만든 뒤 부른다."""
    global _stat_cache
    _stat_cache = None


def search_lexical(
    connection: psycopg.Connection,
    query: str,
    *,
    k: int = DEFAULT_K,
    effective_from: str | None = None,
    distinctive_only: bool = False,
    prefix: bool = False,
) -> list[Hit]:
    """어간 일치로 k개. 걸리는 것이 없으면 빈 리스트다.

    `distinctive_only`면 문서빈도가 낮은 어간만으로 질의한다 — ts_rank_cd에
    IDF가 없어서 흔한 말이 순위를 먹는 것을 막는다.
    """
    keep = (
        distinctive_filter(connection, prefix=prefix) if distinctive_only else None
    )
    tsquery = to_tsquery_or(query, keep=keep, prefix=prefix)
    if not tsquery and distinctive_only:
        # 변별어가 하나도 안 남는 질문이 있다. 빈 질의로 0건을 내는 것보다
        # 전체 어간으로 한 번 더 거는 쪽이 낫다 — 놓치는 것이 목적이 아니다.
        tsquery = to_tsquery_or(query)
    if not tsquery:
        return []
    with connection.cursor() as cursor:
        cursor.execute(
            _LEXICAL_SEARCH, (tsquery, tsquery, effective_from, effective_from, k)
        )
        return [
            Hit(
                node_uid=row[0],
                node_kind=row[1],
                product=row[2],
                article_number=row[3],
                article_title=row[4],
                is_exclusion=row[5],
                content=row[6],
                score=float(row[7]),
                source="lexical",
            )
            for row in cursor.fetchall()
        ]
