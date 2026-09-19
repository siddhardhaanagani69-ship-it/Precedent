"""Load BGE once per worker and return unit-length 384-dimensional vectors."""

import os
import threading
import numpy as np
from engine.config import ROOT

os.environ.setdefault("HF_HOME", str(ROOT / "data/huggingface"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")


class Embeddings:
    def __init__(self):
        from fastembed import TextEmbedding
        self.model = TextEmbedding("BAAI/bge-small-en-v1.5", cache_dir=str(ROOT / "data/embeddings"),
                                   threads=2, local_files_only=True)
        self.lock = threading.Lock()
        self.embed(["Ready"])

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, 384))
        with self.lock:
            values = np.asarray(list(self.model.embed(texts, batch_size=32)))
        norms = np.linalg.norm(values, axis=1, keepdims=True)
        if values.shape != (len(texts), 384) or not np.isfinite(values).all() or (norms == 0).any():
            raise ValueError("Embedding model returned invalid vectors")
        return values / norms
