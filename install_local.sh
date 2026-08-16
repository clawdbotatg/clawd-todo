#!/bin/bash
# Install the `todo` CLI + Claude skill on THIS machine (Mac or Linux).
# Idempotent — symlinks back into this repo, so `git pull` updates everything.
#
# Prereq: ~/.clawd-todo.env with TODO_URL + TODO_TOKEN (chmod 600).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"

# CLI -> ~/bin/todo (plus /usr/local/bin if writable)
mkdir -p "$HOME/bin"
ln -sf "$HERE/cli/todo" "$HOME/bin/todo"
chmod +x "$HERE/cli/todo"
echo "CLI: ~/bin/todo"
if [ -w /usr/local/bin ]; then
  ln -sf "$HERE/cli/todo" /usr/local/bin/todo
  echo "CLI: /usr/local/bin/todo"
fi

# Skill -> every Claude config dir on this machine
link_skill() {
  mkdir -p "$1/skills"
  ln -sfn "$HERE/skill/todo" "$1/skills/todo"
  echo "skill: $1/skills/todo"
}
[ -d "$HOME/.claude" ] && link_skill "$HOME/.claude"
for d in "$HOME"/.clawd-accounts/*/; do
  [ -d "$d" ] && link_skill "${d%/}"
done

[ -f "$HOME/.clawd-todo.env" ] || echo "WARNING: ~/.clawd-todo.env missing (TODO_URL + TODO_TOKEN)"
echo "done. try: todo list"
