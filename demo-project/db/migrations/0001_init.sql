-- 0001_init: the links table.
CREATE TABLE links (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    url     TEXT NOT NULL,
    created REAL NOT NULL
);
