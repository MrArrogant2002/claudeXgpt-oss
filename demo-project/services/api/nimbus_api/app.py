"""Tiny request router for the Nimbus API.

Framework-free on purpose: `handle(method, path, body)` maps a request to a
`(status, payload)` pair, so the routing logic is trivially testable and portable.
A real deployment wraps this in an ASGI/WSGI server — see docs/ARCHITECTURE.md.

Routes:
    POST /shorten            {"url": ...}      -> 201 {"code": ...}
    GET  /api/stats/<code>                     -> 200 {"code", "clicks"}
    GET  /<code>                               -> 302 {"location": <url>}
    GET  /                                      -> 200 {"service": "nimbus"}
"""

from .store import LinkError, LinkStore


class Api:
    def __init__(self, store: LinkStore | None = None):
        self.store = store or LinkStore()

    def handle(self, method: str, path: str, body: dict | None = None):
        try:
            if method == "POST" and path == "/shorten":
                code = self.store.create_link((body or {}).get("url", ""))
                return 201, {"code": code}
            if method == "GET" and path.startswith("/api/stats/"):
                return 200, self.store.stats(path.rsplit("/", 1)[-1])
            if method == "GET" and path.startswith("/"):
                code = path.lstrip("/")
                if not code:
                    return 200, {"service": "nimbus", "ok": True}
                self.store.record_click(code)
                return 302, {"location": self.store.resolve(code)}
            return 404, {"error": "not found"}
        except LinkError as exc:
            return 400, {"error": str(exc)}
