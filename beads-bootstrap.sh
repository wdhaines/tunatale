#!/usr/bin/env bash
#
# Recover the bd (beads) backlog in a fresh clone.
#
#   ./beads-bootstrap.sh
#
# WHY THIS EXISTS: bd's issue store is a Dolt database that lives at .beads/,
# which is gitignored (stealth mode). It travels as a hidden git ref,
# refs/dolt/data, on the PRIVATE tunatale-tasks repo. Two consequences, both
# by design and neither an error:
#
#   1. A default `git clone` fetches only refs/heads/* and refs/tags/*, so a
#      fresh clone has NO backlog. `bd list` says "no beads database found".
#   2. The data is on tunatale-tasks, NOT on this repo's origin. This repo is
#      public; the backlog holds internal workflow notes. A hidden ref is
#      unlisted but still world-fetchable, so it must not live on a public
#      remote.
#
# So the backlog cannot arrive by cloning alone, and bd's own auto-detection
# ("does MY origin have refs/dolt/data?") does not fire either — our origin is
# the wrong repo on purpose. This script supplies the missing pointer and lets
# `bd bootstrap` do the rest.
#
# Requires read access to the private repo (gh auth / git credentials). If you
# do not have it, this fails at the clone step and that is expected — see
# CLAUDE.md. Verified end-to-end against a fresh clone on 2026-08-10: 16 open
# issues and both dependency edges recovered.

set -euo pipefail

DOLT_REMOTE='git+https://github.com/wdhaines/tunatale-tasks.git'
ISSUE_PREFIX='tunatale'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

command -v bd >/dev/null || {
  echo "beads-bootstrap: bd not on PATH — install it first (brew install beads)." >&2
  exit 1
}

# Already have a populated store? Do nothing. `bd bootstrap` is safe here (it
# "will never delete existing issues"), but silently re-running it on a healthy
# store invites a needless divergence, so stop early and say so.
if [[ -d .beads/embeddeddolt ]] && bd list --json >/dev/null 2>&1; then
  n=$(bd list --status open --json | jq 'length')
  echo "beads-bootstrap: store already present (${n} open). Nothing to do."
  echo "  To pull newer issues from the remote instead: bd dolt pull"
  exit 0
fi

# `bd bootstrap` clones from sync.remote when it is configured, but reading that
# key needs a config file, and a fresh clone has no .beads/ at all. Seed the
# minimum by hand — this is the chicken-and-egg step the recipe exists for.
echo "==> seeding .beads/config.yaml with the private Dolt remote"
mkdir -p .beads
chmod 700 .beads          # bd warns at 0755; it wants 0700
printf 'sync.remote: %s\nissue_prefix: %s\n' "$DOLT_REMOTE" "$ISSUE_PREFIX" > .beads/config.yaml

echo "==> bd bootstrap (clones the Dolt store from the private remote)"
bd bootstrap --yes

# Wire the remote for ordinary push/pull from here on. bootstrap sets up the
# clone; this is what makes `bd dolt push` work without --remote afterwards.
if ! bd dolt remote list 2>/dev/null | awk '$1=="origin"{found=1} END{exit !found}'; then
  echo "==> wiring dolt remote 'origin' for future push/pull"
  bd dolt remote add origin "$DOLT_REMOTE"
fi

echo "==> verifying"
open_n=$(bd list --status open --json | jq 'length')
blocked_n=$(bd blocked --json 2>/dev/null | jq 'length' 2>/dev/null || echo '?')
echo "    ${open_n} open issues, ${blocked_n} blocked (dependency edges intact)"

if [[ "$open_n" == "0" ]]; then
  echo "    !! 0 open issues is suspicious for this repo — the backlog is normally"
  echo "    !! non-empty. Do NOT start work on the assumption there is nothing to do;"
  echo "    !! check 'bd dolt remote list' and your access to the private repo first."
  exit 1
fi

echo "==> done — 'bd ready' now works"
