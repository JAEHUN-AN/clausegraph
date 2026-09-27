"""교차 인코더 리랭킹 — 후보를 다시 세운다.

## 왜 이걸 재는가

이 프로젝트의 주장은 notes/008에 있다.

> 면책 조회는 랭킹 문제가 아니라 열거 문제다.

리랭킹은 **그 주장을 정면으로 시험한다.** 벡터의 recall@100은 88%다 —
정답이 후보 안에 이미 있다는 뜻이다. 교차 인코더가 그걸 상위 10개로
끌어올릴 수 있다면 **랭킹 문제가 맞고 주장을 고쳐야 한다.** 못 끌어올리면
주장이 한 겹 더 단단해진다.

어느 쪽이 나오든 답이 되는 측정이라 값어치가 있다.

## 모델

`BAAI/bge-reranker-v2-m3`. 임베딩으로 쓰는 bge-m3와 같은 계열이라 한국어
처리가 일관되고, 교차 인코더라 질문과 조항을 **함께** 본다 — 벡터가 각자
인코딩해 놓치는 관계를 볼 수 있는 자리가 여기다.

GPU가 없으므로 CPU로 돈다. **이 비용이 측정의 일부다.** 심사 한 건이
1.3ms인 시스템에 초 단위 리랭커를 얹는 것이 값어치가 있는지는 recall과
지연을 같이 놓고 봐야 정해진다.
"""

from __future__ import annotations

import os
from dataclasses import replace
from functools import lru_cache

from .retriever import Hit

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"
# 조항 전문이 길다. 512를 넘기면 뒤가 잘리는데, 면책 사유는 앞머리에 오므로
# 자르는 쪽이 느려지는 것보다 낫다.
MAX_LENGTH = 512
BATCH_SIZE = 16


@lru_cache(maxsize=1)
def _model():
    """모델을 한 번만 올린다. 첫 호출에 수 초가 걸린다."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    name = os.getenv("CLAUSEGRAPH_RERANKER", DEFAULT_MODEL)
    tokenizer = AutoTokenizer.from_pretrained(name)
    model = AutoModelForSequenceClassification.from_pretrained(name)
    model.eval()
    return tokenizer, model, torch


def score_pairs(query: str, documents: list[str]) -> list[float]:
    """(질문, 문서) 쌍마다 점수 하나. 값의 절대 크기는 뜻이 없고 순서만 쓴다."""
    if not documents:
        return []
    tokenizer, model, torch = _model()
    scores: list[float] = []
    with torch.no_grad():
        for start in range(0, len(documents), BATCH_SIZE):
            batch = documents[start : start + BATCH_SIZE]
            encoded = tokenizer(
                [query] * len(batch),
                batch,
                padding=True,
                truncation=True,
                max_length=MAX_LENGTH,
                return_tensors="pt",
            )
            logits = model(**encoded).logits.view(-1).float()
            scores.extend(logits.tolist())
    return scores


def rerank(query: str, hits: list[Hit], *, limit: int) -> list[Hit]:
    """후보를 교차 인코더 점수로 다시 세워 상위 `limit`개.

    `Hit.score`를 리랭커 점수로 갈아 끼운다. 원래 점수를 남겨 두지 않는
    이유는 두 값의 단위가 달라 한 필드에 섞으면 무엇을 보고 있는지
    모르게 되기 때문이다 — 순위만 쓸 것이면 순위만 남긴다.
    """
    if not hits:
        return []
    scores = score_pairs(query, [hit.content for hit in hits])
    ordered = sorted(
        (replace(hit, score=score, source=f"{hit.source}+rerank")
         for hit, score in zip(hits, scores, strict=True)),
        key=lambda hit: hit.score,
        reverse=True,
    )
    return ordered[:limit]
