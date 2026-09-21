-- Nimbus canonical schema (SQLite dialect).
-- This is the full, current shape; see migrations/ for the incremental steps.

CREATE TABLE links (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    url     TEXT NOT NULL,
    created REAL NOT NULL
);

CREATE TABLE clicks (
    link_id INTEGER NOT NULL REFERENCES links(id),
    ts      REAL NOT NULL
);

CREATE INDEX idx_clicks_link ON clicks(link_id);
