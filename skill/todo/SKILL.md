---
name: todo
description: Austin's personal todo list (todo.atg.link). Use whenever the user says "add that to my todo list", "put it on my list", asks "what's on my todo list / what should I do next", or wants to check something off / mark it done. Also for wrapping up a session with a follow-up task the user should do later.
---

# Austin's todo list

One shared list, hosted at https://todo.atg.link, used from his phone and by
agents on every machine. Talk to it with the `todo` CLI:

```bash
todo                    # list open items (each line: [ ] <id>  <text>)
todo add <text>         # add an item — plain words, no quotes needed
todo done <id|words>    # check off (unique substring of the text works)
todo undone <id>        # reopen
todo rm <id|words>      # delete
todo list --all         # include finished items
todo clear              # purge finished items
todo enroll             # one-time link (15 min) to add a new device's Face ID
```

If Austin says the todo app is locked out on his phone or he got a new
device, run `todo enroll` and give him the printed link.

If `todo` is not on PATH, use `~/bin/todo`. Config lives in
`~/.clawd-todo.env` (TODO_URL + TODO_TOKEN); if that file is missing on this
machine, the token is in the credential store under "clawd-todo".

## How to behave

- **Adding**: keep items short and imperative ("test scrollback on phone"),
  one item per task. When the user says "add that", derive the item from the
  work just discussed — include the project name if it isn't obvious from
  the text. Confirm with the one-line output.
- **Checking off**: `todo done` with a distinctive word from the item is
  enough; if the CLI says ambiguous, list and use the id.
- **Reading**: `todo` prints open items in Austin's priority order, top item
  first (he drag-orders them in the app — don't reorder unless he asks; the
  API has POST /api/reorder if he does). When asked "what's next", show the
  list and, if you have context, suggest which one fits now.
- An agent on a machine WITHOUT this skill/CLI can be handed the pasteable
  instructions from `GET /skill.md` (authed) — same text the app's
  "🤖 agent instructions" footer shows.
- The list is Austin's, not yours: never clear or delete items you didn't
  just add unless he asks.
