# Deployment Plan

**Target:** Windows 11 Enterprise VM at `192.168.70.180`
**DB + SMB source:** stays at `192.168.70.10` (Postgres `clientfiles_v2` + `\\192.168.70.10\Files\...`)
**Reverse proxy:** none — Next.js production server binds port 80 and proxies `/api/*` to FastAPI on `localhost:8000` via `next.config.ts` (same behavior as dev).
**Service manager:** [nssm](https://nssm.cc/) — two auto-start Windows services, one per process.
**Deployment root:** `C:\apps\cfv\`

Server change later = edit `.env` and restart the two services. Nothing hardcoded.

---

## 0 · Prerequisites checklist

Before starting, have on hand:

- [ ] Windows 11 Enterprise VM provisioned per specs (4 vCPU / 8 GB RAM / 128 GB SSD / static IP `192.168.70.180`).
- [ ] Local admin account on the VM (for install + nssm).
- [ ] Network reachability verified from the VM: `ping 192.168.70.10`, plus `telnet 192.168.70.10 5432` (Postgres) and access to at least one `\\PCx\C$` share.
- [ ] Postgres server on `192.168.70.10` accepts connections from `192.168.70.180` — check `pg_hba.conf` on the DB box (see §4b).
- [ ] SMTP creds on hand for the same account already used in dev `.env`.

---

## 1 · Host prep (one-time)

On the fresh VM, in an elevated PowerShell:

```powershell
# Static IP (adjust adapter name via `Get-NetAdapter`)
$if = "Ethernet"
New-NetIPAddress -InterfaceAlias $if -IPAddress 192.168.70.180 -PrefixLength 24 -DefaultGateway 192.168.70.1
Set-DnsClientServerAddress -InterfaceAlias $if -ServerAddresses 192.168.70.10, 8.8.8.8

# Firewall: allow inbound HTTP from the LAN (adjust RemoteAddress to your subnet)
New-NetFirewallRule -DisplayName "CFV HTTP (LAN)" -Direction Inbound -Protocol TCP `
  -LocalPort 80 -Action Allow -RemoteAddress 192.168.70.0/24

# Long-path support — some SMB paths on the source PCs exceed 260 chars
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
  -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force

# Time zone (matches APP_TIMEZONE default)
Set-TimeZone -Id "Eastern Standard Time"
```

Optional: enable Remote Desktop for headless admin (`sysdm.cpl` → Remote → Allow).

---

## 2 · Install runtimes (one-time)

**All installers below** — download to the VM, run as admin, leave defaults except where noted. All should offer "Add to PATH" — accept.

| Runtime | Version | Source |
|---|---|---|
| Git for Windows | latest | https://git-scm.com/download/win |
| Python | **3.12.x** (64-bit) | https://www.python.org/downloads/windows/ — check "Add to PATH" |
| Node.js | **24 LTS** (64-bit) | https://nodejs.org/en/download/ |
| nssm | 2.24 | https://nssm.cc/download — extract `win64\nssm.exe` to `C:\Windows\System32\` |

Then in a fresh PowerShell (new PATH):

```powershell
# uv — Python project + venv manager
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# pnpm — via corepack (ships with Node)
corepack enable
corepack prepare pnpm@latest --activate

# Sanity check
python --version   # 3.12.x
node --version     # v24.x
uv --version
pnpm --version
nssm --version
```

---

## 3 · Deploy the code (one-time; §7 covers updates)

```powershell
New-Item -ItemType Directory -Path C:\apps -Force | Out-Null
Set-Location C:\apps

# Clone the repo (adjust the URL / branch to your remote)
git clone <your-git-url> cfv
Set-Location C:\apps\cfv

# Backend deps into a project-local .venv
uv sync

# Frontend deps + production build
Set-Location .\frontend
pnpm install --frozen-lockfile
pnpm build
Set-Location ..
```

Create the log + secrets folders (used by nssm and by the JWT key generator):

```powershell
New-Item -ItemType Directory -Path C:\apps\cfv\logs, C:\apps\cfv\secrets -Force | Out-Null
```

---

## 4 · Fill in `.env` on the target

### 4a · Generate the JWT key pair on this box

Each deployment should sign its own JWTs (fresh key = clean session hygiene, and dev keys stay in dev):

```powershell
Set-Location C:\apps\cfv
uv run python scripts\gen_jwt_key.py --dir C:\apps\cfv\secrets
```

The script prints four `AUTH_JWT_*` / `AUTH_REFRESH_HASH_SECRET` lines — **paste them into `.env` exactly as printed** (quoted, forward-slash paths). `uv`'s dotenv parser silently drops every line after an unquoted backslash, so `AUTH_JWT_PRIVATE_KEY_PATH=C:\apps\...` will make the API fail to start.

### 4b · Write `C:\apps\cfv\.env`

Full key list, grouped by purpose. Copy from dev `.env` where a value already exists (SMTP, SMB creds); regenerate the auth secrets on this box.

```dotenv
# ---- Database ----------------------------------------------------
# Same server as before — this VM just connects across the LAN.
DATABASE_URL=postgresql+asyncpg://<user>:<pass>@192.168.70.10:5432/clientfiles_v2
DATABASE_ADMIN_URL=postgresql://<user>:<pass>@192.168.70.10:5432/clientfiles_v2

# ---- App identity (change this line and .env is the only file to touch) ---
APP_BASE_URL=http://192.168.70.180
ALLOWED_ORIGINS=http://192.168.70.180
APP_TIMEZONE=America/New_York
LOG_LEVEL=INFO
AUTH_COOKIE_SECURE=false          # LAN HTTP — set true when a real cert is in front

# ---- SMB (per-PC scan/commit) ------------------------------------
SMB_USER=<same as dev>
SMB_PASS=<same as dev>

# ---- SMTP (auth mail + reports + PC alerts) ----------------------
SMTP_HOST=<host>
SMTP_PORT=587
SMTP_USER=<user>
SMTP_PASS=<pass>
EMAIL_FROM=<from>
EMAIL_TO=<comma-separated admins>
EMAIL_BRAND_NAME=Client Files Viewer
EMAIL_SUPPORT_LINE=AmericaWorks NYC · Client Files Viewer

# ---- Auth (paste the four lines printed by gen_jwt_key.py in §4a exactly — quotes and forward slashes) ---
AUTH_JWT_KID="k-YYYYMM-xxxx"
AUTH_JWT_PRIVATE_KEY_PATH="C:/apps/cfv/secrets/jwt_k-YYYYMM-xxxx.pem"
AUTH_JWT_PUBLIC_KEY_PATH="C:/apps/cfv/secrets/jwt_k-YYYYMM-xxxx.pub"
AUTH_REFRESH_HASH_SECRET="<hex from gen_jwt_key.py>"

# ---- Health alerter ----------------------------------------------
PC_HEALTH_STALE_DAYS=3
```

Lock the `.env` and secrets to admins only:

```powershell
icacls C:\apps\cfv\.env         /inheritance:r /grant:r Administrators:F SYSTEM:F
icacls C:\apps\cfv\secrets      /inheritance:r /grant:r Administrators:F SYSTEM:F /T
```

### 4c · Postgres firewall on the DB box

On `192.168.70.10`, ensure `pg_hba.conf` has an entry accepting `192.168.70.180`:

```
host    clientfiles_v2    <user>    192.168.70.180/32    scram-sha-256
```

Then reload Postgres. (Dev box already has its own entry — leave it alone.)

---

## 5 · Migrations

Since prod and dev share the same DB, migrations may already be applied. Run head anyway — idempotent:

```powershell
Set-Location C:\apps\cfv
uv run --env-file .env alembic upgrade head
```

Expected: `INFO ... Will assume transactional DDL.` and either "Running upgrade" lines or nothing to do.

---

## 6 · Register the two Windows services

Run as admin from `C:\apps\cfv`. **Adjust paths** if `uv` or `node.exe` didn't install where these commands expect (check with `where uv`, `where node`).

### 6a · Backend — `cfv-api` (FastAPI on port 8000)

```powershell
$UV   = (Get-Command uv).Source
$ROOT = "C:\apps\cfv"

nssm install cfv-api $UV
nssm set cfv-api AppParameters "run --env-file .env uvicorn api:app --host 0.0.0.0 --port 8000"
nssm set cfv-api AppDirectory  $ROOT
nssm set cfv-api AppStdout     "$ROOT\logs\api.out.log"
nssm set cfv-api AppStderr     "$ROOT\logs\api.err.log"
nssm set cfv-api AppRotateFiles 1
nssm set cfv-api AppRotateBytes 10485760       # 10 MB
nssm set cfv-api Start SERVICE_AUTO_START
nssm set cfv-api AppExit Default Restart
nssm set cfv-api AppRestartDelay 5000
nssm set cfv-api Description "Client Files Viewer — FastAPI backend"
```

### 6b · Frontend — `cfv-web` (Next.js on port 80)

```powershell
$NODE = (Get-Command node).Source
$ROOT = "C:\apps\cfv"

nssm install cfv-web $NODE
nssm set cfv-web AppParameters ".\node_modules\next\dist\bin\next start -p 80"
nssm set cfv-web AppDirectory  "$ROOT\frontend"
nssm set cfv-web AppStdout     "$ROOT\logs\web.out.log"
nssm set cfv-web AppStderr     "$ROOT\logs\web.err.log"
nssm set cfv-web AppRotateFiles 1
nssm set cfv-web AppRotateBytes 10485760
nssm set cfv-web Start SERVICE_AUTO_START
nssm set cfv-web AppExit Default Restart
nssm set cfv-web AppRestartDelay 5000
nssm set cfv-web DependOnService cfv-api
nssm set cfv-web Description "Client Files Viewer — Next.js frontend"
```

Both run as **LocalSystem** by default — has the permission to bind port 80 and to open outbound SMB / SMTP / Postgres.

### 6c · Start them

```powershell
nssm start cfv-api
nssm start cfv-web
Get-Service cfv-api, cfv-web
```

Confirm each says **Running**. If `cfv-api` refuses to start, look at `logs\api.err.log` — 99% of the time it's a bad `.env` value or the JWT key path being wrong.

---

## 7 · Smoke test

From another workstation on the LAN:

1. Browse `http://192.168.70.180` → login page renders.
2. Log in as bootstrap admin (`admin@aw.local`).
3. Sidebar loads, dashboard shows tiles.
4. Click **Customize** → widget drawer opens (validates the /me endpoint + custom-dashboard round-trip).
5. Trigger **Run scan** from Quick Actions → log drawer streams progress (validates SSE + SMB creds).
6. Open the **Notifications** bell → any recent events show up.
7. `http://192.168.70.180/api/health` returns `{"status":"ok"}` (validates the reverse-through-Next path).

If all seven pass, deployment is live.

---

## 8 · Ongoing updates

Everything in §3, §5, §6, §7 is baked into **`scripts/deploy.ps1`** — colored progress, idempotent, safe to re-run. To ship a change:

```powershell
C:\apps\cfv\scripts\deploy.ps1
```

It pulls, `uv sync`s, rebuilds the frontend, runs migrations, registers any missing services, restarts, and health-checks. Downtime: ~5 s (API) + ~2 s (web).

You can also run it for the **first deploy** — as soon as §1, §2, §4 are done and the repo is at `C:\apps\cfv`, the script handles the rest.

---

## Rollback

`git log --oneline -20` on the target to find the last known good commit, then:

```powershell
Set-Location C:\apps\cfv
git checkout <sha>
.\scripts\update.ps1
```

If a bad Alembic migration is the issue, downgrade first:

```powershell
uv run --env-file .env alembic downgrade -1
```

then check out the code that matches that revision and re-run `update.ps1`.

---

## Troubleshooting quickref

| Symptom | First place to look |
|---|---|
| Login page 500s | `logs\api.err.log` — usually missing/malformed env var |
| Login page never loads (blank) | `logs\web.err.log` — Node port bind or missing `.next/` build |
| Scan works but PDFs fail to open | SMB creds — `SMB_USER`/`SMB_PASS` in `.env`, or a firewall on the source PC |
| Emails not landing | `logs\api.out.log` — search for `email failed:` — SMTP env vars or the SMTP host blocking |
| PC-unreachable alert never fires | check `PC_HEALTH_STALE_DAYS`, verify `pc_status.last_reachable = false` for the PC in the DB |
| Dashboard shows stale data | browser cache — hard reload; if not that, restart `cfv-api` |
| Both services healthy but 502 on `/api/*` | `next.config.ts` rewrite target — should still be `http://localhost:8000` |

## Log locations
- `C:\apps\cfv\logs\api.out.log` — FastAPI structured JSON logs (per-request + audit + scan/commit output)
- `C:\apps\cfv\logs\api.err.log` — Python tracebacks
- `C:\apps\cfv\logs\web.out.log` — Next.js access-ish logs
- `C:\apps\cfv\logs\web.err.log` — Node crashes
- All rotate at 10 MB (nssm handles rotation).
