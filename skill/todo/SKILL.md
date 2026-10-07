---
name: todo
description: Austin's personal todo list (todo.atg.link). Use whenever the user says "add that to my todo list", "put it on my list", asks "what's on my todo list / what should I do today / what's next", or wants to check something off, mark it done, or put it on today. Also for wrapping up a session with a follow-up task the user should do later.
---

# Austin's todo list

One shared app, hosted at https://todo.atg.link, used from his phone and by
agents on every machine. Talk to it with the `todo` CLI. If `todo` is not on
PATH, use `~/bin/todo`. Config lives in `~/.clawd-todo.env` (TODO_URL +
TODO_TOKEN); if that file is missing on this machine, the token is in the
credential store under "clawd-todo".

## What the app looks like (so you know what Austin means)

- **Tabs across the top, one emoji each:**
  - ✅ **todo** — personal, the default (list name `todo`)
  - 💼 **work** — work items (list name `work`)
  - 🛠️ **builds** — things to build (list name `builds`)
  - then **one tab per clawd-harness iron** (📋 🔏 🎬 🦾 …) — each iron's
    shared to-do list, live from the harness. Those items belong to the
    iron, not to Austin's lists (see "Iron tabs" below).
- **The ☀️ today line.** Every tab has a "☀️ today" divider. Items Austin
  drags *above* it are what he means to get done **today** — his front
  burner, the highest priority, shown first. Everything below is the
  backlog in his drag order (top = most important). New items land just
  below the line, never on today.

## Commands

```bash
todo                       # all open items, every list (personal unlabeled; others show [work]/[builds])
todo today                 # ☀️ today's items — every list AND every iron tab
todo today <id|words>      # put an item above the ☀️ line (only when Austin asks)
todo later <id|words>      # take it back off today
todo add <text>            # add to the personal list — plain words, no quotes
todo add -l work <text>    # add to 💼 work   (-l builds → 🛠️ builds)
todo -l work               # just one list (works with list / today / clear too)
todo done <id|words>       # check off (unique substring of the text works)
todo undone <id>           # reopen
todo rm <id|words>         # delete
todo list --all            # include finished items
todo clear [-l <list>]     # purge finished items (only if Austin asks)
todo enroll                # one-time link (15 min) to add a new device's Face ID
```

If Austin says the app is locked out on his phone or he got a new device,
run `todo enroll` and give him the printed link — and tell him to open it in
Safari (long-press → copy → paste), not an in-app browser: Telegram's has no
Face ID support.

## How to behave

- **"What should I do today / what's next"**: run `todo today` first and lead
  with those ☀️ items — that's Austin's own pick for today. Then, if he wants
  more, `todo` for the rest in his order. If you have context, say which one
  fits now.
- **Adding**: keep items short and imperative ("test scrollback on phone"),
  one item per task. When the user says "add that", derive the item from the
  work just discussed — include the project name if it isn't obvious from
  the text. Personal list by default; `-l work` / `-l builds` only when
  Austin says it's a work item / something to build. Confirm with the CLI's
  one-line output.
- **Today**: only put something on ☀️ today when Austin says so ("put that
  on today", "that's for today", "make it a priority today"). `todo later`
  when he says it can wait. Never decide his day for him.
- **Checking off**: `todo done` with a distinctive word from the item is
  enough; if the CLI says ambiguous, list and use the id.
- **Order is his**: he drag-orders in the app — don't reorder unless he asks
  (the API has POST /api/reorder if he does).
- The list is Austin's, not yours: never clear or delete items you didn't
  just add unless he asks.

## Iron tabs

The emoji tabs after 🛠️ are clawd-harness **irons** (named groups of
projects). Their items live on the harness relay, not in todo.atg.link;
the app just shows them, and Austin can drag them above a ☀️ line there
too — `todo today` includes those, tagged `[iron: <title>]`.

- To read or write an iron's list from inside a harness session, use the
  `iron-todo` skill (`harness-todo` CLI) — that's its home.
- Never copy or move items between an iron and Austin's personal lists.
- From outside the harness, the raw API is `GET /api/irons` (all irons with
  their items) and `POST /api/irons` with
  `{"iron": "<id>", "op": "add"|"done"|"undone"|"rm", "text"|"ref": ...}`.

## Raw API (no CLI)

An agent on a machine WITHOUT this skill/CLI can be handed the pasteable
instructions from `GET /skill.md` (authed) — same text the app's
"🤖 agent instructions" footer shows. Key bits: `GET /api/todos` (each todo
has `list` and `today`), `POST /api/todos` `{"text", "via", "list"}`, and a
one-item `POST /api/reorder` `{"ids": [id], "today": [id]}` (or `"today": []`)
flips ☀️ without moving anything.
