#!/usr/bin/env python3
"""clawd-todo MCP server (stdio) — optional alternative to the `todo` CLI.

Register with:  claude mcp add todo -- python3 /path/to/mcp_server.py
Config comes from ~/.clawd-todo.env (TODO_URL, TODO_TOKEN), like the CLI.

Newline-delimited JSON-RPC over stdio; tools: todo_list, todo_add,
todo_done, todo_remove. Pure stdlib.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

ENV_FILE = Path.home() / ".clawd-todo.env"
cfg = {}
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"')
URL = (os.environ.get("TODO_URL") or cfg.get("TODO_URL", "https://todo.atg.link")).rstrip("/")
TOKEN = os.environ.get("TODO_TOKEN") or cfg.get("TODO_TOKEN", "")

TOOLS = [
    {"name": "todo_list", "description": "List Austin's todo items. Returns open items unless all=true.",
     "inputSchema": {"type": "object", "properties": {"all": {"type": "boolean"}}}},
    {"name": "todo_add", "description": "Add an item to Austin's todo list.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "todo_done", "description": "Check off a todo item by id or a unique substring of its text.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "todo_remove", "description": "Delete a todo item by id or unique substring.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
]


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method=method, headers={
        "Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def resolve(query, pool):
    byid = [t for t in pool if t["id"].startswith(query.lower())]
    if len(byid) == 1:
        return byid[0], None
    bytext = [t for t in pool if query.lower() in t["text"].lower()]
    if len(bytext) == 1:
        return bytext[0], None
    hits = byid or bytext
    if not hits:
        return None, f"nothing matches {query!r}"
    return None, "ambiguous: " + "; ".join(f"{t['id']} {t['text']}" for t in hits[:6])


def run_tool(name, args):
    if name == "todo_list":
        items = api("GET", "/api/todos")["todos"]
        if not args.get("all"):
            items = [t for t in items if not t["done"]]
        if not items:
            return "nothing to do"
        return "\n".join(f"[{'x' if t['done'] else ' '}] {t['id']}  {t['text']}" for t in items)
    if name == "todo_add":
        t = api("POST", "/api/todos", {"text": args["text"], "via": "mcp"})
        return f"added {t['id']}  {t['text']}"
    if name in ("todo_done", "todo_remove"):
        pool = api("GET", "/api/todos")["todos"]
        if name == "todo_done":
            pool = [t for t in pool if not t["done"]]
        t, err = resolve(args["query"], pool)
        if err:
            return err
        if name == "todo_done":
            api("POST", "/api/todos/" + t["id"], {"done": True})
            return f"done: {t['text']}"
        api("DELETE", "/api/todos/" + t["id"])
        return f"deleted: {t['text']}"
    return f"unknown tool {name}"


def reply(mid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": mid}
    if error:
        msg["error"] = {"code": -32000, "message": error}
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        m = json.loads(line)
    except Exception:
        continue
    method, mid = m.get("method"), m.get("id")
    if method == "initialize":
        reply(mid, {"protocolVersion": m["params"].get("protocolVersion", "2025-06-18"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "clawd-todo", "version": "1.0"}})
    elif method == "tools/list":
        reply(mid, {"tools": TOOLS})
    elif method == "tools/call":
        try:
            text = run_tool(m["params"]["name"], m["params"].get("arguments") or {})
            reply(mid, {"content": [{"type": "text", "text": text}]})
        except Exception as e:
            reply(mid, {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})
    elif mid is not None:
        reply(mid, {})
