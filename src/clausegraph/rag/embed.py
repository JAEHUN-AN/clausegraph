"""로컬 CPU 임베딩.

GPU가 없고 API 크레딧도 쓰지 않으므로 bge-m3를 CPU에서 돌린다. 한국어에
강하고 1024차원이라 `clause_chunk.embedding` 정의와 맞는다.

FP32 대신 **ONNX INT8**을 기본으로 쓴다. kograph에서 같은 모델로 처리량이
두 배(1.43 -> 2.91 chunks/s), 가중치가 2,166MB -> 544MB로 줄고 검색 품질은
같다는 것을 이미 측정했다. 그 산출물을 그대로 가리켜 재사용한다.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import numpy as np

DEFAULT_MODEL = "BAAI/bge-m3"
EMBED_DIM = 1024
MAX_SEQ_TOKENS = 1024
BATCH_SIZE = 16

# kograph에서 만든 양자화 모델을 그대로 쓴다 (2.2GB 재다운로드 회피).
DEFAULT_ONNX_DIR = Path(
    os.getenv("CLAUSEGRAPH_ONNX_DIR", r"C:\workspace\kograph\models\bge-m3-onnx")
)
ONNX_INT8_FILE = "onnx/model_qint8_avx512_vnni.onnx"

# torch | onnx-int8
BACKEND = os.getenv("CLAUSEGRAPH_EMBED_BACKEND", "onnx-int8")


def _load_tokenizer(auto_tokenizer: object, model_dir: Path) -> object:
    """bge-m3 토크나이저. transformers의 오탐 경고를 지운다.

    transformers 4.57은 이 모델을 열 때마다 경고를 낸다.

    > The tokenizer you are loading ... with an incorrect regex pattern ...
    > You should set the `fix_mistral_regex=True` flag

    **이 모델에는 해당하지 않는다.** bge-m3는 `XLMRobertaTokenizerFast`이고
    전처리기가 `Metaspace`다 — 경고가 말하는 Mistral의 정규식 전처리기가
    아니다. 확인차 권고대로 `fix_mistral_regex=True`를 줘 봤더니 오히려
    터진다(`'Metaspace' object does not support item assignment`).
    평가셋 18문항과 약관 문장으로 토큰화를 대조해도 차이가 없다.

    경고를 그냥 두면 CLI 출력마다 섞여 나와 측정 결과를 읽기 어렵다.
    그래서 **이 문구만** 지운다. 다른 경고는 그대로 둔다.

    `warnings.filterwarnings`로는 안 잡힌다 — transformers가 `warnings.warn`이
    아니라 자기 로거로 내보내기 때문이다. 그래서 로깅 필터를 쓴다.
    """
    logger = logging.getLogger("transformers.tokenization_utils_base")
    filter_ = _RegexWarningFilter()
    logger.addFilter(filter_)
    try:
        return auto_tokenizer.from_pretrained(str(model_dir))
    finally:
        logger.removeFilter(filter_)


class _RegexWarningFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "incorrect regex pattern" not in record.getMessage()


class Embedder(Protocol):
    def encode(self, texts: list[str], **kwargs: object) -> np.ndarray: ...


class OnnxEmbedder:
    """onnxruntime 직접 추론.

    SentenceTransformer가 내보낸 ONNX는 풀링·정규화까지 그래프에 들어 있어
    출력이 `sentence_embedding`이다. 후처리가 그래프 안에 있는 편이 빠르다.
    """

    def __init__(self, model_dir: Path, file_name: str = ONNX_INT8_FILE) -> None:
        import onnxruntime as ort
        from transformers import AutoTokenizer

        path = model_dir / file_name
        if not path.exists():
            raise FileNotFoundError(
                f"ONNX 모델이 없다: {path}\n"
                "CLAUSEGRAPH_ONNX_DIR로 경로를 주거나 백엔드를 torch로 바꿀 것"
            )
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            str(path), options, providers=["CPUExecutionProvider"]
        )
        self.tokenizer = _load_tokenizer(AutoTokenizer, model_dir)
        self._input_names = {spec.name for spec in self.session.get_inputs()}

    def encode(
        self,
        texts: list[str],
        batch_size: int = BATCH_SIZE,
        normalize_embeddings: bool = True,
        **_: object,
    ) -> np.ndarray:
        chunks: list[np.ndarray] = []
        for start in range(0, len(texts), batch_size):
            batch = list(texts[start : start + batch_size])
            encoded = self.tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=MAX_SEQ_TOKENS,
                return_tensors="np",
            )
            feed = {key: value for key, value in encoded.items() if key in self._input_names}
            vectors = self.session.run(["sentence_embedding"], feed)[0]
            if normalize_embeddings:
                norms = np.linalg.norm(vectors, axis=1, keepdims=True)
                vectors = vectors / np.maximum(norms, 1e-12)
            chunks.append(vectors)
        return np.vstack(chunks) if chunks else np.empty((0, EMBED_DIM), dtype="float32")


@lru_cache(maxsize=2)
def get_embedder(backend: str = "") -> Embedder:
    """모델 로딩이 수 초 걸리므로 백엔드당 한 번만."""
    backend = backend or BACKEND
    if backend == "onnx-int8":
        return OnnxEmbedder(DEFAULT_ONNX_DIR)

    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(DEFAULT_MODEL, device="cpu")
