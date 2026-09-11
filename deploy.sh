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

# Fail with an answer rather than with "Permission denied (publickey)".
# The box uses OS Login, whose POSIX username is derived from the Google
# account (wdhaines@gmail.com -> wdhaines_gmail_com) and therefore does NOT
# match the local username ssh assumes by default.
preflight() {
  ssh_box true 2>/dev/null && return 0
  echo "deploy: cannot ssh to ${USER_AT}${HOST} with $SSH_KEY" >&2
  if [ -z "$USER_AT" ]; then
    echo "deploy: no user given, so ssh used '$(id -un)'. The box's OS Login name is different — find it with:" >&2
    echo "         gcloud compute os-login describe-profile --format='value(posixAccounts[0].username)'" >&2
    echo "       then re-run as:  TT_DEPLOY_USER=<that> $0 $*" >&2
  fi
  echo "deploy: if the tailnet is down, get in with: gcloud compute ssh <vm> --zone=<zone> --tunnel-through-iap" >&2
  exit 1
}

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

preflight "$@"

echo "==> syncing compose file to $HOST:$REMOTE_DIR"
ssh_box "sudo mkdir -p $REMOTE_DIR && sudo chown \$(id -u):\$(id -g) $REMOTE_DIR"
scp -q -i "$SSH_KEY" -o BatchMode=yes "$REPO_ROOT/docker-compose.yml" "${USER_AT}${HOST}:$REMOTE_DIR/docker-compose.yml"

# The app's own env file is NOT shipped from here: it holds secrets and lives
# only on the box (docs/deployment.md § Accounts). Refuse early rather than
# letting compose fail halfway through a pull.
ssh_box "test -f $REMOTE_DIR/backend/.env" \
  || die "$HOST:$REMOTE_DIR/backend/.env is missing — create it from backend/.env.prod.example before deploying"

# The last tag that reached HEALTHY, from the history log — not the tag in
# .env, which is simply the last one attempted. After a failed deploy those
# differ, and it is the failed tag that .env holds: a rollback hint naming it
# would send you back to the thing that just broke.
PREVIOUS="$(ssh_box "awk -F'\t' 'NF>1 {tag=\$2} END {print tag}' $REMOTE_DIR/deploy-history.log 2>/dev/null" || true)"
[ -n "$PREVIOUS" ] && echo "==> last healthy deploy: $PREVIOUS"

echo "==> pulling images"
ssh_box "cd $REMOTE_DIR && printf 'TT_TAG=%s\n' '$TAG' > .env && sudo -E docker compose pull --quiet"

echo "==> starting"
ssh_box "cd $REMOTE_DIR && sudo -E docker compose up -d --remove-orphans"

# Waits on ONE container id, resolved once. `ps -q api` can return two ids
# during a recreate (the outgoing container has not been removed yet), and
# `docker inspect` over two ids prints two lines, which never equals "healthy" —
# a wait that fails on a stack that is perfectly fine.
#
# Every state CHANGE is printed. The first version of this reported only
# "did not become healthy", which was untrue (the app was healthy seconds later)
# and left nothing to diagnose from. Measured cold start on the e2-micro: 18s,
# with the first check at +5s failing with curl exit 7 while uvicorn binds.
#
# "unhealthy" must persist, because docker reports it transiently while a
# container is restarting, and `restart: unless-stopped` means a container that
# crashed once is mid-restart exactly when this runs.
echo "==> waiting for health"
if ! ssh_box "
      cid=\$(sudo docker compose -f $REMOTE_DIR/docker-compose.yml ps -q api | head -n1)
      [ -n \"\$cid\" ] || { echo 'no api container'; exit 1; }
      last=''; sick=0
      for i in \$(seq 1 60); do
        state=\$(sudo docker inspect -f '{{.State.Status}}/{{.State.Health.Status}}' \"\$cid\" 2>/dev/null || echo 'gone/none')
        [ \"\$state\" != \"\$last\" ] && { echo \"    \$state\"; last=\$state; }
        case \"\$state\" in
          */healthy) exit 0 ;;
          */unhealthy) sick=\$((sick+1)); [ \$sick -ge 3 ] && exit 1 ;;
          *) sick=0 ;;
        esac
        sleep 3
      done
      echo '    timed out after 180s'; exit 1"; then
  echo "deploy: api did not become healthy. Last 40 log lines:" >&2
  ssh_box "sudo docker compose -f $REMOTE_DIR/docker-compose.yml logs --tail=40 api" >&2 || true
  [ -n "$PREVIOUS" ] && echo "deploy: roll back with  ./deploy.sh $PREVIOUS" >&2
  exit 1
fi

# Written only after health passes, so the log is a record of what actually ran.
ssh_box "printf '%s\t%s\tfrom=%s\n' \"\$(date -u +%Y-%m-%dT%H:%M:%SZ)\" '$TAG' '${PREVIOUS:-none}' >> $REMOTE_DIR/deploy-history.log"

echo "==> deployed $TAG"
ssh_box "sudo docker compose -f $REMOTE_DIR/docker-compose.yml ps --format '{{.Service}}\t{{.Image}}\t{{.Status}}'"
