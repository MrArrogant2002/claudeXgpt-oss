-- 0002_add_clicks: click events + an index for per-link aggregation.
CREATE TABLE clicks (
    link_id INTEGER NOT NULL REFERENCES links(id),
    ts      REAL NOT NULL
);

CREATE INDEX idx_clicks_link ON clicks(link_id);
