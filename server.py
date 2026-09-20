#!/usr/bin/env python3
"""clawd-todo — a tiny personal todo server + mobile PWA.

Pure Python stdlib, one JSON file of state, one bearer token.
Serves the UI (index.html/sw.js/manifest/icons, read from disk per request,
so UI edits need no restart) and a small REST API:

  GET    /api/todos               -> {"rev": N, "todos": [...]}  (list order = priority)
  POST   /api/todos               {"text": "...", "via": "cli", "list": "todo"} -> todo
  POST   /api/todos/<id>          {"done": true|false, "text": "...", "list": "..."} -> todo
  POST   /api/reorder             {"ids": [...]} new relative order  -> {"rev": N}
  DELETE /api/todos/<id>          -> {"ok": true}
  POST   /api/clear_done          {"list": "todo"} (optional scope)  -> {"removed": N}

Todos live on named lists ("list" field, default "todo"). The server is
list-agnostic — it just stores the string; the UI hardcodes which lists
exist as emoji tabs (✅ todo, 💼 work, 🛠️ builds) and more are added by
editing that one array in index.html.
  GET    /skill.md                -> pasteable agent instructions (embeds the token)

Auth, two lanes (same stance as the clawd-harness fleet UI):
  - machines: `Authorization: Bearer <token>` or `?t=<token>` — the token
    never goes to a phone.
  - humans: a passkey (WebAuthn / Face ID, rpId = TODO_RPID) traded for a
    7-day HttpOnly session cookie. Enrollment of a new device needs a
    one-time code, armed via POST /auth/arm_enroll (bearer-token only).

Endpoints: POST /auth/challenge, /auth/register, /auth/login, /auth/logout,
/auth/arm_enroll. Passkeys + sessions persist in .clawd-todo.auth.json.

Env: TODO_PORT (8794), TODO_HOST (127.0.0.1), TODO_TOKEN or
TODO_TOKEN_FILE (.clawd-todo.token, auto-generated), TODO_DATA (todos.json),
TODO_RPID (todo.atg.link), TODO_ORIGIN (https://todo.atg.link),
TODO_SESSION_TTL (604800, 7 days).
"""
import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding
    from cryptography.hazmat.primitives.serialization import load_der_public_key
    HAVE_CRYPTO = True
except ImportError:  # passkey endpoints degrade to 501; token auth still works
    HAVE_CRYPTO = False

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

# ---- passkey auth ------------------------------------------------------
RPID = os.environ.get("TODO_RPID", "todo.atg.link")
ORIGIN = os.environ.get("TODO_ORIGIN", "https://todo.atg.link")
SESSION_TTL = int(os.environ.get("TODO_SESSION_TTL", str(7 * 86400)))
AUTH_FILE = Path(os.environ.get("TODO_AUTH", HERE / ".clawd-todo.auth.json"))
ENROLL_TTL = 900
CHAL_TTL = 300

# in-memory only; short-lived
CHALLENGES = {}  # b64url challenge -> (purpose, exp)


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _ub64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _load_auth():
    try:
        a = json.loads(AUTH_FILE.read_text())
        assert isinstance(a.get("passkeys"), list)
        a.setdefault("sessions", {})
        a.setdefault("enroll", None)
        return a
    except Exception:
        return {"passkeys": [], "sessions": {}, "enroll": None}


AUTH = _load_auth()


def _save_auth():
    tmp = AUTH_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(AUTH, indent=1))
    tmp.chmod(0o600)
    tmp.replace(AUTH_FILE)


def _prune_auth():
    now = time.time()
    stale = [sid for sid, exp in AUTH["sessions"].items() if exp < now]
    for sid in stale:
        del AUTH["sessions"][sid]
    if AUTH["enroll"] and AUTH["enroll"]["exp"] < now:
        AUTH["enroll"] = None
    if stale:
        _save_auth()
    for c, (_, exp) in list(CHALLENGES.items()):
        if exp < now:
            del CHALLENGES[c]


def _new_session():
    sid = secrets.token_hex(24)
    AUTH["sessions"][sid] = time.time() + SESSION_TTL
    _save_auth()
    secure = "; Secure" if ORIGIN.startswith("https") else ""
    return (f"sid={sid}; Path=/; Max-Age={SESSION_TTL}; "
            f"HttpOnly; SameSite=Strict{secure}")


def _take_challenge(cdj: dict, purpose: str) -> bool:
    chal = cdj.get("challenge", "")
    got = CHALLENGES.pop(chal, None)
    return bool(got and got[0] == purpose and got[1] >= time.time()
                and cdj.get("origin") == ORIGIN)


def _auth_ok_ad(ad: bytes) -> bool:
    """rpIdHash matches and both User-Present + User-Verified bits set."""
    return (len(ad) >= 37
            and ad[:32] == hashlib.sha256(RPID.encode()).digest()
            and (ad[32] & 0x01) and (ad[32] & 0x04))


def _verify_sig(pk_entry, authdata: bytes, cdj_raw: bytes, sig: bytes) -> bool:
    data = authdata + hashlib.sha256(cdj_raw).digest()
    try:
        pub = load_der_public_key(base64.b64decode(pk_entry["spki"]))
        if pk_entry["alg"] == -7:
            pub.verify(sig, data, ec.ECDSA(hashes.SHA256()))
        elif pk_entry["alg"] == -257:
            pub.verify(sig, data, padding.PKCS1v15(), hashes.SHA256())
        else:
            return False
        return True
    except Exception:
        return False


# Pasteable instructions for ANY ai agent (no CLI dependency) — served at
# /skill.md behind auth, since it embeds the bearer token. __TOKEN__ and
# __ORIGIN__ are substituted per request.
SKILL_MD = """\
# Austin's todo list — agent access

You can read and edit Austin's personal todo list (one shared list, used from
his phone and by agents on many machines) over a small REST API.

Base URL: __ORIGIN__
Every call needs this header:

    Authorization: Bearer __TOKEN__

## Endpoints

- `GET /api/todos` -> `{"rev": N, "todos": [{"id", "text", "done", "created",
  "done_at", "via", "list"}, ...]}` — list order is Austin's priority order,
  top first. There are separate lists: `"todo"` (personal, the default —
  a missing "list" field means "todo"), `"work"`, and `"builds"` (things to
  build).
- `POST /api/todos` with `{"text": "buy milk", "via": "<your agent name>"}`
  -> the new todo (lands on top). Add `"list": "work"` or `"list": "builds"`
  to target those lists; omit for the personal list. Only target another
  list when Austin says it's a work item / a build — default to personal
  when unsure.
- `POST /api/todos/<id>` with `{"done": true}` to check off, `{"done": false}`
  to reopen, `{"text": "..."}` to edit, `{"list": "work"}` to move lists.
- `DELETE /api/todos/<id>` — delete.
- `POST /api/clear_done` with `{"list": "todo"}` — purge that list's finished
  items (omit "list" to purge every list).
- `POST /api/reorder` with `{"ids": ["<id>", ...]}` — new relative order.

Examples:

    curl -s -H "Authorization: Bearer __TOKEN__" __ORIGIN__/api/todos

    curl -s -X POST -H "Authorization: Bearer __TOKEN__" \\
         -H "Content-Type: application/json" \\
         -d '{"text": "test scrollback on phone", "via": "my-agent"}' \\
         __ORIGIN__/api/todos

## How to behave

- Keep items short and imperative ("test scrollback on phone"), one item per
  task; include the project name when it isn't obvious. Set "via" to your name.
- To check something off, find its id with GET /api/todos first.
- The order is Austin's priority order — don't reorder unless he asks.
- The list is Austin's, not yours: never clear or delete items you didn't
  just add unless he asks.
- Treat the token as a secret: don't echo it into logs, commits, or chat.
"""


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
    def _send(self, code, body, ctype="application/json", cookie=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _token_authed(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and secrets.compare_digest(auth[7:].strip(), TOKEN):
            return True
        q = parse_qs(urlparse(self.path).query)
        for t in q.get("t", []):
            if secrets.compare_digest(t, TOKEN):
                return True
        return False

    def _cookie_authed(self) -> bool:
        cookies = self.headers.get("Cookie", "")
        for part in cookies.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "sid" and AUTH["sessions"].get(v, 0) >= time.time():
                # cookie-authed browser writes must come from our own page
                origin = self.headers.get("Origin", "")
                return origin in ("", ORIGIN)
        return False

    def _authed(self) -> bool:
        _prune_auth()
        return self._token_authed() or self._cookie_authed()

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
            return self._send(200, f.read_bytes(), ctype)
        if path == "/api/todos":
            if not self._authed():
                return self._send(401, {"error": "bad token"})
            with LOCK:
                return self._send(200, {"rev": STATE["rev"], "todos": STATE["todos"]})
        if path == "/skill.md":
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            md = SKILL_MD.replace("__ORIGIN__", ORIGIN).replace("__TOKEN__", TOKEN)
            return self._send(200, md.encode(), "text/markdown; charset=utf-8")
        if path == "/healthz":
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith("/auth/"):
            return self._auth_route(path)
        if not path.startswith("/api/"):
            return self._send(404, {"error": "not found"})
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})

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
                "list": str(body.get("list", "todo")).strip()[:20] or "todo",
            }
            with LOCK:
                STATE["todos"].insert(0, todo)
                STATE["rev"] += 1
                _save_state()
            return self._send(200, todo)

        if path == "/api/reorder":
            ids = self._body().get("ids")
            if (not isinstance(ids, list) or len(ids) > 10000
                    or not all(isinstance(i, str) for i in ids)):
                return self._send(400, {"error": "ids must be a list of strings"})
            order = {tid: i for i, tid in enumerate(ids)}
            with LOCK:
                # Reorder the mentioned todos into the slots they already
                # occupy; anything unmentioned (races with a concurrent add,
                # done items the phone doesn't send) keeps its position.
                mentioned = sorted((t for t in STATE["todos"] if t["id"] in order),
                                   key=lambda t: order[t["id"]])
                it = iter(mentioned)
                STATE["todos"] = [next(it) if t["id"] in order else t
                                  for t in STATE["todos"]]
                STATE["rev"] += 1
                _save_state()
                return self._send(200, {"rev": STATE["rev"]})

        if path == "/api/clear_done":
            lst = str(self._body().get("list", "")).strip()
            with LOCK:
                before = len(STATE["todos"])
                STATE["todos"] = [
                    t for t in STATE["todos"]
                    if not (t["done"] and (not lst or t.get("list", "todo") == lst))]
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
                if "list" in body and str(body["list"]).strip():
                    todo["list"] = str(body["list"]).strip()[:20]
                STATE["rev"] += 1
                _save_state()
                return self._send(200, todo)

        return self._send(404, {"error": "not found"})

    def _auth_route(self, path):
        _prune_auth()
        body = self._body()

        if path == "/auth/arm_enroll":
            # bearer token ONLY — this is the machine-side act that lets a
            # new device enroll a passkey (mirrors the fleet's deliberate-act
            # registration rule).
            if not self._token_authed():
                return self._send(401, {"error": "token required"})
            code = secrets.token_hex(6)
            AUTH["enroll"] = {"code": code, "exp": time.time() + ENROLL_TTL}
            _save_auth()
            return self._send(200, {"url": f"{ORIGIN}/?enroll={code}",
                                    "expires_in": ENROLL_TTL})

        if not HAVE_CRYPTO:
            return self._send(501, {"error": "cryptography not installed"})

        if path == "/auth/challenge":
            purpose = body.get("purpose")
            if purpose == "register":
                e = AUTH["enroll"]
                if not (e and e["exp"] >= time.time()
                        and secrets.compare_digest(str(body.get("code", "")), e["code"])):
                    return self._send(403, {"error": "no valid enroll code"})
            elif purpose == "login":
                if not AUTH["passkeys"]:
                    return self._send(403, {"error": "no passkeys enrolled"})
            else:
                return self._send(400, {"error": "bad purpose"})
            chal = _b64u(secrets.token_bytes(32))
            CHALLENGES[chal] = (purpose, time.time() + CHAL_TTL)
            out = {"challenge": chal, "rpId": RPID}
            if purpose == "login":
                out["credIds"] = [p["id"] for p in AUTH["passkeys"]]
            return self._send(200, out)

        if path == "/auth/register":
            e = AUTH["enroll"]
            if not (e and e["exp"] >= time.time()
                    and secrets.compare_digest(str(body.get("code", "")), e["code"])):
                return self._send(403, {"error": "no valid enroll code"})
            try:
                cdj_raw = _ub64(body["clientDataJSON"])
                cdj = json.loads(cdj_raw)
                ad = _ub64(body["authenticatorData"])
                alg = int(body["alg"])
                spki = base64.b64decode(body["spki"])
                load_der_public_key(spki)  # must parse
            except Exception:
                return self._send(400, {"error": "bad credential payload"})
            if cdj.get("type") != "webauthn.create" or not _take_challenge(cdj, "register"):
                return self._send(403, {"error": "challenge/origin mismatch"})
            if not _auth_ok_ad(ad) or alg not in (-7, -257):
                return self._send(403, {"error": "authenticator rejected"})
            AUTH["passkeys"].append({
                "id": body["id"], "spki": base64.b64encode(spki).decode(),
                "alg": alg, "added": _now(),
                "label": str(body.get("label", ""))[:60]})
            AUTH["enroll"] = None
            cookie = _new_session()
            return self._send(200, {"ok": True}, cookie=cookie)

        if path == "/auth/login":
            pk = next((p for p in AUTH["passkeys"] if p["id"] == body.get("id")), None)
            if not pk:
                return self._send(403, {"error": "unknown credential"})
            try:
                cdj_raw = _ub64(body["clientDataJSON"])
                cdj = json.loads(cdj_raw)
                ad = _ub64(body["authenticatorData"])
                sig = _ub64(body["signature"])
            except Exception:
                return self._send(400, {"error": "bad assertion payload"})
            if cdj.get("type") != "webauthn.get" or not _take_challenge(cdj, "login"):
                return self._send(403, {"error": "challenge/origin mismatch"})
            if not _auth_ok_ad(ad) or not _verify_sig(pk, ad, cdj_raw, sig):
                return self._send(403, {"error": "verification failed"})
            cookie = _new_session()
            return self._send(200, {"ok": True}, cookie=cookie)

        if path == "/auth/logout":
            cookies = self.headers.get("Cookie", "")
            for part in cookies.split(";"):
                k, _, v = part.strip().partition("=")
                if k == "sid":
                    AUTH["sessions"].pop(v, None)
            _save_auth()
            return self._send(200, {"ok": True},
                              cookie="sid=; Path=/; Max-Age=0; HttpOnly")

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
