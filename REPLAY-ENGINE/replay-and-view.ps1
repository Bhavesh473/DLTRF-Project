# replay-and-view.ps1

param(
    [int]$MaxEvents       = 100,
    [string]$Speed        = "1.0",
    [string]$ApiToken     = "mysecret",
    [string]$ApiUrl       = "http://localhost:8000",
    [switch]$SkipCheckpoint
)

$ErrorActionPreference = "Stop"

function Info($msg)  { Write-Host "  $msg" -ForegroundColor Cyan }
function Ok($msg)    { Write-Host "  $msg" -ForegroundColor Green }
function Warn($msg)  { Write-Host "  $msg" -ForegroundColor Yellow }
function Fail($msg)  { Write-Host "  $msg" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "  ============================================" -ForegroundColor Blue
Write-Host "   DLTRF - Deterministic Log Test Replay"     -ForegroundColor Blue
Write-Host "  ============================================" -ForegroundColor Blue
Write-Host ""

# ── Step 1: Check Redis ─────────────────────────────────────────
Info "Checking Redis for recorded events..."
$streamLen = 0
try {
    $raw = docker exec universal-logging-redis redis-cli XLEN logs:stream
    $streamLen = [int]$raw
} catch {
    Warn "Could not check Redis. Proceeding anyway."
}

if ($streamLen -eq 0) {
    Warn "Redis stream is empty."
    exit 1
}
Ok "Found $streamLen events in Redis"
Write-Host ""

# ── Step 2: Checkpoint restore ─────────────────────────────────
if (-not $SkipCheckpoint) {
    Info "Checking checkpoint..."

    $cpDir = Join-Path $PSScriptRoot "checkpoints"
    if ((Test-Path $cpDir) -and (Get-ChildItem $cpDir -File)) {

        try {
            $driveLetter = $PSScriptRoot.Substring(0,1).ToLower()
            $pathNoColon = $PSScriptRoot.Substring(2).Replace("\","/")
            $wslPath     = "/mnt/$driveLetter$pathNoColon"

            Info "Restoring checkpoint via WSL..."
            wsl sh -c "cd '$wslPath' && ./scripts/checkpoint.sh restore"

            if ($LASTEXITCODE -eq 0) {
                Ok "Checkpoint restored"
            } else {
                Warn "Checkpoint restore failed (code $LASTEXITCODE)"
            }
        } catch {
            Warn "Checkpoint restore error: $_"
        }

    } else {
        Warn "No checkpoint found. Skipping..."
    }
}
Write-Host ""

# ── Step 3: Wait for app ───────────────────────────────────────
Info "Checking app readiness..."
$ready = $false

for ($i=0; $i -lt 10; $i++) {
    try {
        $r = Invoke-WebRequest "http://localhost:3000" -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
    Start-Sleep 2
    Write-Host "." -NoNewline
}
Write-Host ""

if ($ready) { Ok "App ready" } else { Warn "App not responding" }
Write-Host ""

# ── Step 4: Start replay ───────────────────────────────────────
Info "Starting replay..."

$headers = @{
    Authorization = "Bearer $ApiToken"
    "Content-Type" = "application/json"
}

$body = @{
    mode       = "replay"
    max_events = $MaxEvents
    speed      = [float]$Speed
} | ConvertTo-Json

try {
    $res = Invoke-RestMethod "$ApiUrl/replay/start" -Method POST -Headers $headers -Body $body
    $replayId = $res.replay_id
    Ok "Replay started: $replayId"
} catch {
    Fail "Replay start failed: $_"
}

# ── Step 5: Wait for report ────────────────────────────────────
Info "Waiting for report..."

$foundFile = ""
for ($i=0; $i -lt 60; $i++) {
    Start-Sleep 2

    docker exec replay-engine test -f "reports/replay_$replayId.html"

    if ($LASTEXITCODE -eq 0) {
        $foundFile = "replay_$replayId.html"
        break
    }

    Write-Host "." -NoNewline
}
Write-Host ""

if (-not $foundFile) {
    Warn "Using latest report..."
    $latest = docker exec replay-engine sh -c "ls -t reports/*.html | head -1"
    if ($latest) {
        $foundFile = ($latest.Trim() -replace "reports/","")
    } else {
        Fail "No report found"
    }
}

Ok "Replay complete"
Write-Host ""

# ── Step 6: Copy report ────────────────────────────────────────
Info "Copying report..."

docker cp "replay-engine:/app/reports/$foundFile" ".\$foundFile"

$fullPath = (Resolve-Path ".\$foundFile").Path
Ok "Saved: $fullPath"

# ── Step 7: Open report ────────────────────────────────────────
Info "Opening report..."
Start-Process $fullPath

Ok "Done!"