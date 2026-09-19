"""Verify the public 384-dimensional embedding model on this machine."""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.config import ROOT

# Keep downloaded model files inside ignored project data.
os.environ.setdefault("HF_HOME", str(ROOT / "data/huggingface"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
# Standard HTTP avoids a stalled Xet transfer on this machine.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")

def main() -> int:
    import numpy as np
    from fastembed import TextEmbedding

    try:
        model = TextEmbedding("BAAI/bge-small-en-v1.5", cache_dir=str(ROOT / "data/embeddings"), threads=2)
        vectors = np.asarray(list(model.embed(["Should I change jobs?", "Choosing a new career"], batch_size=32)))
        if vectors.shape != (2, 384) or not np.isfinite(vectors).all():
            raise ValueError("Unexpected embedding shape or values")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if (norms == 0).any():
            raise ValueError("Zero embedding")
        normalized = vectors / norms
        assert np.allclose(np.linalg.norm(normalized, axis=1), 1)
        print("Embeddings: pass (384 dimensions; normalization verified)")
        return 0
    except Exception as exc:
        print(f"Embeddings: fail ({type(exc).__name__}); stop before changing embedding libraries")
        return 1


if __name__ == "__main__":
    if "--worker" in sys.argv:
        raise SystemExit(main())
    try:
        result = subprocess.run([sys.executable, __file__, "--worker"], timeout=120)
        raise SystemExit(result.returncode)
    except subprocess.TimeoutExpired:
        print("Embeddings: fail (download/load exceeded 120 seconds); stop and report before changing libraries")
        raise SystemExit(1)
