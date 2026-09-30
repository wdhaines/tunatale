---
name: beads
description: TunaTale's bd (beads) conventions and traps — JSON field shapes, dependency-edge direction, parent/child blocking, sync.sh and stealth mode, agent mail, epics, and where briefs live. Load before any bd write (create, update, dep, close, comment), before running sync.sh or mail.sh, and before writing a dispatch brief.
---

# bd (beads) in TunaTale

The BP dispatch backlog and its dependency ordering live in bd, not in prose
queue tables.

```bash
bd ready --exclude-type=epic                  # unblocked work (epics are containers, not work)
bd ready --parent <epic> --exclude-type=epic  # ...scoped to one theme
bd show <id> --json | jq -r '.[0].description'    # always --json (see traps)
bd create "title" -d "..." -p 0-4             # 0 critical .. 4 backlog
bd dep add <child> <parent>                   # child is blocked by parent
bd update <id> --claim  /  bd close <id> --reason "..."
bd graph <epic> --compact                     # terminal view of one theme
```

`bd prime` prints bd's own current command and flag reference (about 100 lines)
and is the authoritative reference; run it by hand when you need it. Don't wire
it into a SessionStart hook: its "Core Rules" block would arrive as
authoritative context every session, and two of those rules conflict with this
repo's.

- "Create a beads issue before writing code" — most commits here have no bead,
  and this repo's rule is the looser one.
- "Do not use MEMORY.md" — its reason (memory fragmenting across accounts) does
  not apply to one user on one machine, and MEMORY.md belongs to the harness.

Its other rules only look like conflicts: tracking does live in bd
(`.beads-tasks/briefs/` holds documents, not tasks), and "stealth mode (no git
ops)" is bd echoing this repo's own configuration (below).

## Traps — a wrong query returns a clean negative, not an error

Before believing a count, check the field against a record whose answer you
already know.

- Plain `bd show` mangles technical content (strips code fences, turns
  `Promise<boolean>` into `Promise****`). The stored data is fine; use
  `--json | jq`. Upstream: gastownhall/beads#5495.
- JSON shapes differ: `bd show --json` is not the shape of `bd list` /
  `bd export`. The parent field is `parent`, not `parent_id`. `dependency_count`
  counts only `blocks` edges, while the `dependencies` array also holds
  `parent-child` and `discovered-from` edges, so the two disagree on most issues
  by design.
- `bd dep add <child> <parent>` and `bd create --deps blocks:<id>` point in
  opposite directions: `--deps blocks:X` means "this new issue blocks X". Verify
  every edge right after wiring it.
- A `parent-child` edge blocks the child when the parent is a `task`, not when it
  is an `epic`. A task parented to an open task drops out of `bd ready` and shows
  in `bd blocked`. Parenting a task to a task and also running
  `bd dep add <parent> <child>` creates a mutual block, and the child silently
  never appears in `bd ready`. Use an epic as the container, or express ordering
  with a `blocks` edge and leave the parent unset.
- `bd comment` succeeds silently, so retrying on the silence posts twice. Confirm
  with the `comment_count` field (`.comments` comes back empty and is not the
  field).

## Sync, and keeping the backlog private

- After any `bd create` / `close` / `dep add` batch, run `./.beads-tasks/sync.sh`
  without asking. `.beads/` is in stealth mode and its Dolt backups sit on the
  same disk, so nothing leaves this machine until that script runs. It pushes the
  Dolt store, exports, renders `GRAPH.md`, commits and pushes.
- A bd write is safe while another agent holds uncommitted work: `.beads/` is
  gitignored, so `git status` is untouched. A sync is not, because `sync.sh`
  commits and pushes the parent repo, landing a commit under that agent and
  moving HEAD beneath their gate fingerprint. Standing authorization means you
  need not ask, not that every moment is safe — in a two-agent session, sync
  after the other agent's commit lands.
- Stealth mode (`no-git-ops`) is a privacy guard. `.beads/` must sit at the
  parent repo root (bd stops walking up at a git root, so from inside
  `.beads-tasks/` it never finds `../.beads`), and that root's origin is the
  public repo. With bd's automatic git operations on, it would write
  `refs/dolt/data` to the public remote, and a hidden ref is unlisted, not
  private. Stealth suppresses only automatic git operations, so `bd dolt push`
  through `sync.sh` still works. Full reasoning:
  `.beads-tasks/briefs/design-beads-github-sync-2026-08.md` § Appendix B.
- The Dolt store travels as the hidden ref `refs/dolt/data` on the private
  tunatale-tasks repo, deliberately not this repo's public origin. `sync.sh`
  refuses to push if the remote stops containing `tunatale-tasks`; never relax
  that guard. `bd-export.jsonl` is a secondary, human-diffable copy — restore from
  the Dolt ref, not the JSONL.
- A fresh clone has no bd data: run `./beads-bootstrap.sh`. A default clone
  fetches only branches and tags, and bd's auto-detection looks at this repo's
  origin, which is the wrong repo on purpose. The script needs read access to the
  private tasks repo, and it exits non-zero on 0 open issues because an empty
  backlog after bootstrap means the restore failed, not that there is nothing to
  do.

## Browsing

`npx beads-ui start` (mantoni/beads-ui) serves `http://127.0.0.1:3000`, reads the
live store through the `bd` CLI, and stays on localhost. `bd graph <epic>
--compact` is the terminal equivalent.

`bd graph --all --html` is not usable on this backlog. It colours nodes by status
and nearly everything is `open`, and most open issues share layer 0, so the
layout collapses into one column of identical boxes. Its help calls the HTML
self-contained, but it loads d3 from `https://d3js.org` (so it needs the network
and cannot be published as an Artifact without inlining d3), and
`bd graph <id> --html` does not scope to the epic. When you evaluate a view like
this, look at the rendered result: matching node and edge counts say nothing about
whether it is readable.

## Claiming work

Claim a bead when you start working on it, with your location as the actor:

```bash
bd update <id> --claim --actor "$(basename "$PWD")@$(git branch --show-current)"
bd list --status in_progress --json | jq -r '.[] | "\(.id) \(.assignee) \(.title)"'
```

Every session on this machine shares one git identity, so a bare `--claim`
records the same assignee for all of them. The `worktree@branch` actor (e.g.
`tunatale-wt-dvdm2@feat/dvdm2-listen-production-note`) says both who holds the
bead and where its uncommitted work lives — which is what another session needs
before it switches branches in, gates, or syncs a shared tree. A claim held by a
different actor is refused (`issue already claimed by …`), and that refusal is the
point: read the message rather than the exit status, which a pipe can mask. Check
`bd list --status in_progress` before starting work, and close what you claimed
when it merges. The SessionStart hook lists current claims.

## Agent mail

Mail is how a session ending its window hands off to its successor: it persists
across sessions (each message is a bead in the local Dolt store), so the next
session sees it through the SessionStart hook. For a session that is still
running, use Claude Code's cross-session messages instead (`ListAgents` /
`SendMessage`), which are delivered live but only reach sessions that exist.

`./.beads-tasks/mail.sh inbox` / `read <id>` / `peek <id>` (reads without marking
read) / `MAIL_ID=orch ./.beads-tasks/mail.sh send peer "subject" "body"`. Unread
means open, addressing is a `to-*` label, and message beads are excluded from
`bd list`, `bd ready` and `GRAPH.md`. The SessionStart hook shows the unread count
and the newest three. Once you have taken over a handoff, mark it read with
`read <id>` — `peek` leaves it open, and unread handoffs pile up in every later
session's listing. Mail is in neither `bd-export.jsonl` nor the Dolt push, so it
has no off-machine copy; that's fine for handoffs, which are picked up within
hours, but anything that must outlive them belongs in an issue.

## Organising work

- **Give related issues an epic once a theme produces more than two.** `bd ready`
  sorts by priority across the whole backlog, so loose siblings scatter and
  nobody sees they are one piece; `bd update <id> --parent <epic>` reparents at
  no cost. The epic carries the through-line, the ordering rationale, and
  anything explicitly out of scope (the cheapest place to stop an executor
  widening the work). It does not restate its children. `tunatale-vnf` is the
  reference shape.
- **Method vs work.** `.beads-tasks/DISPATCH-PREAMBLE.md` holds what binds every
  delegated run (fence, prohibitions, escalation, report contract). An issue
  holds only what is true of that task; never restate the preamble inside one,
  because two copies drift.
- **Short work inline, long briefs as files.** A screenful goes in the issue
  description. Anything longer, or carrying a big oracle table, goes in
  `.beads-tasks/briefs/` with a genre prefix (`brief-`, `findings-`, `testplan-`,
  `handoff-`, `design-`), and the issue holds the scope, `Source: <path> §
  <section>`, and the decisive oracles — never both. `.beads-tasks/archive/`
  holds docs whose work shipped and which exist nowhere else. Description edits
  are versioned in `bd-export.jsonl`, and the prose diff is one command:
  ```bash
  desc () { git show "$1:bd-export.jsonl" | jq -r --arg id "$2" 'select(.id==$id).description'; }
  diff <(desc HEAD~1 tunatale-xyz) <(desc HEAD tunatale-xyz)
  ```
  `docs/briefs/` is retired (it was gitignored, so it had no history); do not
  author anything new there.
- **Closing an issue is not authorization to commit.** It records that the
  described work is done; the `./test.sh` gate and the diff audit still stand
  between that and anything shipping.

## The `.beads-tasks` submodule

- The pointer is auto-staged onto any commit that already carries other content
  (`.claude/hooks/stage_submodule_pointer.py`), so there is nothing to remember.
  Check drift with `git submodule status` (a leading `+` means behind, a space
  means current). `ignore = all` hides the gitlink from `git diff` and
  `git status` even when it is staged, so pass `--ignore-submodules=none` before
  concluding the hook did nothing. A close cites the commit that shipped the
  work, so closures land at most one commit late; that is by construction.
- It is a submodule rather than a sibling clone because the BP fence blocks reads
  outside the project directory, and an outside path does not error — it ends
  the run at exit 0 with nothing changed. Dispatch docs must be reachable inside
  the checkout while the backlog stays private. `git submodule update --init`
  returns 403 for anyone without access; that is expected and breaks nothing.
- The GitHub Issues mirror is retired and its tab disabled; bd is the only
  tracker.
