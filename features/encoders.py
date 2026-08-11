"""Sentence embeddings, cached (3.6).

The encoder is a pretrained sentence-transformer used as an *input feature*.
The trained component is the matcher in model/, not this.

Two rules this module exists to enforce:

  * Loading is lazy. The executor and the scanner never need embeddings, and
    importing torch to fill a form would be absurd.
  * Absence is loud. If the model cannot load, `encode` raises. A silent zero
    vector would make features 1-4 look merely uninformative instead of broken,
    and that failure is invisible in a trained model's accuracy.
"""

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO / "data" / "cache"
CACHE_PATH = CACHE_DIR / "embeddings.json"

MODEL_NAME = "all-MiniLM-L6-v2"
DIMS = 384

_model = None
_cache = None
_dirty = False


class EncoderUnavailable(RuntimeError):
    """Raised when embeddings are asked for and the model cannot be loaded."""


def _load_cache():
    global _cache
    if _cache is None:
        if CACHE_PATH.exists():
            _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        else:
            _cache = {}
    return _cache


def save_cache():
    global _dirty
    if not _dirty:
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(_load_cache()), encoding="utf-8")
    _dirty = False


def available():
    """True when embeddings can be produced, without raising."""
    try:
        model()
        return True
    except EncoderUnavailable:
        return False


def model():
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EncoderUnavailable(
                "sentence-transformers is not installed; features 1-4 cannot be "
                "computed. pip install sentence-transformers"
            ) from exc
        # Load from the local hub cache first. Once the model is on disk there
        # is nothing to fetch, and huggingface_hub 1.x can fail its revalidation
        # request outright ("Cannot send a request, as the client has been
        # closed"), which would otherwise look like a missing model.
        errors = []
        for local_only in (True, False):
            try:
                _model = SentenceTransformer(MODEL_NAME, local_files_only=local_only)
                return _model
            except Exception as exc:  # noqa: BLE001 - network, disk, corrupt cache
                errors.append(f"local_files_only={local_only}: {exc}")
        raise EncoderUnavailable(
            f"could not load {MODEL_NAME}; " + " | ".join(errors)
        )
    return _model


def _key(text):
    return hashlib.sha1(f"{MODEL_NAME}\x00{text}".encode("utf-8")).hexdigest()


def encode(text):
    """A unit-normalised embedding for one string. Cached across runs."""
    global _dirty
    text = (text or "").strip()
    if not text:
        return [0.0] * DIMS

    cache = _load_cache()
    key = _key(text)
    if key in cache:
        return cache[key]

    vector = model().encode(text, normalize_embeddings=True)
    cache[key] = [float(v) for v in vector]
    _dirty = True
    return cache[key]


def encode_many(texts):
    return [encode(t) for t in texts]


def cosine(a, b):
    """Cosine similarity, rescaled to [0,1] so every feature shares a range."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    raw = dot / (na * nb)
    return max(0.0, min(1.0, (raw + 1.0) / 2.0))


def similarity(left, right):
    """Cosine between two strings, in [0,1]. Empty input scores 0."""
    if not (left or "").strip() or not (right or "").strip():
        return 0.0
    return cosine(encode(left), encode(right))


if __name__ == "__main__":
    if not available():
        print("encoder unavailable")
        sys.exit(1)
    pairs = [
        ("FINAL GRADE", "Grade 0-100"),
        ("PROGRAM", "Course"),
        ("YEAR LEVEL", "Year 1-5"),
        ("PROGRAM", "Recommendations optional"),
        ("FINAL GRADE", "Grade (Recomputed) 0-100"),
    ]
    for left, right in pairs:
        print(f"  {left:<14} ~ {right:<26} {similarity(left, right):.3f}")
    save_cache()
