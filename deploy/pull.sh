#!/bin/bash
# Auto-deploy: pull main if clean+behind; restart only when server.py changed.
# (index.html/sw.js/manifest are read from disk per request — no restart needed.)
set -e
cd /home/ubuntu/clawd-todo
git fetch -q origin main
[ -n "$(git status --porcelain -uno)" ] && { echo "dirty tree, skipping"; exit 0; }
LOCAL=$(git rev-parse HEAD); REMOTE=$(git rev-parse origin/main)
[ "$LOCAL" = "$REMOTE" ] && exit 0
CHANGED=$(git diff --name-only "$LOCAL" "$REMOTE")
git merge --ff-only origin/main
echo "pulled $REMOTE"
if echo "$CHANGED" | grep -q '^server.py$'; then
  sudo systemctl restart clawd-todo
  echo "restarted clawd-todo (server.py changed)"
fi
