#!/usr/bin/env bash
# Install (or update) the prod box's disk alert (tunatale-al6).
#
#   TT_DEPLOY_USER=<os-login-name> TT_ALERT_EMAIL=<gmail> ./install-disk-alert.sh [--test]
#
# Copies backend/scripts/disk_alert.py to the box and runs it hourly from a
# systemd timer as root. --test also sends one email right away.
#
# The Gmail app password is NOT handled here. It must already be at
# /etc/tunatale/smtp-password (root:root 600); docs/deployment.md § Disk and log
# hygiene has the one command that puts it there without argv or history.
# The address goes to /etc/tunatale/disk-alert.env, not into this repo.
set -euo pipefail

HOST="${TT_DEPLOY_HOST:-tunatale}"
USER_AT="${TT_DEPLOY_USER:+${TT_DEPLOY_USER}@}"
SSH_KEY="${TT_DEPLOY_KEY:-$HOME/.ssh/google_compute_engine}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() { echo "install-disk-alert: $*" >&2; exit 1; }
ssh_box() { ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=15 "${USER_AT}${HOST}" "$@"; }

[ -n "${TT_DEPLOY_USER:-}" ] || die "set TT_DEPLOY_USER (see docs/deployment.md § Connecting)"
[[ "${TT_ALERT_EMAIL:-}" == *@* ]] || die "set TT_ALERT_EMAIL to the Gmail address that owns the app password"

ssh_box "sudo test -s /etc/tunatale/smtp-password" \
  || die "/etc/tunatale/smtp-password is missing or empty on $HOST — store the app password first"

echo "==> installing disk_alert.py"
ssh_box "sudo install -D -m 755 -o root -g root /dev/stdin /usr/local/lib/tunatale/disk_alert.py" \
  < "$REPO_ROOT/backend/scripts/disk_alert.py"
ssh_box "sudo python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' /usr/local/lib/tunatale/disk_alert.py" \
  || die "the box's python3 cannot parse disk_alert.py"

echo "==> writing /etc/tunatale/disk-alert.env and the systemd units"
printf 'TT_ALERT_EMAIL=%s\n' "$TT_ALERT_EMAIL" \
  | ssh_box "sudo sh -c 'umask 077; cat > /etc/tunatale/disk-alert.env'"

ssh_box "sudo tee /etc/systemd/system/tunatale-disk-alert.service >/dev/null" <<'EOF'
[Unit]
Description=TunaTale disk usage alert (email at 75%)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/tunatale/disk-alert.env
ExecStart=/usr/bin/python3 /usr/local/lib/tunatale/disk_alert.py --to ${TT_ALERT_EMAIL} --password-file /etc/tunatale/smtp-password
EOF

ssh_box "sudo tee /etc/systemd/system/tunatale-disk-alert.timer >/dev/null" <<'EOF'
[Unit]
Description=Hourly TunaTale disk usage check

[Timer]
OnCalendar=hourly
RandomizedDelaySec=300
Persistent=true

[Install]
WantedBy=timers.target
EOF

ssh_box "sudo systemctl daemon-reload && sudo systemctl enable --now tunatale-disk-alert.timer >/dev/null"
ssh_box "sudo systemctl start tunatale-disk-alert.service" \
  || die "the first check failed: sudo journalctl -u tunatale-disk-alert.service -n 20"
echo "==> first check ran:"
ssh_box "sudo journalctl -u tunatale-disk-alert.service -n 3 --no-pager -o cat"

if [ "${1:-}" = "--test" ]; then
  echo "==> sending a test email to $TT_ALERT_EMAIL"
  ssh_box "sudo sh -c '. /etc/tunatale/disk-alert.env && /usr/bin/python3 /usr/local/lib/tunatale/disk_alert.py --to \"\$TT_ALERT_EMAIL\" --password-file /etc/tunatale/smtp-password --test'"
fi

echo "==> timer:"
ssh_box "systemctl list-timers tunatale-disk-alert.timer --no-pager | head -2"
