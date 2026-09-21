"""Persistence layer: links and click events, stored in SQLite.

Uses the stdlib `sqlite3` module (in-memory by default) so there is no external
database to run. Link ids auto-increment from 1 and are base62-encoded into the
short code by `shortener.encode`.
"""

import sqlite3
import time

from .config import DB_PATH
from .shortener import decode, encode
from .validators import is_valid_url, normalize_url

_SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    url     TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS clicks (
    link_id INTEGER NOT NULL,
    ts      REAL NOT NULL
);
"""


class LinkError(Exception):
    """Raised for an invalid URL or an unknown short code."""


class LinkStore:
    """CRUD for links plus click recording/aggregation."""

    def __init__(self, db_path: str = DB_PATH):
        self._db = sqlite3.connect(db_path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def create_link(self, url: str) -> str:
        """Store `url` and return its freshly minted short code."""
        if not is_valid_url(url):
            raise LinkError(f"invalid url: {url!r}")
        cur = self._db.execute(
            "INSERT INTO links(url, created) VALUES(?, ?)",
            (normalize_url(url), time.time()),
        )
        self._db.commit()
        return encode(cur.lastrowid)

    def resolve(self, code: str) -> str:
        """Return the original URL for a short code, or raise LinkError."""
        row = self._db.execute(
            "SELECT url FROM links WHERE id=?", (decode(code),)
        ).fetchone()
        if row is None:
            raise LinkError(f"unknown code: {code!r}")
        return row["url"]

    def record_click(self, code: str) -> None:
        self._db.execute(
            "INSERT INTO clicks(link_id, ts) VALUES(?, ?)", (decode(code), time.time())
        )
        self._db.commit()

    def stats(self, code: str) -> dict:
        row = self._db.execute(
            "SELECT COUNT(*) AS n FROM clicks WHERE link_id=?", (decode(code),)
        ).fetchone()
        return {"code": code, "clicks": row["n"]}
