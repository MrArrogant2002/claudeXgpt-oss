"""base62 short-code encoding.

A link's integer id is encoded to a compact base62 string for the short URL, and
decoded back to the id on lookup. encode() and decode() must be exact inverses:

    decode(encode(n)) == n   for every non-negative integer n
"""

from .config import ALPHABET, BASE


def encode(n: int) -> str:
    """Encode a non-negative integer id to its base62 short code."""
    if n < 0:
        raise ValueError("cannot encode a negative id")
    out = []
    while n > 0:
        n, rem = divmod(n, BASE)
        out.append(ALPHABET[rem])
    return "".join(reversed(out))


def decode(code: str) -> int:
    """Decode a base62 short code back to its integer id."""
    n = 0
    for ch in code:
        idx = ALPHABET.find(ch)
        if idx < 0:
            raise ValueError(f"invalid base62 character: {ch!r}")
        n = n * BASE + idx
    return n
