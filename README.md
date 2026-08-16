# clawd-todo

Austin's one shared todo list: a mobile PWA at **https://todo.atg.link** plus a
token-authed REST API, so every agent on every machine can read, add, and
check off items.

- **Phone**: open `https://todo.atg.link/?t=<token>` once, then Share → Add to
  Home Screen. The token persists in localStorage; the installed app opens
  straight to the list. Installable PWA (manifest + service worker,
  offline-readable shell).
- **Agents / any machine**: the `todo` CLI (`cli/todo`) + a Claude skill
  (`skill/todo/SKILL.md`). Run `./install_local.sh` on a machine to symlink
  the CLI to `~/bin/todo` and the skill into every Claude config dir
  (`~/.claude` and `~/.clawd-accounts/*`). Config: `~/.clawd-todo.env` with
  `TODO_URL` + `TODO_TOKEN`.
- **MCP** (optional): `mcp_server.py` is a stdio MCP server with the same four
  verbs — `claude mcp add todo -- python3 /path/to/mcp_server.py`. The skill +
  CLI path needs no per-account MCP registration, so prefer that.

## Server

`server.py` — pure Python stdlib, one process, port 8794 (loopback), state in
`todos.json` (atomic writes), token in `.clawd-todo.token` (auto-generated) or
`TODO_TOKEN`. Static files are read from disk per request, so UI edits deploy
without a restart.

API (all need `Authorization: Bearer <token>` or `?t=`):

```
GET    /api/todos            -> {"rev": N, "todos": [{id,text,done,created,done_at,via}]}
POST   /api/todos            {"text": "...", "via": "cli"}
POST   /api/todos/<id>       {"done": true} and/or {"text": "..."}
DELETE /api/todos/<id>
POST   /api/clear_done
GET    /healthz              (no auth)
```

## Deploy (the h.atg.link pattern)

Lives on the `clawd-nerve-cord` AWS box (`ssh zkllmapi`) at
`~/clawd-todo`, behind nginx + certbot:

- DNS: `todo.atg.link` A → 174.129.67.164 (Route53, same zone as h.atg.link)
- `deploy/clawd-todo.service` → systemd unit (`sudo systemctl enable --now clawd-todo`)
- `deploy/nginx-todo.atg.link` → `/etc/nginx/sites-available/todo.atg.link`
  (+ symlink into `sites-enabled`), then `sudo certbot --nginx -d todo.atg.link`
- `deploy/clawd-todo-pull.{service,timer}` → auto-deploy: **push to main = deploy**
  (~3 min; restarts the server only when `server.py` changed)

Icons are generated (`python3 tools/gen_icon.py`) and committed.
