"""Minimal Language Server Protocol client over stdio (JSON-RPC 2.0).

Spawns a language server as a child process and speaks LSP to it: Content-Length
framed messages, request/response correlation by id, a background reader thread,
and replies to the few server->client requests that would otherwise make a server
hang. Local only — the server is a process on this machine; nothing hits the network.
"""

import json
import os
import subprocess
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse


class LspError(RuntimeError):
    pass


def path_to_uri(path):
    return Path(path).resolve().as_uri()


def uri_to_path(uri):
    parsed = urlparse(uri)
    path = unquote(parsed.path)
    # Windows: "/C:/a/b" -> "C:/a/b"
    if os.name == "nt" and len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return path


class LspClient:
    """One language-server process. Thread-safe for serial use by the agent loop."""

    def __init__(self, cmd, root_path, language, timeout=30):
        self.language = language
        self.timeout = timeout
        self._id = 0
        self._pending = {}  # id -> [Event, result, error]
        self._lock = threading.Lock()
        self._opened = set()
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                cwd=str(root_path),
            )
        except (OSError, ValueError) as e:
            raise LspError(f"could not start language server {cmd!r}: {e}") from e
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._initialize(root_path)

    def alive(self):
        return self.proc.poll() is None

    # --- wire protocol ------------------------------------------------------
    def _read_loop(self):
        stdout = self.proc.stdout
        try:
            while True:
                length = None
                while True:  # read headers up to the blank line
                    line = stdout.readline()
                    if not line:
                        raise EOFError
                    line = line.strip()
                    if not line:
                        break
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":", 1)[1].strip())
                if length is None:
                    continue
                body = stdout.read(length)
                if not body:
                    raise EOFError
                try:
                    msg = json.loads(body.decode("utf-8", "replace"))
                except ValueError:
                    continue
                self._dispatch(msg)
        except Exception:
            # stream closed / server died: unblock everyone waiting.
            with self._lock:
                for slot in self._pending.values():
                    slot[0].set()

    def _dispatch(self, msg):
        if "id" in msg and "method" in msg:
            # server -> client request; must answer or some servers stall.
            self._send({"jsonrpc": "2.0", "id": msg["id"], "result": self._server_request(msg)})
        elif "id" in msg:
            with self._lock:
                slot = self._pending.get(msg["id"])
                if slot:
                    slot[1] = msg.get("result")
                    slot[2] = msg.get("error")
                    slot[0].set()
        # notifications (no id) are ignored

    def _server_request(self, msg):
        if msg.get("method") == "workspace/configuration":
            items = (msg.get("params") or {}).get("items") or []
            return [{} for _ in items]
        return None

    def _send(self, obj):
        data = json.dumps(obj).encode("utf-8")
        header = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
        try:
            self.proc.stdin.write(header + data)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise LspError(f"language server stdin closed: {e}") from e

    def _notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method, params, timeout=None):
        with self._lock:
            self._id += 1
            rid = self._id
            slot = [threading.Event(), None, None]
            self._pending[rid] = slot
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        if not slot[0].wait(timeout or self.timeout):
            with self._lock:
                self._pending.pop(rid, None)
            raise LspError(f"LSP request timed out: {method}")
        with self._lock:
            self._pending.pop(rid, None)
        if not self.alive():
            raise LspError("language server exited")
        if slot[2]:
            raise LspError(f"LSP error for {method}: {slot[2]}")
        return slot[1]

    # --- lifecycle ----------------------------------------------------------
    def _initialize(self, root_path):
        root_uri = path_to_uri(root_path)
        self._request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": root_uri,
                "workspaceFolders": [{"uri": root_uri, "name": "root"}],
                "capabilities": {
                    "workspace": {"symbol": {}, "configuration": True},
                    "textDocument": {
                        "hover": {"contentFormat": ["plaintext", "markdown"]},
                        "definition": {},
                        "references": {},
                        "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                    },
                },
            },
            timeout=max(self.timeout, 30),
        )
        self._notify("initialized", {})

    def shutdown(self):
        try:
            if self.alive():
                self._request("shutdown", None, timeout=5)
                self._notify("exit", {})
        except Exception:
            pass
        try:
            self.proc.terminate()
        except Exception:
            pass

    # --- document sync ------------------------------------------------------
    def ensure_open(self, path):
        uri = path_to_uri(path)
        if uri in self._opened:
            return uri
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise LspError(f"cannot read {path}: {e}") from e
        self._notify(
            "textDocument/didOpen",
            {"textDocument": {"uri": uri, "languageId": self.language, "version": 1, "text": text}},
        )
        self._opened.add(uri)
        return uri

    # --- queries ------------------------------------------------------------
    def workspace_symbol(self, query):
        return self._request("workspace/symbol", {"query": query}) or []

    def definition(self, uri, line, char):
        return self._request(
            "textDocument/definition",
            {"textDocument": {"uri": uri}, "position": {"line": line, "character": char}},
        )

    def references(self, uri, line, char):
        return (
            self._request(
                "textDocument/references",
                {
                    "textDocument": {"uri": uri},
                    "position": {"line": line, "character": char},
                    "context": {"includeDeclaration": True},
                },
            )
            or []
        )

    def hover(self, uri, line, char):
        return self._request(
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": {"line": line, "character": char}},
        )

    def document_symbol(self, uri):
        return self._request("textDocument/documentSymbol", {"textDocument": {"uri": uri}}) or []
