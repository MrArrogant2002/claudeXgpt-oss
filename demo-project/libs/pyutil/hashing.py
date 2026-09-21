"""Small hashing helpers shared across the Python services."""

import hashlib


def short_digest(text: str, length: int = 8) -> str:
    """A short, stable hex digest of `text` — handy for cache keys and etags."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]
