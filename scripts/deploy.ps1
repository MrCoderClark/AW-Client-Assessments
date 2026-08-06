# Client Files Viewer — deploy / update script.
#
# Idempotent. Safe on first deploy AND on subsequent updates.
# Run as admin from anywhere.
#
# Prereqs (do these once, DEPLOYMENT.md §1, §2, §4):
#   - Host prep (static IP, firewall, long paths)
#   - Runtimes installed (git, python 3.12, node 24, uv, pnpm, nssm)
#   - Repo cloned to C:\apps\cfv
#   - .env filled in at C:\apps\cfv\.env (with JWT keys generated)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ---- pretty output ------------------------------------------------
function Step($msg)  { Write-Host ""; Write-Host "▶ $msg" -ForegroundColor Cyan }
function Ok($msg)    { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Info($msg)  { Write-Host "  · $msg" -ForegroundColor DarkGray }
function Warn($msg)  { Write-Host "  ⚠ $msg" -ForegroundColor Yellow }
function Fail($msg)  { Write-Host "  ✗ $msg" -ForegroundColor Red; exit 1 }
function Banner($t)  {
    $bar = "─" * ($t.Length + 4)
    Write-Host ""
    Write-Host "┌$bar┐" -ForegroundColor Magenta
    Write-Host "│  $t  │" -ForegroundColor Magenta
    Write-Host "└$bar┘" -ForegroundColor Magenta
}

$ROOT = "C:\apps\cfv"

Banner "Client Files Viewer — deploy"
Set-Location $ROOT

# ---- 1. preflight -------------------------------------------------
Step "Preflight"
foreach ($t in @("git","uv","pnpm","node","nssm")) {
    if (Get-Command $t -ErrorAction SilentlyContinue) { Ok "$t on PATH" }
    else { Fail "$t not on PATH — see DEPLOYMENT.md §2" }
}
if (-not (Test-Path "$ROOT\.env")) { Fail ".env missing at $ROOT\.env — see §4b" }
Ok ".env present"
$pemCount = (Get-ChildItem "$ROOT\secrets\*.pem" -ErrorAction SilentlyContinue | Measure-Object).Count
if ($pemCount -lt 1) { Fail "no JWT key in $ROOT\secrets — run scripts\gen_jwt_key.py (§4a)" }
Ok "JWT key present"

# ---- 2. git pull --------------------------------------------------
Step "git pull"
git pull --ff-only
Ok "up to date"

# ---- 3. backend deps ----------------------------------------------
Step "uv sync"
uv sync
Ok "backend deps synced"

# ---- 4. frontend build --------------------------------------------
Step "pnpm install + build"
Set-Location "$ROOT\frontend"
pnpm install --frozen-lockfile
pnpm build
Set-Location $ROOT
Ok "frontend built"

# ---- 5. migrations ------------------------------------------------
Step "alembic upgrade head"
uv run --env-file .env alembic upgrade head
Ok "schema at head"

# ---- 6. register services (skip if already present) --------------
Step "Windows services"
$UV   = (Get-Command uv).Source
$NODE = (Get-Command node).Source
New-Item -ItemType Directory -Path "$ROOT\logs" -Force | Out-Null

function EnsureService {
    param($name, $exe, $params, $dir, $depends = $null, $desc = "")
    $svc = Get-Service $name -ErrorAction SilentlyContinue
    if ($svc) { Info "$name already registered"; return }
    nssm install $name $exe                              | Out-Null
    nssm set $name AppParameters   $params               | Out-Null
    nssm set $name AppDirectory    $dir                  | Out-Null
    nssm set $name AppStdout       "$ROOT\logs\$name.out.log" | Out-Null
    nssm set $name AppStderr       "$ROOT\logs\$name.err.log" | Out-Null
    nssm set $name AppRotateFiles  1                     | Out-Null
    nssm set $name AppRotateBytes  10485760              | Out-Null
    nssm set $name Start           SERVICE_AUTO_START    | Out-Null
    nssm set $name AppExit         Default Restart       | Out-Null
    nssm set $name AppRestartDelay 5000                  | Out-Null
    if ($depends) { nssm set $name DependOnService $depends | Out-Null }
    if ($desc)    { nssm set $name Description $desc        | Out-Null }
    Ok "$name registered"
}

EnsureService -name "cfv-api" -exe $UV `
    -params "run --env-file .env uvicorn api:app --host 0.0.0.0 --port 8000" `
    -dir $ROOT -desc "Client Files Viewer — FastAPI backend"

EnsureService -name "cfv-web" -exe $NODE `
    -params ".\node_modules\next\dist\bin\next start -p 80" `
    -dir "$ROOT\frontend" -depends "cfv-api" `
    -desc "Client Files Viewer — Next.js frontend"

# ---- 7. restart ---------------------------------------------------
Step "Restart services"
Restart-Service cfv-api -Force
Start-Sleep -Seconds 3
Ok "cfv-api restarted (cfv-web restarted with it)"

# ---- 8. health check ---------------------------------------------
Step "Health check"
try {
    $r = Invoke-WebRequest -Uri "http://localhost/api/health" -TimeoutSec 10 -UseBasicParsing
    if ($r.StatusCode -eq 200) { Ok "http://localhost/api/health → 200" }
    else { Warn "unexpected status: $($r.StatusCode)" }
} catch {
    Warn "health check failed: $($_.Exception.Message)"
    Info "check logs\api.err.log and logs\web.err.log"
}

Get-Service cfv-api, cfv-web | Format-Table -AutoSize

Banner "Done"
