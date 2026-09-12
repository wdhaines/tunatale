# Deployment

Operational runbook for running TunaTale somewhere other than the author's
laptop. Built incrementally alongside the `Deploy` epic; sections appear as the
corresponding work lands.

Provisioning the host is written and was executed end to end on 2026-09-11.
Caddy/TLS, image delivery, data migration and cutover are not written yet.
Account management and backups apply to the laptop today as well.

## Provisioning the host — GCP e2-micro

What this produces: one e2-micro VM in an Always Free region with a 30 GB
standard disk, Docker, 2 GiB swap, unattended security upgrades with automatic
reboots, and ports 80/443 open for Caddy. Admin access goes over Tailscale, with
Google's IAP tunnel as the break-glass path. **Port 22 never faces the
internet, not even during bootstrap.**

Every command below was run on 2026-09-11 against a fresh project, and every
timing is from that run. Everything after step 3 is plain Ubuntu 24.04 and
applies unchanged to the Hetzner CX23 fallback.

⚠️ **This repo is public.** This section carries procedure only. The project
ID, the external IP, the tailnet name and the OS Login username are recorded in
the private tasks repo (`tunatale-zp9`), not here.

```bash
PROJECT=<project id>
REGION=us-east1        # us-west1 / us-central1 / us-east1 — NO other region is free
ZONE=${REGION}-b
```

### What is free and what is not — read off the billing catalog

The free-tier page and the VPC pricing page disagree with each other, so this
table comes from the source billing actually applies: the Cloud Billing Catalog
(`GET cloudbilling.googleapis.com/v1/services/6F81-5844-456A/skus`, read
2026-09-11). Re-read it rather than trusting the table if a bill ever surprises
you.

| Resource | Free | Then | SKU |
|---|---|---|---|
| e2-micro, non-preemptible | every hour of the month, **only** in us-west1/us-central1/us-east1 | E2 core/RAM rates | free-tier program |
| Standard PD (`pd-standard`) | 30 GiB-month | $0.04/GiB-mo | `D973-5D65-BAB2` |
| Balanced PD (`pd-balanced`) — **the console default** | **none** | $0.10/GiB-mo | `6AE1-525F-8B80` |
| External IPv4 in use on a VM, static or ephemeral | **720 h/month** per billing account | $0.005/h | `C054-7F72-A02E` |
| Static IPv4 reserved but *unattached* | 1 h/month | $0.01/h | `66A2-68EA-56BE` |
| Internet egress, **Standard** network tier | **200 GiB/month** | $0.085/GiB | `8312-AAA7-AE05` (us-east1) |
| Internet egress, Premium tier — **the project default** | 1 GiB/month | $0.12/GiB | `F274-1692-F213` |
| Disk snapshots, custom images | none | $0.05/GiB-mo | `817F-F5A3-514E`, `DAA2-253C-6680` |

Three consequences, all applied below:

1. **Use the Standard network tier.** It moves the egress allowance from 1 GiB
   to 200 GiB a month. The design docs budgeted 1 GB/month against a measured
   ~0.4 GB baseline. On Standard tier, egress stops being a cost question at all.
2. **Name `pd-standard` explicitly.** The default disk type bills from the first
   gigabyte, and nothing warns you.
3. **The IP is free for 720 hours, and a 31-day month has 744.** The expected
   bill is therefore **$0.12 in each 31-day month** (24 h × $0.005) and $0
   otherwise. That is below the $1 budget's first alert ($0.50), so it shows on
   the invoice and never in an email. The VPC pricing page says the IP free tier
   is "one hour per month", which contradicts the catalog. The first 31-day
   month (October 2026) settles which one billing applies.

A static IP attached to the VM (running *or stopped*) costs nothing beyond that
720-hour pool. It only bills at the higher rate once it is detached, or once
the VM is deleted and the address is left reserved. A second VM, a second IP or
a second disk draws from the same per-account pools, so none of them is free.

⚠️ The project may be shared with unrelated tooling. Never disable an API,
delete an OAuth client or edit the consent screen as part of this runbook. And
do **not** `gcloud config set billing/quota_project`: it reroutes every call's
quota and IAM calls then fail with `SERVICE_DISABLED`.

### 1. Enable Compute Engine — ~70 s, first time only

```bash
gcloud services enable compute.googleapis.com --project=$PROJECT
```

This creates the auto-mode `default` VPC with four firewall rules, **two of
which open SSH (22) and RDP (3389) to `0.0.0.0/0`**.

**Verify:** `gcloud compute firewall-rules list --project=$PROJECT` shows
`default-allow-{icmp,internal,rdp,ssh}`.

### 2. Firewall — before the VM exists

```bash
gcloud compute firewall-rules delete default-allow-ssh default-allow-rdp --project=$PROJECT --quiet
gcloud compute firewall-rules create tunatale-allow-iap-ssh --project=$PROJECT \
  --network=default --direction=INGRESS --action=ALLOW --rules=tcp:22 \
  --source-ranges=35.235.240.0/20 --target-tags=tunatale
gcloud compute firewall-rules create tunatale-allow-web --project=$PROJECT \
  --network=default --direction=INGRESS --action=ALLOW --rules=tcp:80,tcp:443 \
  --source-ranges=0.0.0.0/0 --target-tags=tunatale
gcloud compute firewall-rules create tunatale-allow-tailscale --project=$PROJECT \
  --network=default --direction=INGRESS --action=ALLOW --rules=udp:41641 \
  --source-ranges=0.0.0.0/0 --target-tags=tunatale
```

- `35.235.240.0/20` is Google's IAP TCP-forwarding range. It is the only source
  allowed to reach port 22.
- `udp:41641` lets Tailscale connect directly instead of relaying through a DERP
  server. WireGuard drops unauthenticated packets, so this exposes nothing.
- GCP's firewall filters only the VM's external interface. Traffic arriving
  inside Tailscale's tunnel is not subject to it, which is why SSH over the
  tailnet needs no rule.

**Verify:** the list shows exactly five rules, and none pairs `tcp:22` with
`0.0.0.0/0`.

### 3. Reserve the IP and create the VM — ~15 s

```bash
gcloud compute addresses create tunatale-ip --project=$PROJECT \
  --region=$REGION --network-tier=STANDARD
gcloud compute instances create tunatale --project=$PROJECT --zone=$ZONE \
  --machine-type=e2-micro \
  --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB --boot-disk-type=pd-standard \
  --network-tier=STANDARD --address=tunatale-ip --tags=tunatale \
  --no-service-account --no-scopes \
  --deletion-protection \
  --metadata=enable-oslogin=TRUE
```

- The image family is `ubuntu-2404-lts-amd64` from `ubuntu-os-cloud`, not an
  `ubuntu-pro` image, which carries a license fee.
- `--no-service-account --no-scopes`: the VM holds no Google credentials.
  Nothing on it needs any, because the Azure/Groq/B2 keys arrive in the app's
  env file.
- `--deletion-protection`: the data lives on the boot disk, and the disk is
  deleted with the VM.
- Two warnings are expected and harmless: "disk size under 200GB" (performance)
  and "larger than image size". The root filesystem grows itself to 29 G on
  first boot.

**Verify.** The value that matters is the one the API reports, not the flag
you typed:

```bash
gcloud compute instances describe tunatale --project=$PROJECT --zone=$ZONE --format=json \
  | jq '{mt: (.machineType|split("/")[-1]), tier: .networkInterfaces[0].accessConfigs[0].networkTier,
         sa: .serviceAccounts, del: .deletionProtection, model: .scheduling.provisioningModel}'
gcloud compute disks list --project=$PROJECT --format='table(name,type.basename(),sizeGb)'
gcloud compute addresses list --project=$PROJECT --format='table(name,networkTier,status)'
```

Expected: `e2-micro`, `STANDARD`, `null`, `true`, `STANDARD`; disk
`pd-standard 30`; address `STANDARD IN_USE`.

### 4. First login through IAP — the break-glass path

```bash
gcloud compute ssh tunatale --project=$PROJECT --zone=$ZONE --tunnel-through-iap
```

The first run generates `~/.ssh/google_compute_engine`. OS Login maps your
Google account to a POSIX user, and a project owner gets passwordless sudo. The
IAP API did **not** need to be enabled for this to work. Keep this path
working: it is how you get in if Tailscale breaks.

**Verify:** `sudo -n true` succeeds on the box, and a direct connection from
outside does not: `nc -v -z -G 5 <ip> 22` must report `Operation timed out`,
not `Connection refused`. *Refused* means a packet reached the host. Without
`-v`, `nc` prints nothing on failure and the two cases look identical.

The image already ships `PasswordAuthentication no`, unattended-upgrades
enabled and active, `ufw` inactive and `iptables -P INPUT ACCEPT`. Unlike
Oracle's Ubuntu images, **there is no host firewall to open**: GCP's VPC
firewall is the whole perimeter. Remember that for anything Docker publishes.

### 5. Base hardening — swap, sshd, reboots, trim

Save as `base.sh` and run it with
`gcloud compute ssh tunatale --project=$PROJECT --zone=$ZONE --tunnel-through-iap --command='sudo bash -s' < base.sh`.
It is idempotent.

```bash
#!/bin/bash
set -euo pipefail

# 2 GiB swap: 953 MB RAM, ~345 MB of it used at idle before anything is installed.
if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
fi
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab

# The image ships PermitRootLogin without-password.
cat > /etc/ssh/sshd_config.d/60-tunatale.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
sshd -t && systemctl reload ssh

# Let unattended-upgrades reboot for kernel updates. 08:00 UTC = 03:00-04:00 US Eastern.
cat > /etc/apt/apt.conf.d/52tunatale-auto-reboot <<'EOF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "08:00";
EOF

# With no service account the OS Config agent cannot reach its API, and one PD
# needs no multipath. google-guest-agent STAYS: OS Login depends on it.
systemctl disable --now google-osconfig-agent.service 2>/dev/null || true
systemctl disable --now multipathd.service multipathd.socket 2>/dev/null || true
```

The trim bought **27 MB** (343 → 316 MB used). That is less than the agents'
combined RSS suggested, so do not expect more from removing further agents.

**Verify:**
`swapon --show` lists a 2G `/swapfile`;
`sudo sshd -T | grep -E '^(permitrootlogin|passwordauthentication) '` prints `no` for both;
`apt-config dump | grep Automatic-Reboot` prints `"true"` and `"08:00"`.

### 6. Docker — ~2 min

Use Docker's own apt repo: Ubuntu's `docker.io` package ships no Compose v2
plugin. Save as `docker.sh` and run it the same way:

```bash
#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
# json-file never rotates; the data shares this 30 GB disk.
cat > /etc/docker/daemon.json <<'EOF'
{ "log-driver": "local", "log-opts": { "max-size": "10m", "max-file": "3" } }
EOF
systemctl enable --now docker containerd && systemctl restart docker
```

**Verify:** `sudo docker run --rm hello-world` prints `Hello from Docker!`, and
`sudo docker info --format '{{.LoggingDriver}}'` prints `local` (`sudo` because
the OS Login user is not in the `docker` group). On 2026-09-11 this
installed Docker 29.8.0 and Compose v5.5.1.

### 7. Tailscale — the day-to-day admin path

```bash
curl -fsSL https://tailscale.com/install.sh | sudo sh
sudo tailscale up --hostname=tunatale     # prints a login URL; approve it in a browser
```

Then, in the Tailscale admin console, **disable key expiry for this machine**
(Machines → the box → ⋯ → Disable key expiry). A new node's key expires after
about 180 days, and a server that silently drops off the tailnet is the failure
this whole path exists to prevent.

This is plain OpenSSH over the tailnet, with the same OS Login key. Tailscale
SSH (`--ssh`) is deliberately not enabled, so no tailnet ACL decides who gets a
shell.

**Verify, from the laptop:**

```bash
tailscale ping tunatale        # want "via <public-ip>:41641" (direct), not "via DERP"
tailscale status --json | jq '.Peer[] | select(.HostName=="tunatale") | .KeyExpiry'   # want null
OSLOGIN_USER=$(gcloud compute os-login describe-profile --format='value(posixAccounts[0].username)')
ssh -i ~/.ssh/google_compute_engine $OSLOGIN_USER@tunatale 'sudo -n true && echo ok'
```

### 8. Reboot drill — does it come back without you?

```bash
ssh -i ~/.ssh/google_compute_engine $OSLOGIN_USER@tunatale 'uptime -s; sudo systemctl reboot'
# then poll until `uptime -s` prints a NEW boot time
```

Measured 2026-09-11: reboot ordered at :53:48, journal stopped at :54:24 (a
**36 s** shutdown), kernel up at :54:41, SSH over the tailnet answering by
~:55:00. That is **~75 s from reboot to reachable**, with Docker, tailscaled
and swap all back on their own.

⚠️ **Poll on the boot time, not on SSH answering.** `systemctl reboot` returns
before shutdown begins, and sshd kept answering for about 35 s afterwards. The
first attempt at this drill "recovered in 6 s" because it never rebooted.
`uptime` saying 19 minutes was what exposed it.

### State after this section, and what it does not do

After all of the above, the box idles at **~350 MB used / ~600 MB available**
of 953 MB, with no app running. The findings doc budgets 160–250 MB for the
app plus the Anki sync driver.

Ports 80/443 answer `Connection refused`: the perimeter is open and nothing is
listening yet. Everything that listens is later work:

- Caddy, TLS and the domain — `tunatale-1oq` (P2.2)
- image delivery and `deploy.sh` — `tunatale-pse` (P2.3)
- data migration — `tunatale-lbf` (P2.4)

**Rebuild time.** The machine steps of this section total about 5 minutes. The
2026-09-11 run took 24 minutes of wall clock, including one browser approval and
the investigation. A full RTO also needs the data restore onto a different
machine (`tunatale-kbb.6`), which has not been performed yet. Until it has, the
honest RTO is unmeasured.

## Shipping an image

Production runs a **tagged image that a human chose**, never `git pull` of
main. The commit gate is local and nothing on the box re-runs `./test.sh`, so
the image is not evidence the code is good — **CI is**. Build a SHA that CI has
already gone green on.

### Build

`.github/workflows/deploy.yml`, dispatched by hand (or by pushing a `v*` tag):

```bash
gh workflow run deploy-images                # this branch's head
gh workflow run deploy-images -f ref=<sha>   # a specific commit
```

It pushes two images to GHCR, both tagged with the **full commit SHA**:
`tunatale-api` (uvicorn; `init` reuses it with another entrypoint) and
`tunatale-web` (Caddy + the built SPA). The build states `platforms:
linux/amd64` explicitly rather than inheriting the runner's architecture, and
then asserts it on the pushed manifest — an image with the wrong arch pushes and
pulls happily and fails only when the box tries to execute it.

### Deploy, and roll back

```bash
./deploy.sh <full-sha>     # deploy
./deploy.sh --current      # what is running
./deploy.sh --history      # what has run here, newest first
```

**A rollback is not a separate mode — it is `./deploy.sh <older-sha>`.** A
recovery path that only runs during a recovery is a path nobody has tested;
this way every deploy exercises it.

What the script does, and what it refuses:

- Copies `docker-compose.yml` to the box and writes `TT_TAG=<sha>` into `.env`
  beside it. Compose interpolates that file, so the tag is recorded on the box
  rather than living in shell history.
- **Never ships `backend/.env`.** That file holds secrets and exists only on
  the box; the script fails early if it is missing rather than letting compose
  get halfway.
- Refuses anything but a full 40-character SHA. A branch name or short SHA makes
  "what is running?" unanswerable later.
- Waits for the `api` container to report **healthy**, and on failure prints the
  last 40 log lines and the exact rollback command.
- Appends to `deploy-history.log` **only after health passes**, so the log
  records what actually ran.

The compose file is one file for dev and prod on purpose — a prod-only copy
drifts silently. `image:` is what the box pulls; `build:` is what a laptop uses.
`TT_TAG` has no default (`:?`, not `:-latest`), so a mistyped deploy fails
closed instead of shipping "whatever latest is".

### Connecting

`deploy.sh` reaches the box over Tailscale as `$TT_DEPLOY_USER`. **There is no
sensible default**: the box uses OS Login, whose POSIX name is derived from the
Google account (`someone@gmail.com` → `someone_gmail_com`) and does not match
your local username, so plain `ssh` fails with `Permission denied (publickey)`.

```bash
TT_DEPLOY_USER=$(gcloud compute os-login describe-profile --format='value(posixAccounts[0].username)')
export TT_DEPLOY_USER
```

The script preflights this and prints that command rather than letting ssh fail
opaquely. `TT_DEPLOY_HOST`, `TT_DEPLOY_KEY`, `TT_DEPLOY_DIR` and
`TT_IMAGE_OWNER` override the rest.

**GHCR needs no credentials on the box.** Images built by a workflow in a public
repo are public, so `docker pull` works anonymously — verified 2026-09-11 by
pulling on the box, which has no registry login and no service account.

### Verified end to end, 2026-09-11

Run against the real box, not reasoned about:

| step | result |
|---|---|
| build + push both images | `linux/amd64` asserted on the pushed manifests |
| anonymous pull from the box | works; no registry credentials anywhere |
| deploy | ~17s from command to healthy |
| **rollback** | deployed A, then B, then **back to A** — each ~15-20s, `deploy-history.log` recording every transition |
| serving through Caddy | `/api/health` 200, `/` (SPA) 200, `/api/<unknown>` **404 JSON from the API**, not the HTML shell |
| reboot | stack returns unattended and serves 200, no human command |

Three things only a real run found, all now fixed:

1. **A working deploy reported failure.** `start_period: 15s` was shorter than
   the app's cold start, so three health checks failed, docker flipped the
   container to `unhealthy`, and the wait treated that as fatal. Now 90s, with
   the measurement in the compose comment.
2. **The wait was not diagnosable.** It printed only "did not become healthy",
   which was false seconds later. It now prints every state transition, and
   tolerates a transient `unhealthy` rather than failing on the first sight of
   it.
3. **The rollback hint named the wrong tag.** It read the tag from `.env` — the
   last one *attempted* — so after a failed deploy it pointed back at the thing
   that had just broken. It now reads the last tag that actually reached
   healthy, from `deploy-history.log`.

⚠️ **The prod image cannot run with `LLM_MODE=mock`.** `tests/cassettes/` is not
shipped in it, so mock mode dies at startup with a `FileNotFoundError` from
`cassette.py` rather than anything that names the cause. Production is
`LLM_MODE=live` and the `TT_ENV=prod` guard already refuses anything else — but
a non-prod boot of the prod image hits the ugly version.

## Accounts

There is **no self-serve signup**, by design, at any point in Phases 1–3. Every
account is created from the command line, including the first one on a fresh
box. Until you run this, nobody can log in — the API answers 401 to everything
except `/api/health`.

### Creating the first account

On a deployed container:

```bash
docker compose exec -T api \
  uv run python -m app.auth.cli create-user you@example.com
# then type the password and press ctrl-D, or pipe it from a shell variable:
read -rs NEW_PASSWORD          # prompts without echoing
printf '%s\n' "$NEW_PASSWORD" | docker compose exec -T api \
  uv run python -m app.auth.cli create-user you@example.com
```

Locally, drop the `docker compose exec -T api` prefix and run it from
`backend/`.

⚠️ **`-T` matters.** Without it `docker compose exec` allocates a TTY, the CLI
takes that as an interactive session and prompts via `getpass`, and a piped
password is never read — the command then blocks with no visible reason.

### The other commands

```bash
uv run python -m app.auth.cli list-users
uv run python -m app.auth.cli set-password you@example.com     # revokes that account's sessions
uv run python -m app.auth.cli deactivate-user you@example.com  # revokes its sessions too
```

`set-password` and `deactivate-user` both delete the account's server-side
sessions. That is the point of them: a password changed because it may have
leaked is useless if the sessions opened with it keep working.

There is deliberately no `activate-user`. Re-enabling an account somebody
disabled should take more thought than pressing ↑ and Enter; do it from a
Python shell against `AuthDatabase.set_active`.

### ⚠️ Never pass a password in argv

There is no `--password` flag and adding one would be a security bug — argv is
visible in shell history, in `ps` output to every user on the box, and in
process-accounting logs. The two supported sources are stdin and
`TT_AUTH_PASSWORD`:

```bash
TT_AUTH_PASSWORD="$NEW_PASSWORD" docker compose exec -T api \
  uv run python -m app.auth.cli create-user you@example.com
```

Both examples read a shell variable rather than showing a literal. A runbook
that prints a real-looking password invites the reader to paste one into the
very shell history this CLI exists to keep it out of.

An environment variable is not free either — it is readable from `/proc` on
some systems and lands in the deploy tool's logs — but it beats argv and is the
only option a non-interactive deploy has.

### Turning the gate on

`auth_enabled` defaults **False**, which leaves the API open. The production
profile guard (`TT_ENV=prod`) refuses to boot without it set True, so a real
deployment cannot forget. Create the account first, then flip the flag —
the other way round locks you out of your own box.

### Signing in

The SPA determines whether this deployment requires a login by asking
`GET /api/auth/status` at boot — that is the **only** way it can know. The
endpoint returns a single boolean and is deliberately unauthenticated; it must
stay that narrow. `GET /api/auth/me` answers 401 for an anonymous caller
**whether the gate is on or off**, so a 401 alone is not evidence of being
logged out.

A 401 from any other endpoint mid-session sends the user to `/login`, preserving
the route they were on so they land back there after signing in. Sign out lives
on `/settings`, under **Account**.

⚠️ The session cookie is `Secure`. Over plain `http://` a browser stores it and
never sends it back, so login appears to succeed and every later request is
anonymous — **it reads exactly like a broken server and is not**. This is why the
TLS work (Caddy, P2.2) is a prerequisite for the gate in production, not a
nicety. The one exception is `localhost`, which browsers treat as a trustworthy
origin — measured 2026-08-18 in Chromium: a `Secure` cookie set over
`http://localhost` **is** returned on later requests, which is why local dev and
the E2E suite work without TLS.

### Login throttling

Failed login attempts are rate-limited at two independent scopes to defend
against brute-force and distributed enumeration:

- **Per-account** (default: 5 attempts / hour): locks the email address,
  regardless of source IP.  A botnet targeting one account from many addresses
  is stopped here.
- **Per-IP** (default: 20 attempts / hour): locks the address, regardless of
  which accounts were targeted.  A user-enumeration sweep against many
  accounts from one address is stopped here.

Both limits use exponential backoff — each additional failure beyond the
threshold doubles the wait, starting at 60 seconds and capping at 30 minutes.
A **successful** login clears only the account counter (not the IP counter),
so a user who mistypes their password and then gets it right starts clean,
but an attacker who owns one valid account cannot use that account's
successes to reset the per-IP budget.

Only *failed* attempts are recorded.  The Playwright E2E suite, which signs
in repeatedly from one address, is unaffected.

When locked out, the response is `429 Too Many Requests` with a `Retry-After`
header stating the number of whole seconds to wait.  The lock lapses on its
own after the backoff expires; no administrator intervention is needed.
Attempts against a nonexistent email lock on the same schedule as a real one,
so the 429 is not a user-enumeration oracle.

**`TRUSTED_PROXY_HEADER`** must be set to `X-Forwarded-For` behind the
Caddy reverse proxy (P2.2).  Without it every request appears to come from
the proxy's address and all callers share one throttle bucket.  The backend
reads the **rightmost** entry in the header, which is the one the trusted
proxy vouched for; the leftmost is attacker-controlled.

The accepted trade-off: anyone who can reach the login endpoint can lock a
known account out for up to 30 minutes by failing on purpose.  That is the
standard cost of account lockout, it is time-bounded, and it is preferred
over leaving distributed guessing unthrottled.

## Backups and restore

### Why this section exists before the deployment does

A backup that has never been restored is not a backup. This project has wiped
its curricula twice — 2026-06-30 and 2026-07-13, both from an E2E run pointed at
the real DB by a `DATABASE_URLS` casing bug — which is why the restore path is
treated as the risky half and was drilled before any off-box storage existed.

### What is irreplaceable

| Path | Size | Why it cannot be regenerated |
|---|---|---|
| `backend/tunatale_no.db` | 18 MB | curricula, lessons, FSRS state not mirrored in Anki |
| `backend/tunatale_sl.db` | 2.9 MB | same, Slovene |
| `backend/media` | 332 MB | Forvo/Pixabay fetches; upstream is not guaranteed to still serve them |
| `backend/output` | 68 MB | rendered lesson audio |

"Regenerable" does not hold up as a reason to skip the trees: audio rendering
depends on `edge-tts`, an unofficial endpoint that breaks at home and is
filtered from datacenter IPs, and text regeneration is bounded by Groq's free
tier (200K tokens, 1K requests/day). Rebuilding months of content is weeks of
budget at best.

Total payload is ~420 MB against a 10 GB free tier — 25× headroom, so there is
nothing to ration and no selection decision to make.

### The existing rolling snapshots are not off-box backups

`app/storage/db_backup.py::rotate_db_backups` snapshots each content DB once per
calendar day into `~/.tunatale/db-backups`, keeping `db_backup_keep_days` (5).
It runs at app startup and never raises, so a backup problem cannot block boot.

**These live on the same disk as the DBs they protect.** They defend against
application bugs and stray test runs — the failure mode that actually happened
twice — and not at all against losing the machine. They are the *input* to the
drill below, not a substitute for off-box storage.

### Snapshots are self-contained — ignore the sidecars

`rotate_db_backups` writes each snapshot with SQLite's online-backup API
(`Connection.backup`), which materialises everything into the `.db` file. The
`-wal` / `-shm` files that appear next to snapshots are **inspection
artifacts**: they are created the moment anything opens a snapshot, including a
read-only `sqlite3` query.

Verified 2026-08-12: a snapshot `.db` copied alone, with no sidecars, produced
row-for-row identical counts to the same snapshot read in place with its
sidecars present. Separately, opening a snapshot during the drill created a
zero-byte `-wal` beside it while leaving the `.db` byte-identical.

**Do not chase the WAL when restoring.** Copy the `.db`. A restore procedure
built around "remember the sidecars" would be protecting against nothing and
would obscure the real failure modes.

### Running the drill

```bash
cd backend
uv run python scripts/restore_drill.py --scratch /tmp/restore-drill
```

Read-only with respect to every source; everything lands under `--scratch`,
which is wiped per run. Exit code is non-zero on any failure, so it can be a
cron or CI gate. It checks:

- newest snapshot per DB stem, restored `.db`-only, timed
- `PRAGMA integrity_check` and `foreign_key_check`
- row counts per table
- every `media` row's recorded `sha256` against the restored tree — cryptographic
  verification, not a file count
- every `audio_files` entry full-decoded with `ffmpeg -f null`, so the check is
  playability rather than "the header parses", plus a check that the caption
  timeline does not run past the audio

Point `--snapshot-dir` at a restic/rclone-restored tree to run the identical
drill against an off-box backup. That is the intended Phase 2 use.

The app-boot step is printed at the end rather than automated, because it needs
a free port and a comparison against the running server.

### Drill results — 2026-08-12, local snapshots

Performed against `~/.tunatale/db-backups` snapshots dated 2026-08-12, restored
into a scratch directory, on the author's Mac (Darwin 25.5.0, APFS SSD).

| Step | Wall clock |
|---|---|
| Restore both DBs (21 MB) | 0.01 s |
| Restore `media` + `output` trees (400 MB) | 3.25 s |
| Verify 6001 media checksums | 0.23 s |
| Full-decode 48 audio files (7.4 h of audio) | 38.97 s |
| **Total** | **43.34 s** |

**RTO for the data layer is under a minute** once the bytes are local. The
provisioning runbook should state the download time from the chosen remote as
the dominant term, not the restore itself — at 420 MB that is bandwidth-bound.

Verified on the restored copy:

- `integrity_check` — `ok` on both DBs
- 6001/6001 Norwegian media files matched their recorded sha256; 0 missing, 0
  mismatched
- 48/48 lesson audio files decoded end to end across 6 lessons, 0 decode errors,
  0 caption overruns
- Booted the app against the restored DBs on port 8099 and compared against the
  live server: `/api/srs/stats` `{"total":3014,"due_today":94}` and
  `/api/srs/queue-stats` `{"new":3,"learning":0,"review":95,...}` were **identical**
  on both, and the curriculum list matched

Row counts that came back (Norwegian): 3014 collocations, 3033 directions, 24184
`tt_revlog`, 6001 media, 6 lessons, 1 curriculum — 37253 rows across 17 tables.
Slovene: 734 collocations, 17115 `tt_revlog`, 1379 media, 20636 rows total.

### Known gaps found by the drill

These are pre-existing conditions the drill surfaced, confirmed present in the
live data by the same query. None is a backup fault — a faithful restore
reproduces the source warts and all — but each is worth fixing.

1. **The restored media tree cannot be served.** `MEDIA_DIR` is ignored by the
   serving route: `api/srs.py::serve_media` reads a module-level
   `_MEDIA_DIR = Path(__file__).parent.parent.parent / "media"`, and
   `app.state.audio_dir` is hardcoded to `_BACKEND_DIR / "output/audio"` in
   `main.py`. Demonstrated by setting `MEDIA_DIR` to the restored tree, altering
   a file there, and confirming the API still served the original bytes from
   `backend/media`. Meanwhile `settings.media_dir` *is* read on the import side
   (`media/importer.py`, `plugins/anki_sync/import_seed.py`), so the two halves
   can silently diverge — imports land in one directory while serving reads
   another. They coincide today only because the default `./media` resolves to
   the same place under the dev CWD. Tracked by the container-safe mutable paths
   work.

2. **41 foreign-key orphans in `tunatale_no.db`** — 25 `tt_revlog` rows
   referencing absent `collocation_directions`, 16 `media` rows referencing
   absent `collocations`. `PRAGMA foreign_keys` is `0` (SQLite's default), so
   nothing enforces these at write time. Identical row-for-row in live and
   restored, so pre-existing.

3. ~~**One dangling media reference in `tunatale_sl.db`**~~ — **FIXED
   2026-08-12.** Row 723 (collocation 373, `skodelica`) pointed at
   `img_cup.jpg`, which was absent from `backend/media`, and it was the only one
   of 1379 Slovene media rows failing checksum verification.

   **The original diagnosis here was wrong and is worth recording, because the
   wrong one implied a much worse fix.** It read the co-located
   `img_cup_a7577283.jpg` as the same image after a hash-suffixed rename, which
   would have made the repair "repoint the row at the new name" — silently
   swapping the image on a card, and orphaning the `<img src="img_cup.jpg">`
   that `sync_engine.py` had already written into the Anki note.

   The suffix is not a rename tag. `cards/media/vocab_media.py` builds
   `{stem}_{sha256(data)[:8]}.{ext}`, so it is a *content* hash — and
   `a7577283…` is that file's own digest, not `8cf43f1d…`, the digest row 723
   recorded. They are two different pictures of a mug, both fetched under
   different naming schemes.

   The actual state was simpler and entirely recoverable: **`img_cup.jpg` still
   existed in Anki's `collection.media`, byte-identical to the recorded
   sha256.** TT's media tree had lost a file Anki still held. The fix was to
   copy it back — no database write, no Anki write, no hash change, nothing to
   re-sync. Verified: 1379/1379 Slovene media rows now match, live and restored.

   The generalisable lesson: **when a media row and a media file disagree, check
   Anki's media folder before concluding anything is lost.** It is a second
   copy of every file TT has ever pushed, and it is not covered by any of the
   reasoning about what is "regenerable".

   (`img_cup_a7577283.jpg` remains in `backend/media` with no `media` row
   pointing at it. Harmless, and left alone — an orphan file costs 26 KB, while
   deleting a file some Anki note might reference costs an image.)

## Off-box backups — restic into Backblaze B2

`scripts/backup_offbox.py` is the second line of defence: the rolling snapshots
above protect against application bugs, this protects against losing the
machine. It is a thin driver around [restic](https://restic.net), chosen over an
`rclone` mirror because restic's repository is content-addressed and versioned —
a mirror propagates a corruption or a deletion to the remote on its next run,
which is the failure this project has already had twice.

### What it ships, and why not the rolling snapshots

Sources are a **fresh** snapshot of each configured language DB, plus the Anki
collection (`settings.anki_collection_path`, by default
`~/Library/Application Support/Anki2/Will/collection.anki2`), plus `backend/media`
and `backend/output`. Pass `--no-anki-collection` to skip the collection on a
deployment with no Anki, or `--anki-collection PATH` to override the default
path.

It deliberately does **not** back up `~/.tunatale/db-backups`. Those are written
at most once per calendar day, earliest-wins — a rule that exists so an
afternoon wipe cannot clobber the morning's good copy, and that is exactly wrong
for an off-box job: it would upload this morning's database tonight and report
success. `stage_db_snapshots` takes its own consistent snapshot at backup time
into `~/.tunatale/offbox-staging`, and restic's own snapshot history supplies
the time depth the rolling directory was providing.

Staging is swept on every run, so it is fenced twice against the one-typo
disaster of pointing `--staging` at `backend/` or at the rolling snapshot
directory: an ownership marker (`.tt-offbox-staging`) that a non-empty
unmarked directory will not get, and a name filter that only ever considers
`{stem}.{YYYY-MM-DD}.db`.

### Secrets

Three Keychain items, read at run time; nothing lives in the repo or in `.env`:

| Service | Account | Value |
|---|---|---|
| `tunatale-restic` | `repo-password` | restic repository passphrase |
| `tunatale-b2` | `key-id` | B2 `applicationKeyId` |
| `tunatale-b2` | `app-key` | B2 `applicationKey` |

```bash
openssl rand -base64 32 | pbcopy          # then paste into the password manager FIRST
security add-generic-password -s tunatale-restic -a repo-password -w
security add-generic-password -s tunatale-b2     -a key-id        -w
security add-generic-password -s tunatale-b2     -a app-key       -w
```

The bare `-w` makes `security` prompt, so no secret reaches shell history. A
missing item is reported with the exact `add-generic-password` line that fixes
it — an unattended job's stderr is the only user interface it has.

⚠️ **The passphrase must not live only on the machine being backed up.** The
Keychain copy exists so the job can run unattended; the durable copy lives in
the password manager. A restic repository whose passphrase is lost is
indistinguishable from no backup at all — there is no recovery path, by design.

The B2 application key needs `listBuckets`, `listFiles`, `readFiles`,
`writeFiles` and `deleteFiles`. A key missing `deleteFiles` works fine until
`forget --prune` runs weeks later. Set the bucket's lifecycle rule to **keep
only the last version**: restic never rewrites a file in place, so B2 versioning
buys nothing and quietly accumulates against the 10 GB free tier.

### Running it

```bash
cd backend
export TT_B2_BUCKET=<bucket>                       # or pass --bucket

uv run python scripts/backup_offbox.py init        # once, creates the repository
uv run python scripts/backup_offbox.py backup
uv run python scripts/backup_offbox.py snapshots
uv run python scripts/backup_offbox.py check       # repository integrity
uv run python scripts/backup_offbox.py restore --target ~/restore
```

`backup` validates every source **before** staging and before anything reaches
restic. Restic treats a vanished path as a warning and exits 3 having backed up
the rest; a silent partial backup is the failure this whole section exists to
prevent, so a missing tree refuses the run outright. Retention afterwards is
`--keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune`, and a `forget`
failure still fails the job even though the upload succeeded.

Every failure prints a `BACKUP FAILED` banner and exits non-zero.

### Verifying a remote backup

The restore drill is storage-agnostic on purpose — point it at what restic gave
back:

```bash
uv run python scripts/backup_offbox.py restore --target /tmp/b2-restore
uv run python scripts/restore_drill.py \
  --snapshot-dir /tmp/b2-restore/Users/<you>/.tunatale/offbox-staging \
  --media-src    /tmp/b2-restore/<repo>/backend/media \
  --output-src   /tmp/b2-restore/<repo>/backend/output \
  --scratch      /tmp/restore-drill
```

restic restores into the target under each source's absolute path, which is why
the three flags carry that prefix.

### Drill results — 2026-09-11, restored onto a DIFFERENT machine

The drills below were all run on the Mac that made the backup. **That is not the
disaster scenario.** This one was run on the GCP box, which has never held the
Mac's Keychain.

**The custody chain holds.** The restic passphrase *and* the B2 application key
both came from Bitwarden. Nothing was copied off the laptop, so the recovery does
not depend on the machine being replaced — which is the claim every other
measurement here rests on.

| Step | Wall clock | Detail |
|---|---|---|
| `restore` (download) | **37.6 s** | 615 MB, 9 060 files |
| Drill on the restored tree | 27 m 14 s | DBs 0.2 s, trees 88 s, 8 567 checksums 18 s, **104 full decodes 1 436 s** |

The drill is slow here and that is not a regression: ~14 s per audio file on
0.25 vCPU, against ~0.8 s on the Mac. The download is not the RTO's main term —
38 s of transfer against minutes of verification.

**Four defects surfaced, none of them findable at home.** This is the return on
running it elsewhere:

1. **`audio_files.file_path` stores absolute `/Users/<author>/…` paths** — 100
   of 104 rows — and `audio.py` serves them with no re-rooting, so lesson audio
   404s on the box. `tunatale-kbb.15`, blocks the data migration and cutover.
2. **Three media rows are case-mismatched** (`sl_zemlja.mp3` recorded,
   `sl_Zemlja.mp3` on disk). macOS is case-insensitive and resolves them; ext4
   does not. `tunatale-kbb.14`.
3. **The drill's own audio check was vacuous on the source machine.** It
   resolved `tree_root / file_path`, and pathlib discards `tree_root` when the
   right operand is absolute — so it decoded the **originals** and passed while
   testing nothing about the restore. Fixed in `51270cb`. ⚠️ **The
   "48/48 decoded end to end" line in the 2026-08-12 results below is therefore
   not evidence about that backup**, and cannot retroactively be made into
   evidence: those rows no longer exist.
4. **Four Norwegian lessons have a truncated full-lesson audio file** — 267–335 s
   of audio under a 1 421–3 619 s caption timeline, sections all intact.
   Pre-existing on the Mac, faithfully backed up. `tunatale-c7tx`.

Two traps worth knowing before a real recovery:

- **`snapshots found — 0` also means "I could not read the directory."** restic
  restores macOS `0700` modes, so the container user could not read the restored
  tree at all; the drill reported an empty directory, which is indistinguishable
  from an empty backup. Run the drill as root against a restored tree.
- **Ubuntu's apt ships restic 0.16.4**, against a repository written by 0.19.1.
  Install the official binary (checksum-verified) rather than `apt install
  restic`, which is what a person in a hurry reaches for.

### Drill results — 2026-08-12, restored from B2

Performed against `b2:tunatale-backups:tunatale`, the first real upload. Same
machine, same drill script, same day as the local-snapshot drill above — so the
two tables are directly comparable.

| Step | Wall clock | Detail |
|---|---|---|
| `backup` (upload) | 32.7 s | 7576 files, 403.8 MiB read, **266.8 MiB stored** after compression |
| `check` (repository) | 2 s | `no errors were found` |
| `restore` (download) | 32.8 s | 7586 files/dirs, 403.8 MiB, 28 s of it transfer |
| Drill on the restored tree | 44.4 s | DBs 0.01 s, trees 3.00 s, 6001 checksums 0.29 s, 48 full decodes 40.23 s |
| **Total, bucket → verified** | **≈ 77 s** | |

**The download did not dominate.** Phase 1 predicted the transfer would be the
RTO's main term; measured, it is 28 s against 40 s of audio decoding. At 403 MiB
this is a sub-two-minute recovery on a home connection — the number the
provisioning runbook should state, with the caveat that it is bandwidth-bound
and a datacenter restore will differ in whichever direction that box's link does.

Compression is worth noting for capacity planning: 403.8 MiB of sources occupy
266.8 MiB in the repository, so the 10 GB free tier holds roughly 38 full copies
before deduplication is even counted. Retention is not close to binding.

Verified on the restored copy:

- `integrity_check` — `ok` on both DBs
- row counts **identical to the local drill**: Norwegian 37253 rows across 17
  tables (3014 collocations, 3033 directions, 24184 `tt_revlog`, 6001 media, 6
  lessons, 1 curriculum); Slovene 20636 rows across 17 tables
- 6001/6001 Norwegian media files matched their recorded sha256
- 48/48 lesson audio files decoded end to end across 6 lessons (7.4 h), 0 decode
  errors, 0 caption overruns
- Booted the app on port 8099 against the restored DB. `/api/srs/stats`
  `{"total":3014,"due_today":94}`, `/api/srs/queue-stats`
  `{"new":3,"learning":0,"review":95,...}` and the curriculum list
  (`reading-sn-mannen-022a5dc3`, created `2026-08-02 01:22:20`) were **identical**
  to the live server queried the same minute

The first run of this drill exited **non-zero** on one line — Slovene media
`1378/1379 matched, 1 missing (img_cup.jpg)`, i.e. Known gap #3 above, faithfully
reproduced from the live tree. That file was recovered from Anki's
`collection.media` (see gap #3 for why that was the right fix and the plausible
wrong one), and the drill was re-run end to end against a fresh backup and a
fresh restore:

| Step | Wall clock | Detail |
|---|---|---|
| `backup` #2 (incremental) | **7.5 s** | one new 45 KB file; everything else deduplicated |
| `restore` | 29.4 s | 7587 files/dirs, 403.9 MiB, 26 s of it transfer |
| Drill | 42.5 s | DBs 0.02 s, trees 2.65 s, checksums 0.22 s, 48 decodes 38.83 s |

`=== DRILL PASSED ===`, exit 0, with Slovene media at **1379/1379 matched, 0
missing, 0 mismatched** and every other check unchanged.

Two things that run tells you beyond the fix itself. The incremental backup cost
**7.5 s against 32.7 s** for the initial one, so a daily job is cheap and the
free tier is nowhere near binding. And because the drill now exits 0 on this
data, it is usable as a monitor's pass/fail signal — a scheduled job can gate on
it directly, with no allowance list of accepted pre-existing failures.

The `[note] 41 pre-existing FK orphan row(s)` line is deliberately a note and not
a failure (gap #2); it does not affect the exit code.

### Scheduling — the LaunchAgent

```bash
cd backend
uv run python scripts/install_backup_agent.py --bucket tunatale-backups
uv run python scripts/install_backup_agent.py --bucket X --dry-run   # inspect the plist
uv run python scripts/install_backup_agent.py --uninstall
```

Installed as `com.tunatale.backup`, daily at 03:30 local, logging to
`~/.tunatale/logs/backup.log`. Check it with `launchctl list | grep tunatale` —
the second column is the **last exit status**, so `0` means the most recent run
succeeded and `1` means it did not.

**launchd, not cron.** The laptop is asleep at 03:30. cron silently skips the
window and never catches up; `StartCalendarInterval` fires the job when the
machine next wakes.

**Everything the plist carries, it carries because a LaunchAgent inherits
nothing.** Measured with a throwaway probe agent (2026-08-12) rather than
assumed:

| What the agent gets | Consequence |
|---|---|
| `PATH=/usr/bin:/bin:/usr/sbin:/sbin` | Neither `uv` (`~/.local/bin`) nor `restic` (`/opt/homebrew/bin`) is on it. The plist calls `uv` by absolute path and puts both directories on `PATH`. |
| No environment at all | `TT_B2_BUCKET` is written into the plist's `EnvironmentVariables`. |
| Keychain: **works** | `security find-generic-password` returns 0 unattended — a generic-password item's default ACL trusts `/usr/bin/security`, and the agent invokes that same binary. This was the main thing feared and it is a non-issue. |

Tool paths are resolved with `shutil.which` at install time, never hardcoded
(Homebrew differs between Apple silicon and Intel), and a missing tool
**refuses to install**. An agent that cannot run is worse than no agent: the
calendar entry makes the job look covered.

`RunAtLoad` is `False`, so installing does not fire a backup and verifying the
install is not a destructive act. To run one on demand:
`launchctl kickstart -w gui/$UID/com.tunatale.backup`.

No credential is in the plist — it is a world-readable file in the home
directory. Everything comes from the Keychain at run time.

### Verified 2026-08-12, and what is still unverified

Run through the agent, not by hand — that distinction is the whole point:

- ✅ **Success path.** Kickstarted the agent; it staged both DB snapshots,
  uploaded, pruned an expired snapshot, and launchd recorded exit status `0`.
- ✅ **Failure detection AND delivery.** Reinstalled against a nonexistent
  bucket, kickstarted: `Fatal: unable to open repository … 401`, `BACKUP
  FAILED: restic backup returned non-zero`, launchd exit status **`1`**, the
  marker file written, and TextEdit opened showing it. Then reinstalled
  correctly and re-ran: exit `0` and the marker deleted.
- ✅ **Agent environment.** Bucket and both Keychain items resolve unattended.

### ⚠️ Desktop notifications DO NOT WORK here — do not rely on them

Tested with a human watching the screen, 2026-08-12. `osascript display
notification` **returned 0 and no banner appeared**, from a terminal *and* from
the LaunchAgent. Not Focus mode (no Focus DB, no legacy DND flag,
NotificationCenter running) — macOS drops notifications silently when the
calling process lacks notification permission, and the permission database is
TCC-protected so a job cannot even tell.

This is the worst possible shape for an alerting mechanism: **it reports
success while doing nothing**, and it is grantable and revocable outside the
program.

It is also the wrong mechanism regardless. The job runs at 03:30. A transient
banner posted while you are asleep is collected and gone by morning, so even a
working notification would not reliably reach you.

**So the load-bearing signal is a file, not a banner:**
`~/.tunatale/BACKUP-FAILED.txt`, written on any failure with the timestamp, the
cause, and the commands to investigate. It is then handed to `open`, which
launches the default app — launching an app needs no permission. A window still
sitting there in the morning beats a 3am notification.

The marker **persists until a backup actually succeeds**; the next successful
run deletes it, unconditionally and regardless of `--notify`, because a signal
that stays red after the problem is fixed is one you learn to ignore.

The `osascript` call is kept as a free bonus for whenever permission is granted.
Nothing depends on it.

Verified end to end through the agent:

| | |
|---|---|
| forced failure | marker written, **TextEdit opened with it**, launchd exit `1` |
| next success | marker deleted, launchd exit `0` |

The TextEdit window was confirmed **by a human watching the screen**, not merely
by a zero exit code — which is the whole distinction that sank the notification
path an hour earlier.

⚠️ **UNVERIFIED — the sleep/wake catch-up.** The reason for choosing launchd
over cron is untested here: nobody has yet closed the lid across 03:30 and
confirmed the run fired on wake. It is documented launchd behaviour, not a
measurement.

### Not done yet

The restore has only ever been performed on the Mac that made the backup.
Restoring onto a *different* box — the actual disaster scenario — additionally
requires the passphrase out of the password manager, which is precisely the step
this arrangement is designed around and the one thing never exercised.

## Schema rollback

### Image rollback is not schema rollback

"Roll back to a previous SHA in one command" is true of the image and false of
the data. Starting a build runs `app/srs/migrations.py::migrate` against the
existing volume, so a deploy that advances the schema and is then rolled back
leaves a **newer schema under older code** — a different and worse failure than
the one being rolled back from. The schema only ever moves forward: `migrate`'s
loop is `while version < CURRENT_VERSION`, and there are no down-migrations.

Two mechanisms close that gap.

**1. A pre-migration snapshot.** Before the first pending migration runs,
`migrate` copies the database to
`{migration_backup_dir}/{stem}.pre-v{N}.db`, where `N` is the version being
left behind — so the filename says which build can still open it. Written via
the same online-backup API as the rolling snapshots (`db_backup.py::_snapshot`),
so the file is self-contained; a restore copies the `.db` alone and ignores any
`-wal`/`-shm` beside it.

Three properties are deliberate and each has a test in
`backend/tests/test_pre_migration_backup.py`:

- **It never rotates.** `migration_backup_dir` defaults to
  `~/.tunatale/pre-migration-backups`, separate from the rolling
  `~/.tunatale/db-backups`, which keeps only `db_backup_keep_days` (5). You
  find out you need a pre-migration snapshot long after that window. Pruning is
  additionally scoped to date-shaped filenames, so co-locating the two
  directories would still be safe.
- **The first snapshot of a version wins.** A migration that fails half-way
  leaves the DB mutated; the retry re-enters at the same version and must not
  copy the damaged file over the good one.
- **A failure to snapshot aborts the migration.** This is the opposite of
  `rotate_db_backups`, which swallows every error so a backup hiccup cannot
  block startup. Here, migrating without a snapshot would destroy the only copy
  of the pre-migration state — the precise outcome the mechanism exists to
  prevent. Since it only fires when a migration is actually pending, the
  availability cost is bounded to deploys that change the schema.

**2. A refusal to run older code against a newer DB.** `migrate` raises
`SchemaTooNewError` when `PRAGMA user_version` exceeds the build's
`CURRENT_VERSION`, naming both versions and the snapshot that would fix it.
Refusing to start beats a silent mixed-state boot.

Check it *before* swapping, so a bad rollback declines instead of crash-looping:

```bash
cd backend && uv run python scripts/check_schema_compat.py
# exit 0 = safe to start; 1 = a DB is ahead of this build; 2 = a named DB is missing
```

The script imports the same comparison the runtime guard uses rather than
reimplementing it, and reads `PRAGMA user_version` straight off the files — it
needs nothing running.

### Which migrations are reversible

**Reversible**, below, means: after the migration has run, an older build (one
whose `CURRENT_VERSION` is the pre-migration number) can be pointed back at the
*same file* — after resetting `PRAGMA user_version` — without losing data it
knew about. It does **not** mean a down-migration exists; none does.

Three classes:

- **Additive** — new column, table, or index only. The older build ignores it.
  Reversible.
- **Backfill** — writes rows, but only where the target was `NULL` or empty. No
  pre-existing value is destroyed, so nothing is lost; it is not *mechanically*
  undoable, because nothing records which rows were blank.
- **Destructive** — drops, deletes, or overwrites pre-existing values, or
  rebuilds a table. **Not reversible: the pre-migration snapshot IS the
  rollback.**

29 of the 46 are additive, 3 are backfills, and 14 are destructive.

| From → to | Class | What it does |
|---|---|---|
| v0 → v1 | Additive | `collocations.lemma` + index |
| v1 → v2 | **Destructive** | Splits FSRS state into `collocation_directions`; rebuilds `collocations`; drops `_collocations_v1`; synthesizes a production direction per row |
| v2 → v3 | **Destructive** | Rewrites `collocations.text` (strips the `(suffix)` into a new `disambig_key`) and recomputes every guid. The original `text` values do not survive |
| v3 → v4 | **Destructive** | Rebuilds `collocation_directions` / `media` / `collocation_tags` to repair FK targets. Every row is copied — no data loss — but the pre-repair tables are dropped |
| v4 → v5 | Additive | `last_rating` |
| v5 → v6 | Additive | `anki_due` |
| v6 → v7 | Additive | `grammar`, `note` |
| v7 → v8 | Additive | `source_sentence`, `source_lesson_id`, `source_line_index` |
| v8 → v9 | **Destructive** | `DROP TABLE pending_revlog` (+ index). Unused at the time, but the drop is unconditional |
| v9 → v10 | Additive | `last_review_time_ms` |
| v10 → v11 | Additive | `left`, `due_at` |
| v11 → v12 | **Destructive** | Nulls `last_review` on `state='new' AND reps=0` rows. The prior timestamps are gone |
| v12 → v13 | Additive | `prior_state`, `prior_left`, `prior_stability` |
| v13 → v14 | Additive | `anki_card_mod` |
| v14 → v15 | Backfill | `lemma = LOWER(text)` for single-word rows, `WHERE lemma IS NULL` |
| v15 → v16 | **Destructive** | Deletes phantom direction rows (the `_build_directions` auto-fill residue) |
| v16 → v17 | Additive | Index on `collocations.created_at` |
| v17 → v18 | Additive | `introduced_at` + index |
| v18 → v19 | Additive | `card_type` |
| v19 → v20 | Additive | `bury_kind`; its backfill writes only into that new column |
| v20 → v21 | Additive | `sentence_translation` |
| v21 → v22 | **Destructive** | Rebuilds `media` to drop the `kind` CHECK. All rows copied; old table dropped |
| v22 → v23 | **Destructive** | Appends `audio` to `collocations.dirty_fields` on matching rows — including non-empty ones |
| v23 → v24 | Backfill | Fills `due_at` from `due_date`, `WHERE due_at IS NULL`. `due_date` still exists at this version |
| v24 → v25 | **Destructive** | Drops `collocation_directions.due_date`. The clearest one-way door in the chain |
| v25 → v26 | Additive | `tt_revlog` table |
| v26 → v27 | Additive | `stability_replayed`, `fsrs_difficulty_replayed` |
| v27 → v28 | Additive | `lemma_key` |
| v28 → v29 | Backfill | Fills `grammar` for inflection clozes, `WHERE grammar IS NULL OR grammar = ''` |
| v29 → v30 | Additive | `ignored_lemmas` table |
| v30 → v31 | Additive | `known_prior_state`, `known_prior_stability`, `known_prior_due_at`, `fsrs_force_next` |
| v31 → v32 | **Destructive** | Drops `stability_replayed` / `fsrs_difficulty_replayed` |
| v32 → v33 | Additive | `article` |
| v33 → v34 | Additive | `extras` |
| v34 → v35 | **Destructive** | Rebuilds `collocation_directions` to add CHECK domains, and nulls out-of-domain `prior_state` / `bury_kind` |
| v35 → v36 | **Destructive** | Overwrites `word_count` to 1 for comma-separated spelling-variant fronts |
| v36 → v37 | Additive | `media.mtime_ns` + index on `collocations.anki_note_id` |
| v37 → v38 | Additive | `lesson_listens` table |
| v38 → v39 | Additive | `lesson_reviews` table |
| v39 → v40 | Additive | `tt_revlog.budget_neutral` |
| v40 → v41 | Additive | `pending_listen_grades` table |
| v41 → v42 | **Destructive** | Rebuilds `pending_listen_grades` to widen the UNIQUE key to `(lesson_id, collocation_id, direction)` |
| v42 → v43 | Additive | `collocations.base_collocation_id` + index — links a base cloze to the word whose production it carries |
| v43 → v44 | Additive | `collocations.image_unavailable_at` — the pre-stage's verdict that a word cannot be pictured, which the mint reads instead of fetching |
| v44 → v45 | Additive | `cloze_sentence_cache` — LLM-written cloze sentences for the closed-class words whose own notes carry no clozable example, written off the critical path because the mint makes no network call |
| v45 → v46 | **Destructive** | Deletes `media` rows whose collocation no longer exists — 8 on the Norwegian deck, debris from table rebuilds run under `PRAGMA foreign_keys = OFF`. Destructive by class, not by risk: the rows point at nothing, and the files they named are left on disk |

`test_pre_migration_backup.py::TestReversibilityIsDocumented` fails if a new
migration lands without a row here, so the table cannot silently fall behind
`CURRENT_VERSION`.

### Restoring

```bash
# 1. Refuse-check the build you are rolling back to.
cd backend && uv run python scripts/check_schema_compat.py

# 2. If it refuses, restore the snapshot it names. Stop the app first.
cp ~/.tunatale/pre-migration-backups/tunatale_sl.pre-v41.db backend/tunatale_sl.db

# 3. Re-check: it should now report ok (or "pending", if you rolled forward again).
uv run python scripts/check_schema_compat.py
```

Copy the `.db` alone — see *Snapshots are self-contained* above.

**What this costs.** Restoring a pre-migration snapshot discards every write
made since that migration ran. For a destructive migration there is no better
option; for an additive one you do not need the snapshot at all — reset
`PRAGMA user_version` to the older build's number and the only thing lost is
whatever the new column held. Reach for the snapshot when the table above says
**Destructive**.
