"""URL validation and normalization for incoming shorten requests."""

from urllib.parse import urlparse

from .config import ALLOWED_SCHEMES, MAX_URL_LENGTH


def is_valid_url(url: str) -> bool:
    """True if `url` is a well-formed http(s) URL within the length limit."""
    if not url or len(url) > MAX_URL_LENGTH:
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    return parsed.scheme in ALLOWED_SCHEMES and bool(parsed.netloc)


def normalize_url(url: str) -> str:
    """Canonicalize a URL: trim whitespace and lowercase the host (path is kept)."""
    parsed = urlparse(url.strip())
    return parsed._replace(netloc=parsed.netloc.lower()).geturl()
