#!/usr/bin/env bash
# Is the RUNNING dev server serving what is on disk?
#
# WHY THIS EXISTS (2026-08-31). Tapping a gloss stopped working on the phone.
# Every test was green, CI was green, Playwright touch emulation passed, and the
# component source read correctly — because nothing was wrong with the code. The
# frontend dev server had been up since Aug 29; the dependency pass rewrote
# node_modules/svelte at Aug 31 12:56 (5.56.8 -> 5.57.0) underneath it. Vite
# pre-bundles dependencies at BOOT and serves that graph, so it was serving a
# stale runtime hot-patched with new app modules. Restarting fixed it with zero
# code changes. Diagnosing it took hours; this script is the two comparisons
# that would have answered it immediately.
#
# The same session produced the backend half: uvicorn's --reload picks up new
# CODE but NOT a changed .env. The route existed and returned 405 on GET while
# its settings flag stayed False, because reload re-imports modules without
# re-reading config.
#
# ⚠️ Run this the moment someone reports frontend behaviour you cannot reproduce
# — ESPECIALLY "it worked before" or "only on my phone". A defect that is green
# in every build but live in the running server is not a code defect, and no
# test can reach it: tests build fresh, this does not.
#
# Deliberately NOT wired into ./test.sh. It measures a running process, not the
# tree, so it would be vacuous or flaky there — and adding a check to test.sh
# obliges adding it to ci.yml, where no dev server exists at all.
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
stale=0

mt() { [ -e "$1" ] && stat -f %m "$1" 2>/dev/null || echo 0; }
when() { [ -e "$1" ] && stat -f '%Sm' -t '%Y-%m-%d %H:%M:%S' "$1" 2>/dev/null || echo "(absent)"; }

echo "=== frontend: Vite dependency prebundle ==="
VITE_CACHE="$REPO/frontend/node_modules/.vite"
LOCK="$REPO/frontend/bun.lock"
if [ ! -e "$VITE_CACHE" ]; then
  echo "  no prebundle cache yet — nothing running, or it will build on next boot"
else
  echo "  prebundle built : $(when "$VITE_CACHE")"
  echo "  bun.lock written: $(when "$LOCK")"
  if [ "$(mt "$VITE_CACHE")" -lt "$(mt "$LOCK")" ]; then
    echo "  ⚠️  STALE — dependencies changed after the server pre-bundled them."
    echo "      The running server is serving a dependency graph that no longer"
    echo "      matches node_modules. RESTART the dev server; a reload and HMR"
    echo "      will NOT fix it, because the prebundle is built once, at boot."
    stale=1
  else
    echo "  ✓ fresh"
  fi
fi

echo "=== backend: uvicorn process vs .env ==="
ENV_FILE="$REPO/backend/.env"
# The oldest surviving uvicorn process is the supervisor: --reload replaces the
# WORKER on a code change, so a young worker says nothing about when config was
# last read. The supervisor's start time is when .env was actually loaded.
SUP_START=$(ps -eo lstart=,command= | grep "[u]vicorn app.main:app" | head -1 | awk '{print $1,$2,$3,$4,$5}')
if [ -z "$SUP_START" ]; then
  echo "  backend not running"
else
  SUP_EPOCH=$(date -j -f "%a %b %e %T %Y" "$SUP_START" +%s 2>/dev/null || echo 0)
  echo "  uvicorn started : $SUP_START"
  echo "  .env written    : $(when "$ENV_FILE")"
  if [ "$SUP_EPOCH" -ne 0 ] && [ "$SUP_EPOCH" -lt "$(mt "$ENV_FILE")" ]; then
    echo "  ⚠️  STALE — .env changed after uvicorn started."
    echo "      --reload re-imports modules but does NOT re-read .env, so the"
    echo "      running server has the new CODE and the old CONFIG. Restart it."
    stale=1
  else
    echo "  ✓ fresh"
  fi
fi

echo
if [ "$stale" -ne 0 ]; then
  echo "=== STALE — restart ./start-dev.sh before believing any frontend bug report ==="
  exit 1
fi
echo "=== Both fresh — a bug reported now is a real bug ==="
