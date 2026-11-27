"""Embedding models for search by meaning.

Keyword search finds a bookmark by the words it contains. An embedding model
turns text into a vector so that "feedback tool" also finds a tweet that says
"we built an internal thing to collect user complaints". The model runs
locally (`fastembed <https://github.com/qdrant/fastembed>`_, ONNX, no API key
and nothing leaves the machine) and is an optional install::

    pip install "pinboard-bookmarks-mcp-server[semantic]"

Without it everything else works and search is keyword only.

An embedder is anything with a ``model_id``, ``embed_documents(texts)`` and
``embed_query(text)``, each vector returned as unit-length float32 bytes so
the index can compare them with a dot product.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Sequence
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: Small (about 70 MB), fast on a laptop CPU, and strong for its size on
#: English retrieval. ``PINBOARD_EMBEDDING_MODEL`` picks another fastembed model.
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"

MODEL_ENV_VAR = "PINBOARD_EMBEDDING_MODEL"
MIN_SIMILARITY_ENV_VAR = "PINBOARD_SEMANTIC_MIN_SIMILARITY"

#: Cosine similarity below which a bookmark is not a semantic match at all.
#: Without a floor every query "matches" its nearest neighbours, however far
#: away they are. The figure suits the default model, whose scores for
#: unrelated English text sit around 0.3 to 0.45; it is a starting point, not a
#: measured one, and ``PINBOARD_SEMANTIC_MIN_SIMILARITY`` overrides it. Another
#: model has another scale, so it gets no floor unless one is set.
DEFAULT_MIN_SIMILARITY = 0.45
SEMANTIC_ENV_VAR = "PINBOARD_SEMANTIC"

_OFF = {"false", "0", "no", "off"}


class Embedder(Protocol):
    """What the index needs from an embedding model."""

    model_id: str

    def embed_documents(self, texts: Sequence[str]) -> list[bytes]: ...

    def embed_query(self, text: str) -> bytes: ...


def to_unit_bytes(vector: Any) -> bytes:
    """A vector as unit-length float32 bytes."""
    import numpy as np

    array = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(array))
    unit = array / norm if norm > 0 else array
    return bytes(unit.astype(np.float32).tobytes())


class FastEmbedEmbedder:
    """A fastembed text model, loaded on first use.

    Loading downloads the model once (to fastembed's cache) and takes a second
    or two after that, so it is deferred until something is embedded, and one
    lock keeps the background indexer and a query from loading it twice.
    """

    def __init__(
        self, model_name: str = DEFAULT_MODEL, min_similarity: float | None = None
    ):
        self.model_name = model_name
        if min_similarity is None:
            min_similarity = (
                DEFAULT_MIN_SIMILARITY if model_name == DEFAULT_MODEL else 0.0
            )
        self.min_similarity = min_similarity
        self.model_id = f"fastembed:{model_name}"
        self._model: Any = None
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        """Whether the model is loaded. A search never waits for a download."""
        return self._model is not None

    def warm_up(self) -> None:
        """Load the model now (downloading it the first time)."""
        self._load()

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding

                self._model = TextEmbedding(model_name=self.model_name)
            return self._model

    def embed_documents(self, texts: Sequence[str]) -> list[bytes]:
        model = self._load()
        return [to_unit_bytes(vector) for vector in model.passage_embed(list(texts))]

    def embed_query(self, text: str) -> bytes:
        model = self._load()
        return to_unit_bytes(next(iter(model.query_embed(text))))


def fastembed_installed() -> bool:
    """Whether the optional ``semantic`` extra is present."""
    from importlib.util import find_spec

    try:
        return find_spec("fastembed") is not None and find_spec("numpy") is not None
    except (ImportError, ValueError):
        return False


def default_embedder(
    environ: dict[str, str] | os._Environ[str] | None = None,
) -> Embedder | None:
    """The configured embedder, or ``None`` when semantic search is off or not installed."""
    env = environ if environ is not None else os.environ
    if env.get(SEMANTIC_ENV_VAR, "").strip().lower() in _OFF:
        return None
    if not fastembed_installed():
        return None
    floor: float | None = None
    stated = env.get(MIN_SIMILARITY_ENV_VAR, "").strip()
    if stated:
        try:
            floor = min(max(float(stated), -1.0), 1.0)
        except ValueError:
            logger.warning(
                "%s is not a number; using the default", MIN_SIMILARITY_ENV_VAR
            )
    return FastEmbedEmbedder(
        env.get(MODEL_ENV_VAR, "").strip() or DEFAULT_MODEL, min_similarity=floor
    )


def embed_pending(
    index: Any,
    embedder: Embedder,
    batch_size: int = 64,
    max_batches: int | None = None,
    should_stop: Any = None,
) -> int:
    """Embed every bookmark that has no current vector. Returns how many were embedded.

    Works newest first in small batches, each committed on its own, so a run
    that is interrupted loses at most one batch and search improves as it goes.
    """
    done = 0
    batches = 0
    while max_batches is None or batches < max_batches:
        if should_stop is not None and should_stop():
            break
        pending = index.pending_embeddings(embedder.model_id, batch_size)
        if not pending:
            break
        vectors = embedder.embed_documents([text or " " for _, text, _ in pending])
        if len(vectors) != len(pending):
            raise RuntimeError(
                "the embedding model returned the wrong number of vectors"
            )
        index.store_embeddings(
            embedder.model_id,
            [
                (bookmark_id, text_hash, vector)
                for (bookmark_id, _, text_hash), vector in zip(
                    pending, vectors, strict=True
                )
            ],
        )
        done += len(pending)
        batches += 1
    return done
