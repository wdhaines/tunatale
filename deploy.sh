#!/usr/bin/env bash
# Ship a built image to the box, or roll back to an earlier one.
#
#   ./deploy.sh <commit-sha>     deploy that SHA
#   ./deploy.sh --current        what is running right now
#   ./deploy.sh --history        what has been deployed, newest first
#
# A rollback is not a special mode: it is `./deploy.sh <older-sha>`. That is
# deliberate — a recovery path that only runs during a recovery is a path
# nobody has tested. Every deploy exercises it.
#
# The images come from .github/workflows/deploy.yml, which is manual. This
# script does not build, and it never pushes code to the box: the box runs a
# tagged image and a compose file, and holds no checkout.
#
# Admin reaches the box over Tailscale. If the tailnet is down, the break-glass
# path is `gcloud compute ssh <vm> --zone=<zone> --tunnel-through-iap`; see
# docs/deployment.md § Provisioning.
set -euo pipefail

HOST="${TT_DEPLOY_HOST:-tunatale}"
USER_AT="${TT_DEPLOY_USER:+${TT_DEPLOY_USER}@}"
SSH_KEY="${TT_DEPLOY_KEY:-$HOME/.ssh/google_compute_engine}"
REMOTE_DIR="${TT_DEPLOY_DIR:-/opt/tunatale}"
OWNER="${TT_IMAGE_OWNER:-wdhaines}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ssh_box() { ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=15 "${USER_AT}${HOST}" "$@"; }

die() { echo "deploy: $*" >&2; exit 1; }

case "${1:-}" in
  --current)
    ssh_box "cat $REMOTE_DIR/.env 2>/dev/null; sudo docker compose -f $REMOTE_DIR/docker-compose.yml ps --format '{{.Service}}\t{{.Image}}\t{{.Status}}' 2>/dev/null"
    exit $?
    ;;
  --history)
    ssh_box "cat $REMOTE_DIR/deploy-history.log 2>/dev/null | tail -r 2>/dev/null || tac $REMOTE_DIR/deploy-history.log"
    exit $?
    ;;
  "" | --help | -h)
    sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
esac

TAG="$1"
# A full SHA only. A branch name or a short SHA would make "what is running?"
# unanswerable later, and the workflow tags images with the full SHA anyway.
[[ "$TAG" =~ ^[0-9a-f]{40}$ ]] || die "expected a full 40-character commit SHA, got '$TAG'"

# Warn, don't refuse: an older SHA is a rollback, and the commit may legitimately
# not be in this checkout (a colleague's build, a fetch you haven't done).
if git -C "$REPO_ROOT" cat-file -e "${TAG}^{commit}" 2>/dev/null; then
  echo "==> shipping $(git -C "$REPO_ROOT" log -1 --format='%h %s' "$TAG")"
else
  echo "==> shipping $TAG (not in this checkout — fetch it if you want to read the diff)"
fi

echo "==> syncing compose file to $HOST:$REMOTE_DIR"
ssh_box "sudo mkdir -p $REMOTE_DIR && sudo chown \$(id -u):\$(id -g) $REMOTE_DIR"
scp -q -i "$SSH_KEY" -o BatchMode=yes "$REPO_ROOT/docker-compose.yml" "${USER_AT}${HOST}:$REMOTE_DIR/docker-compose.yml"

# The app's own env file is NOT shipped from here: it holds secrets and lives
# only on the box (docs/deployment.md § Accounts). Refuse early rather than
# letting compose fail halfway through a pull.
ssh_box "test -f $REMOTE_DIR/backend/.env" \
  || die "$HOST:$REMOTE_DIR/backend/.env is missing — create it from backend/.env.prod.example before deploying"

PREVIOUS="$(ssh_box "sed -n 's/^TT_TAG=//p' $REMOTE_DIR/.env 2>/dev/null" || true)"
[ -n "$PREVIOUS" ] && echo "==> currently running: $PREVIOUS"

echo "==> pulling images"
ssh_box "cd $REMOTE_DIR && printf 'TT_TAG=%s\n' '$TAG' > .env && sudo -E docker compose pull --quiet"

echo "==> starting"
ssh_box "cd $REMOTE_DIR && sudo -E docker compose up -d --remove-orphans"

echo "==> waiting for health"
if ! ssh_box "for i in \$(seq 1 30); do
        state=\$(sudo docker inspect -f '{{.State.Health.Status}}' \$(sudo docker compose -f $REMOTE_DIR/docker-compose.yml ps -q api) 2>/dev/null || echo none)
        [ \"\$state\" = healthy ] && exit 0
        [ \"\$state\" = unhealthy ] && exit 1
        sleep 2
      done; exit 1"; then
  echo "deploy: api did not become healthy. Last 40 log lines:" >&2
  ssh_box "sudo docker compose -f $REMOTE_DIR/docker-compose.yml logs --tail=40 api" >&2 || true
  [ -n "$PREVIOUS" ] && echo "deploy: roll back with  ./deploy.sh $PREVIOUS" >&2
  exit 1
fi

# Written only after health passes, so the log is a record of what actually ran.
ssh_box "printf '%s\t%s\tfrom=%s\n' \"\$(date -u +%Y-%m-%dT%H:%M:%SZ)\" '$TAG' '${PREVIOUS:-none}' >> $REMOTE_DIR/deploy-history.log"

echo "==> deployed $TAG"
ssh_box "sudo docker compose -f $REMOTE_DIR/docker-compose.yml ps --format '{{.Service}}\t{{.Image}}\t{{.Status}}'"
