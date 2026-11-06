# Hosting Stardust Core

One small Linux server runs Core, Caddy (automatic HTTPS) and a nightly backup, with Docker Compose.
Core is a single process with a SQLite file, so it needs a real persistent disk, not a serverless platform.

## Production: live at https://core.aifore.org

The hosted Core has been live in production since 2026-10-30, on Oracle Cloud (Oracle Linux 9, Arm64, reserved IP `129.153.92.171`), with Docker Compose and Caddy as described below.

- **Health check:** `curl -s https://core.aifore.org/healthz` returns `"ok": true`, `priced_models` (over 3,000), and `"ingest_auth": "required"` and `"subscriber_auth": "required"`.
- **Admin endpoints** (`/v1/admin/*`) return 404 from the internet.
- **Uptime:** the [`core-uptime`](../.github/workflows/core-uptime.yml) workflow checks both every day at 17:07 UTC. A failure opens (or comments on) a GitHub issue labeled `ops-alert` and posts to #ops-alerts on Slack; the next good run closes the issue.

## Hosts

- **Production host (current):** Oracle Cloud Always Free, one Ampere A1 VM (Arm64), 1 OCPU and 6 GB or more. $0. Core runs comfortably in 1 OCPU / 6 GB (about 100 MB used by all three containers).
- **Possible long-term host:** Azure, paid from AIforE's nonprofit Azure grant. Same steps on an Ubuntu VM.

Moving between them: stop Core, copy `core.db` from the volume, start Core on the new server.

## 1. Create the server (Oracle Cloud)

1. Sign up for Oracle Cloud with an AIforE address. Pick a home region close to users (e.g. US West, Phoenix or San Jose); it can't be changed later.
2. **Compute → Instances → Create instance.** Image: Canonical Ubuntu 24.04. Shape: Ampere, `VM.Standard.A1.Flex`, at the largest size the console marks Always Free (1 OCPU / 6 GB is enough). Don't pick a size the console shows a price for. Add your SSH public key. If you get "out of capacity", try another availability domain or retry later.
3. **Networking:** in the instance's subnet security list, add ingress rules for TCP 80 and 443 from `0.0.0.0/0`. Ubuntu images on Oracle also block ports in iptables; on the server run:

   ```bash
   sudo iptables -I INPUT 6 -p tcp -m multiport --dports 80,443 -j ACCEPT && sudo netfilter-persistent save
   ```
4. Note the instance's public IP and reserve it (Networking → Reserved public IPs) so it doesn't change.

## 2. Point the domain at it

Wherever aifore.org's DNS is managed, add an **A record**: `core` → the server's public IP. The default in the store build is `core.aifore.org`; to use another name, set `STARDUST_DOMAIN` below and `STARDUST_API_BASE` for the extension build.

## 3. Install and start

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker
git clone https://github.com/AIforEcology/stardust && cd stardust/deploy
cp .env.example .env
sed -i "s/^STARDUST_ADMIN_TOKEN=.*/STARDUST_ADMIN_TOKEN=$(openssl rand -hex 32)/" .env
docker compose up -d --build
curl -s https://core.aifore.org/healthz
```

`/healthz` should show `"ingest_auth": "required"`, `"subscriber_auth": "required"` and `"self_registration": true`. Caddy gets the HTTPS certificate automatically on first request (DNS must already point at the server).

### Oracle Linux 9 instead of Ubuntu

Oracle's default image is Oracle Linux 9 (user `opc`). Tested on Oracle Linux 9.8, aarch64. Replace steps 1.3 and 3 with:

```bash
sudo dnf -y update && sudo systemctl reboot   # reboot if a new kernel was installed
sudo dnf -y install dnf-plugins-core
sudo dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin git dnf-automatic
sudo systemctl enable --now docker
sudo usermod -aG docker opc && newgrp docker
# Automatic security updates
sudo sed -i "s/^upgrade_type.*/upgrade_type = security/; s/^apply_updates.*/apply_updates = yes/" /etc/dnf/automatic.conf
sudo systemctl enable --now dnf-automatic.timer
# The OS firewall is firewalld, not iptables
sudo firewall-cmd --permanent --add-service=http --add-service=https && sudo firewall-cmd --reload
```

Then clone, create `.env` and run `docker compose up -d --build` as in step 3.

**SELinux:** Oracle Linux runs SELinux in enforcing mode, but Docker CE leaves its SELinux support off by default, so the `./Caddyfile` and `./backups` bind mounts work without a `:Z` label. If you turn on `"selinux-enabled": true` in `/etc/docker/daemon.json`, add `:Z` to both mounts in `docker-compose.yml`.

## 4. Update

```bash
cd ~/stardust && git pull && cd deploy && docker compose up -d --build
```

The database lives in the `core-data` volume and survives rebuilds.

**If the update changed `deploy/Caddyfile`**, also run `docker compose restart caddy` (Core is unreachable for a second or two). `up -d` doesn't restart Caddy for a config change, and because `git pull` replaces the file, the bind-mounted copy inside the running container stays the old one until Caddy restarts.

## Security

- Every client needs a key. Browser extension installs get their own from `POST /v1/installs`: user-level, able to send its own usage and read its own totals, nothing else. Registrations are limited per IP (`STARDUST_REGISTRATIONS_PER_IP_PER_HOUR`).
- `/v1/admin/*` is blocked at Caddy, so it is never reachable from the internet. Use it from the server:

  ```bash
  docker compose exec core sh -c 'curl -s -H "X-Stardust-Admin-Token: $STARDUST_ADMIN_TOKEN" localhost:8080/v1/admin/keys'
  ```
- Revoke an abusive install: `.../v1/admin/keys/<key_id>/revoke` the same way.
- Keep the OS patched: `sudo apt-get install -y unattended-upgrades` (Ubuntu) or `dnf-automatic` (Oracle Linux, above).

## Backups

The `backup` service writes `deploy/backups/core-YYYYMMDD.db` nightly and keeps 14. Copy them off the server too, for example to the AIforE shared Drive or Oracle Object Storage (20 GB free). Restore: stop Core, copy a backup to the volume as `core.db`, start Core.

### Off-server copies (Oracle Object Storage)

`offsite-backup.sh` copies each finished nightly backup, gzipped, to a private Object Storage bucket every night at 02:30 UTC. The server signs in as itself (instance principal), so no keys are stored on it. The policy only lets it list and add objects, so a compromised server can't overwrite or delete the copies.

In the Oracle console (home region):

1. **Storage → Buckets → Create bucket** `stardust-core-backups`, Standard tier, in the instance's compartment. Leave it private.
2. **Identity & Security → Domains → Default → Dynamic groups → Create** `stardust-core`, with the rule `instance.id = '<the instance OCID>'`.
3. **Identity & Security → Policies → Create policy** in the root compartment, named `stardust-core-backups`:

   ```
   Allow dynamic-group stardust-core to inspect objects in tenancy where target.bucket.name = 'stardust-core-backups'
   Allow dynamic-group stardust-core to manage objects in tenancy where all {target.bucket.name = 'stardust-core-backups', request.permission = 'OBJECT_CREATE'}
   Allow service objectstorage-<region-id> to manage object-family in tenancy
   ```

   The third line lets Object Storage delete old copies for the lifecycle rule. Use your region's identifier, for example `objectstorage-us-phoenix-1`.
4. In the bucket, **Lifecycle policy rules → Create rule**: Delete, objects older than 90 days, prefix `core-`.

On the server (Oracle Linux):

```bash
sudo dnf -y install python39-oci-cli
sudo cp ~/stardust/deploy/systemd/stardust-offsite-backup.* /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now stardust-offsite-backup.timer
sudo systemctl start stardust-offsite-backup.service && journalctl -u stardust-offsite-backup -n 20
```

Restore from a copy: `oci os object get --auth instance_principal -bn stardust-core-backups --name core-YYYYMMDD.db.gz --file core.db.gz && gunzip core.db.gz`, then restore it as above.
The copies stay well inside Always Free's 20 GB of Object Storage: a compressed backup is tens of KB now, and 90 days are kept.

The same script also uploads the ledger's `ledger-YYYYMMDD.dump` files (next section). The policy above is scoped by bucket name, so it already covers them: no IAM change is needed. **Add a second lifecycle rule** in the bucket: Delete, objects older than 90 days, prefix `ledger-`.

## Data Center Resource Ledger

The ledger ([design, schema and API](../docs/architecture/ledger.md)) runs on the same server as three more Compose services: `ledger-db` (PostgreSQL 17 + PostGIS 3, built from `ledger-db.Dockerfile` because `postgis/postgis` has no arm64 image), `ledger` (the API) and `ledger-backup`. Caddy serves it at `https://core.aifore.org/ledger/` and returns 404 for `/ledger/v1/admin/*`. It is separate from Core: Core's code, database and routes don't change, and if the ledger is down Core keeps running. The database port is never published. `ledger-db` is capped at 1.5 GB of memory (`shared_buffers=256MB`, `max_connections=30`).

### First deploy

```bash
ssh opc@core.aifore.org
cd ~/stardust && git pull
cd deploy
echo "LEDGER_DB_PASSWORD=$(openssl rand -hex 24)" >> .env
echo "LEDGER_ADMIN_TOKEN=$(openssl rand -hex 32)" >> .env
docker compose up -d --build
docker compose restart caddy                     # load the new /ledger routes (see 4. Update)
curl -s https://core.aifore.org/healthz          # Core still ok
curl -s https://core.aifore.org/ledger/healthz   # {"ok": true, "service": "ledger", ...}
curl -s -o /dev/null -w "%{http_code}\n" https://core.aifore.org/ledger/v1/admin/keys   # must be 404
curl -s -o /dev/null -w "%{http_code}\n" https://core.aifore.org/v1/admin/keys          # still 404
```

Run the commands one at a time, and each `echo` line only once: running it again adds a duplicate line to `.env`, and a new `LEDGER_DB_PASSWORD` would no longer match the one the database was created with. Without the Caddy restart, `/ledger/...` returns Core's 404 (`{"detail":"Not Found"}`) because the old Caddy config sends it to Core.

The password is read when the database volume is first created. Changing `LEDGER_DB_PASSWORD` later also needs `docker compose exec ledger-db psql -U ledger -c "ALTER USER ledger PASSWORD '<new>'"`, then `docker compose up -d ledger ledger-backup`. The first build of `ledger-db` takes a few minutes on the A1.

### Write keys for the agents

Create one key per agent from the server, with only the scopes it needs (`observations:write`, `facilities:write`, `tracker:write`, `runlog:write`, `corrections:write`; `admin` only for people). [`docs/architecture/ledger.md`](../docs/architecture/ledger.md#what-agents-can-do) lists which agent needs which. The key is shown once; only its SHA-256 hash is stored.

```bash
docker compose exec ledger sh -c 'curl -s -X POST localhost:8090/v1/admin/keys \
  -H "X-Ledger-Admin-Token: $LEDGER_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d "{\"scopes\": [\"observations:write\", \"runlog:write\"], \"label\": \"daily scan agent\"}"'
```

Store each key as a secret in the agents' cloud environment (for example `LEDGER_KEY`), never in a prompt, the sheet or the repository. Agents send it as `X-Ledger-Key`.

**Change an existing key's scopes** (the agent keeps its secret; audited):

```bash
docker compose exec ledger sh -c 'curl -s -X POST -H "X-Ledger-Admin-Token: $LEDGER_ADMIN_TOKEN" -H "Content-Type: application/json" \
  localhost:8090/v1/admin/keys/<key_id>/scopes -d "{\"add\": [\"facilities:write\"]}"'
```

**Rotate a key:** create the new key, update the agent's secret, then revoke the old one:

```bash
docker compose exec ledger sh -c 'curl -s -H "X-Ledger-Admin-Token: $LEDGER_ADMIN_TOKEN" localhost:8090/v1/admin/keys'
docker compose exec ledger sh -c 'curl -s -X POST -H "X-Ledger-Admin-Token: $LEDGER_ADMIN_TOKEN" localhost:8090/v1/admin/keys/<key_id>/revoke'
```

**Rotate the admin token:** put a new `LEDGER_ADMIN_TOKEN` in `.env` and run `docker compose up -d ledger`.

### Import the sheet (once)

Export each tab of the sheet as CSV (File → Download → Comma-separated values) into a folder on your computer, copy it to the server, and load it. Keep the folder out of the repository.

```bash
scp -r sheet-export opc@core.aifore.org:/tmp/
docker compose cp /tmp/sheet-export ledger:/tmp/sheet-export
docker compose exec ledger python /app/ledger/scripts/import_sheet.py /tmp/sheet-export --dry-run --report /tmp/import-report
docker compose exec ledger python /app/ledger/scripts/import_sheet.py /tmp/sheet-export --dsn "" --report /tmp/import-report
docker compose exec ledger cat /tmp/import-report.md
```

`--dsn ""` uses the container's database settings. It keeps every ID and can be re-run safely. Delete `/tmp/sheet-export` from the server afterwards.

### Update

Same as Core: `cd ~/stardust && git pull && cd deploy && docker compose up -d --build`. Restart Caddy too if `deploy/Caddyfile` changed. New migrations in `ledger/migrations/` apply when the ledger starts. Data lives in the `ledger-data` volume and survives rebuilds.

### Backups and restore

`ledger-backup` writes `deploy/backups/ledger-YYYYMMDD.dump` (`pg_dump -Fc`) every night and keeps 14. `offsite-backup.sh` uploads them to the `stardust-core-backups` bucket with `--no-overwrite`, beside Core's copies.

Restore (replaces the ledger's data):

```bash
cd ~/stardust/deploy
# from the bucket, if needed:
oci os object get --auth instance_principal -bn stardust-core-backups --name ledger-YYYYMMDD.dump --file backups/ledger-YYYYMMDD.dump
docker compose stop ledger ledger-backup
docker compose exec -T ledger-db pg_restore -U ledger -d ledger --clean --if-exists --no-owner < backups/ledger-YYYYMMDD.dump
docker compose start ledger ledger-backup
curl -s https://core.aifore.org/ledger/healthz
```

## Cost checks

Oracle: stay on shapes and sizes the console marks Always Free, and within 200 GB of block storage. Set a budget alert at $1 under Billing so any accidental paid resource shows up at once.
