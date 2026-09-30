---
name: beads
description: TunaTale's bd (beads) conventions and traps — JSON field shapes, dependency-edge direction, parent/child blocking, sync.sh and stealth mode, agent mail, epics, and where briefs live. Load before any bd write (create, update, dep, close, comment), before running sync.sh or mail.sh, and before writing a dispatch brief.
---

# bd (beads) in TunaTale

Moved verbatim from AGENTS.md (2026-09-30), which keeps a five-line summary.

## Issue Tracking with bd (beads)

The BP dispatch backlog and its dependency ordering live in **bd (beads)**, not
in prose queue tables. This block is guidance, not permission to override
repository, user, or orchestrator instructions.

```bash
bd ready --exclude-type=epic                  # unblocked work (epics are containers, not work)
bd ready --parent <epic> --exclude-type=epic  # ...scoped to one theme
bd show <id> --json | jq -r '.[0].description'    # ALWAYS --json; see bugs below
bd create "title" -d "..." -p 0-4             # 0 critical .. 4 backlog
bd dep add <child> <parent>                   # child is blocked by parent
bd update <id> --claim  /  bd close <id> --reason "..."
bd graph --all --html   /  --compact          # browse the backlog, edges included
```

Full reference: `bd --help`, and **`bd prime` — run it by hand** whenever you
want the current command and flag reference. It is authoritative and
self-updating in a way this hand-written section can never be, and it is only
~100 lines.

**Do not wire it into a SessionStart hook.** The objection is mechanism, not
content: auto-injected, its "Core Rules" block arrives as authoritative context
every session, and the predicted drift is an agent that quietly stops writing
briefs and stops committing. ⚠️ **That drift is a prediction and has never been
measured.** The ban stands on asymmetry — running it by hand costs nothing, and
the failure it guards against would be silent.

⚠️ **Two of bd's own "Core Rules" conflict with this repo's** (audited against
the real output; re-audit if bd's rules change):
- **"Create beads issue BEFORE writing code"** — a real conflict, and ours is
  the sloppier side: most commits here have no bead.
- **"Do NOT use MEMORY.md"** — a real conflict where we are right for this
  situation. Its stated reason is that they "fragment across accounts"; this is
  one user on one machine, and MEMORY.md is the harness's own system, not a
  choice bd can override.

Its other rules look like conflicts and are not: tracking does live in bd
(`.beads-tasks/briefs/` holds documents, not a task list), and "stealth mode (no
git ops)" is bd echoing OUR OWN config — a privacy guard, see below.

⚠️ **bd's JSON is not one shape, and a wrong field name returns a clean negative
rather than an error.** Verify any field against a record whose answer you
already know before believing a count. All three of these produced confident
wrong conclusions on 2026-08-18:
- the field is **`parent`**, not `parent_id`;
- **`dependency_count` counts only `blocks` edges** while the `dependencies`
  array also carries `parent-child` and `discovered-from` — they disagree on 47
  of 58 open issues, which looks exactly like a bug and is not one;
- `bd show --json` uses a different shape again from `bd list` / `bd export`.

⚠️ **A `parent-child` edge BLOCKS the child when the parent is a `task`, but not
when it is an `epic`** (verified 2026-08-24). A task parented to another open
task drops out of `bd ready` and appears in `bd blocked` as *"Blocked by 1 open
dependencies: [<parent>]"*. Epic children are unaffected — which is why
`bd ready --exclude-type=epic` works at all, and why this goes unnoticed.
Parenting a task to an open task **while also** adding `bd dep add <parent>
<child>` creates a mutual block and the child is silently invisible; the symptom
is a bead you just filed never appearing in `bd ready`. Use an epic as the
container, or express the ordering with a `blocks` edge alone and leave the
parent unset.

⚠️ **`bd comment` succeeds SILENTLY — no output is not failure.** Retrying on the
apparent silence posts it twice (done, 2026-08-24). Confirm with the
`comment_count` field; `.comments` comes back empty and is not the field, which
makes the wrong check look like a confirmed failure. Same clean-negative class as
the JSON-shape traps above.

⚠️ **Two upstream bugs, hands-on verified — do not rediscover them blind:**
- plain `bd show <id>` mangles technical content (strips code fences, garbles
  `Promise<boolean>` into `Promise****`). The stored data is fine; only the
  pretty-printer is broken. Always `--json | jq`. (gastownhall/beads#5495)
- `bd dep add <child> <parent>` and `bd create --deps blocks:<id>` are inverses:
  `--deps blocks:X` means "this new issue blocks X," not "is blocked by X."
  Verify every edge right after wiring it.
<!-- END BEADS INTEGRATION -->

## Beads + TunaTale specifics

- **Sync is standing-authorized — after any `bd create` / `close` / `dep add`
  batch run `./.beads-tasks/sync.sh`, do not ask.** `.beads/` is stealth-mode
  and its Dolt backups sit on the same disk, so nothing leaves this machine until
  that script runs. It pushes the Dolt store, exports, renders `GRAPH.md`,
  commits and pushes.
  ⚠️ **A bd WRITE is freeze-safe; a bd SYNC is not.** `.beads/` is gitignored, so
  `bd create` / `close` / `comment` leave `git status` untouched and are safe
  while another agent holds uncommitted work. `sync.sh` COMMITS AND PUSHES the
  parent repo, so running it then lands a commit under that agent and moves HEAD
  beneath their gate fingerprint. Standing authorisation means you need not ask;
  it does not mean any moment is safe. In a two-agent session, sync after the
  other agent's commit lands, not during their freeze. (Learned 2026-09-13, when
  the peer session deliberately wrote a bd comment mid-freeze — correctly — and
  deliberately did NOT sync.)
- **Stealth mode (`no-git-ops`) is a privacy guard, not a preference.** `.beads/`
  must sit at the PARENT repo root (bd stops walking up at a git root, so from
  inside `.beads-tasks/` it never finds `../.beads`) — and that root's origin is
  the PUBLIC repo. With bd's automatic git operations on, they would write
  `refs/dolt/data` to the public remote, and **a hidden ref is unlisted, not
  private**. It costs nothing: stealth suppresses only *automatic* git ops, so
  `bd dolt push` via `sync.sh` still works. Full reasoning:
  `.beads-tasks/briefs/design-beads-github-sync-2026-08.md` § Appendix B.
- **The backlog is private, and the Dolt remote is the whole mechanism.** The
  store travels as the hidden ref `refs/dolt/data` on the **private**
  tunatale-tasks repo — deliberately NOT this repo's origin, which is public.
  **A hidden ref is unlisted, not private**; anything on a public remote is
  world-fetchable. `sync.sh` refuses to push if the remote ever stops containing
  `tunatale-tasks` — never relax that guard. `bd-export.jsonl` is a secondary
  human-diffable copy; restore from the Dolt ref, not the JSONL.
- **Browse with `npx beads-ui start`** (mantoni/beads-ui; serves
  `http://127.0.0.1:3000`, talks to the `bd` CLI so it reads the live store, and
  never leaves localhost — verified working 2026-08-18). `bd graph <epic>
  --compact` is the terminal equivalent. ⚠️ **`bd graph --all --html`
  degenerates on this backlog and should not be the default suggestion.** Every
  node is colored by status and nearly everything here is `open`; 39 of 52 open
  issues sit at layer 0, and `forceX(150 + layer*220)` pulls all 39 identical
  blue 130×40 rects into one column with `forceCollide(50)` — a solid blue slab,
  not a graph. Two further defects, both measured 2026-08-18: its help calls the
  HTML "self-contained" and that is FALSE (it pulls d3 from `https://d3js.org`,
  so it needs the network and cannot be published as an Artifact without inlining
  d3), and `bd graph <id> --html` does NOT scope — it still emitted all 52 nodes
  for a 22-issue epic, though it did recompute the layers. `--compact` scopes
  correctly.
  ⚠️ **How this got into a doc is the lesson:** the adoption control counted
  nodes and edges (52/57, agreeing with `--dot` and `bd list`) and never looked
  at the rendered picture. The data was right and the view was unusable —
  a control has to test the property you are actually claiming.
  The GitHub Issues mirror was retired 2026-08-18 (`tunatale-93s`; rationale in
  `cc7ddea`) and the tab is disabled.
- **A fresh clone has no bd data — run `./beads-bootstrap.sh`.** A default clone
  fetches only `refs/heads/*` and `refs/tags/*`, and bd's auto-detection looks at
  *this* repo's origin — the wrong repo, on purpose. Needs read access to the
  private tasks repo; without it the clone step fails, which is expected.
  **An empty backlog after bootstrap is a red flag, not "nothing to do"** — the
  script exits non-zero on 0 open issues for exactly that reason.
- **Agent mail lives in bd** — `./.beads-tasks/mail.sh inbox` / `read <id>` /
  `MAIL_ID=orch ./.beads-tasks/mail.sh send peer "subject" "body"` — for talking
  to another Claude session sharing this tree. `unread == open`, addressing is a
  `to-*` label, and message beads are excluded from `bd list` / `bd ready` /
  `GRAPH.md`. A `SessionStart` hook surfaces unread mail; without it a session
  never looks. ⚠️ **Mail is in neither `bd-export.jsonl` nor the Dolt push** — it
  is the one thing here with no off-machine copy. Anything that must survive
  belongs in an issue.
- **Related issues get an epic, once a theme produces more than two.** `bd ready`
  sorts by priority across the WHOLE backlog, so loose siblings scatter across
  unrelated work and the reader cannot see they are one piece. Retrofit the
  moment you notice you are creating the third (`bd update <id> --parent <epic>`
  reparents, so there is no cost to doing it late). The epic carries the
  through-line, the ordering rationale, and anything explicitly OUT of scope —
  the cheapest place to stop an executor widening the work. It does not restate
  its children. `tunatale-vnf` is the reference shape.
- **Method vs work.** `.beads-tasks/DISPATCH-PREAMBLE.md` holds what binds
  *every* delegated run (fence, prohibitions, escalation, report contract). An
  issue holds only what is true of *that* task. Never restate the preamble inside
  an issue — two copies drift, and the one the executor read is the one you did
  not edit.
- **Short work inline, long briefs as files.** A screenful goes in the issue
  description. Anything longer, or carrying a big oracle table, goes in
  `.beads-tasks/briefs/` — genre-prefixed (`brief-`, `findings-`, `testplan-`,
  `handoff-`, `design-`) — with the issue holding scope, `Source: <path> §
  <section>`, and the decisive oracles. Never both. `.beads-tasks/archive/` holds
  docs whose work shipped and which exist nowhere else. Prefer a file for length
  and citability, NOT because bd edits are unreviewable — they are versioned in
  `bd-export.jsonl`, and the prose diff is one command:
  ```bash
  desc () { git show "$1:bd-export.jsonl" | jq -r --arg id "$2" 'select(.id==$id).description'; }
  diff <(desc HEAD~1 tunatale-xyz) <(desc HEAD tunatale-xyz)
  ```
  `docs/briefs/` is retired: gitignored *permanence* is what killed it, not
  files. Do not author anything new there.
- **Closing a bd issue is not authorization to commit.** It records that the
  described work is done; the `./test.sh` gate and the diff audit still stand
  between that and anything shipping.
- **The `.beads-tasks` pointer is auto-staged — there is nothing to remember.**
  `.claude/hooks/stage_submodule_pointer.py` stages it onto any commit already
  carrying other content; every guard fails open, and it never manufactures a
  pointer-only commit. Order-independent with the commit gate, because
  `ignore = all` keeps the gitlink out of what the gate fingerprints. Check drift
  with `git submodule status` (leading `+` = behind, space = current).
  ⚠️ `ignore = all` also hides the gitlink from `git diff` / `git status` **even
  when it is staged** — pass `--ignore-submodules=none`, or you will conclude the
  hook silently no-opped when it did not. Closures land at most one commit late
  by construction: a close cites the hash of the commit that shipped it, so it
  cannot be inside that commit. Do not try to engineer that away.
- **Why a submodule and not a sibling clone:** the BP fence blocks reads outside
  the project directory, and an outside path does not error — it ends the run at
  exit 0 with zero files changed. Dispatch docs must be reachable *inside* the
  checkout, and the backlog must stay private. `git submodule update --init` 403s
  for anyone else; expected, breaks nothing.
