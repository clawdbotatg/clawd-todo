#!/usr/bin/env python3
"""clawd-todo — a tiny personal todo server + mobile PWA.

Pure Python stdlib, one JSON file of state, one bearer token.
Serves the UI (index.html/sw.js/manifest/icons, read from disk per request,
so UI edits need no restart) and a small REST API:

  GET    /api/todos               -> {"rev": N, "todos": [...]}
  POST   /api/todos               {"text": "...", "via": "cli"}   -> todo
  POST   /api/todos/<id>          {"done": true|false, "text": "..."} -> todo
  DELETE /api/todos/<id>          -> {"ok": true}
  POST   /api/clear_done          -> {"removed": N}

Auth: every /api/* call needs the token, as `Authorization: Bearer <t>`
or `?t=<t>`. Static files are served without auth (the UI is useless
without the token, same stance as clawd-harness).

Env: TODO_PORT (8794), TODO_HOST (127.0.0.1), TODO_TOKEN or
TODO_TOKEN_FILE (.clawd-todo.token, auto-generated), TODO_DATA (todos.json).
"""
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get("TODO_PORT", "8794"))
HOST = os.environ.get("TODO_HOST", "127.0.0.1")
DATA = Path(os.environ.get("TODO_DATA", HERE / "todos.json"))
TOKEN_FILE = Path(os.environ.get("TODO_TOKEN_FILE", HERE / ".clawd-todo.token"))

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
}


def load_token() -> str:
    tok = os.environ.get("TODO_TOKEN", "").strip()
    if tok:
        return tok
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    tok = secrets.token_urlsafe(24)
    TOKEN_FILE.write_text(tok + "\n")
    TOKEN_FILE.chmod(0o600)
    return tok


TOKEN = load_token()
LOCK = threading.Lock()


def _load_state():
    try:
        st = json.loads(DATA.read_text())
        assert isinstance(st.get("todos"), list)
        st.setdefault("rev", 0)
        return st
    except Exception:
        return {"rev": 0, "todos": []}


STATE = _load_state()


def _save_state():
    # atomic write; caller holds LOCK
    tmp = DATA.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(STATE, indent=1))
    tmp.replace(DATA)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _find(tid):
    for t in STATE["todos"]:
        if t["id"] == tid:
            return t
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet; errors still raise
        pass

    # ---- helpers -------------------------------------------------
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _authed(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and secrets.compare_digest(auth[7:].strip(), TOKEN):
            return True
        q = parse_qs(urlparse(self.path).query)
        for t in q.get("t", []):
            if secrets.compare_digest(t, TOKEN):
                return True
        return False

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 1_000_000:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8", "replace"))
        except Exception:
            return {}

    # ---- routes --------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if path in STATIC:
            name, ctype = STATIC[path]
            f = HERE / name
            if not f.exists():
                return self._send(404, {"error": "missing " + name})
            body = f.read_bytes()
            # Installed-PWA token handoff: iOS gives a home-screen web app its
            # own storage, so the manifest's start_url must carry the token.
            # The page requests the manifest with ?t=<token>; only an authed
            # request gets the tokenized start_url (the bare manifest leaks
            # nothing).
            if name == "manifest.webmanifest" and self._authed():
                body = body.replace(b'"start_url": "/"',
                                    b'"start_url": "/?t=' + TOKEN.encode() + b'"')
            return self._send(200, body, ctype)
        if path == "/api/todos":
            if not self._authed():
                return self._send(401, {"error": "bad token"})
            with LOCK:
                return self._send(200, {"rev": STATE["rev"], "todos": STATE["todos"]})
        if path == "/healthz":
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._send(404, {"error": "not found"})
        if not self._authed():
            return self._send(401, {"error": "bad token"})

        if path == "/api/todos":
            body = self._body()
            text = str(body.get("text", "")).strip()
            if not text:
                return self._send(400, {"error": "empty text"})
            todo = {
                "id": secrets.token_hex(4),
                "text": text[:2000],
                "done": False,
                "created": _now(),
                "done_at": None,
                "via": str(body.get("via", "web"))[:40],
            }
            with LOCK:
                STATE["todos"].insert(0, todo)
                STATE["rev"] += 1
                _save_state()
            return self._send(200, todo)

        if path == "/api/clear_done":
            with LOCK:
                before = len(STATE["todos"])
                STATE["todos"] = [t for t in STATE["todos"] if not t["done"]]
                removed = before - len(STATE["todos"])
                if removed:
                    STATE["rev"] += 1
                    _save_state()
            return self._send(200, {"removed": removed})

        m = re.fullmatch(r"/api/todos/([0-9a-f]{8})", path)
        if m:
            body = self._body()
            with LOCK:
                todo = _find(m.group(1))
                if not todo:
                    return self._send(404, {"error": "no such todo"})
                if "done" in body:
                    todo["done"] = bool(body["done"])
                    todo["done_at"] = _now() if todo["done"] else None
                if "text" in body and str(body["text"]).strip():
                    todo["text"] = str(body["text"]).strip()[:2000]
                STATE["rev"] += 1
                _save_state()
                return self._send(200, todo)

        return self._send(404, {"error": "not found"})

    def do_DELETE(self):
        path = urlparse(self.path).path
        if not self._authed():
            return self._send(401, {"error": "bad token"})
        m = re.fullmatch(r"/api/todos/([0-9a-f]{8})", path)
        if m:
            with LOCK:
                todo = _find(m.group(1))
                if not todo:
                    return self._send(404, {"error": "no such todo"})
                STATE["todos"].remove(todo)
                STATE["rev"] += 1
                _save_state()
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"clawd-todo on http://{HOST}:{PORT}/?t={TOKEN}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
