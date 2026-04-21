# dltrf-cloud.ps1 - DLTRF Azure Cloud CLI
# Run this from your LOCAL laptop to control your Azure VM
# Usage: .\dltrf-cloud.ps1 [command]

param([string]$Command = "help")

$ErrorActionPreference = "Continue"

# =============================================================================
#  CONFIGURATION - Edit these if your IP or key path changes
# =============================================================================
$VM_IP        = "20.212.82.99"
$VM_USER      = "azureuser"
$SSH_KEY      = "$env:USERPROFILE\Downloads\dltrf-server_key.pem"
$HOOK_DIR     = "~/DLTRF-Project/universal-logging-hook-microservice"
$ENGINE_DIR   = "~/DLTRF-Project/REPLAY-ENGINE"
$ENGINE_API   = "http://${VM_IP}:8000"
$ENGINE_TOKEN = "mysecret"
$REDIS_C      = "universal-logging-redis"
$STREAM_KEY   = "nginx-log-stream"
# =============================================================================

function PrintInfo($m)   { Write-Host "  * $m" -ForegroundColor Cyan }
function PrintOk($m)     { Write-Host "  + $m" -ForegroundColor Green }
function PrintWarn($m)   { Write-Host "  ! $m" -ForegroundColor Yellow }
function PrintFail($m)   { Write-Host "  X $m" -ForegroundColor Red; exit 1 }
function PrintHeader($m) {
    Write-Host "`n  ======================================" -ForegroundColor Blue
    Write-Host "   DLTRF Cloud - $m" -ForegroundColor Blue
    Write-Host "  ======================================`n" -ForegroundColor Blue
}

function RunSSHCmd($cmd) {
    ssh -i $SSH_KEY -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${VM_USER}@${VM_IP}" $cmd
    return $LASTEXITCODE
}

function CheckSSHKey {
    if (-not (Test-Path $SSH_KEY)) {
        PrintFail "SSH key not found at: $SSH_KEY`n  Fix: Update the SSH_KEY path at the top of this script."
    }
}

# -----------------------------------------------------------------------------
#  up
# -----------------------------------------------------------------------------
function RunUp {
    PrintHeader "Starting All Components on Azure"
    CheckSSHKey
    PrintInfo "Starting universal-logging-hook-microservice..."
    RunSSHCmd "cd $HOOK_DIR && docker compose up -d"
    PrintInfo "Starting REPLAY-ENGINE..."
    RunSSHCmd "cd $ENGINE_DIR && docker compose up -d"
    PrintOk "All services started!"
    Write-Host ""
    Write-Host "  Access URLs:" -ForegroundColor White
    Write-Host "    Webapp (via Nginx) :  http://${VM_IP}" -ForegroundColor White
    Write-Host "    Log Dashboard      :  http://${VM_IP}:5000" -ForegroundColor White
    Write-Host "    Replay Engine API  :  http://${VM_IP}:8000" -ForegroundColor White
    Write-Host "    HTML Report        :  http://${VM_IP}:8000/report" -ForegroundColor White
    Write-Host ""
}

# -----------------------------------------------------------------------------
#  down
# -----------------------------------------------------------------------------
function RunDown {
    PrintHeader "Stopping All Components"
    CheckSSHKey
    PrintInfo "Stopping universal-logging-hook-microservice..."
    RunSSHCmd "cd $HOOK_DIR && docker compose down --remove-orphans"
    PrintInfo "Stopping REPLAY-ENGINE..."
    RunSSHCmd "cd $ENGINE_DIR && docker compose down --remove-orphans"
    PrintOk "All containers stopped and removed."
}

# -----------------------------------------------------------------------------
#  build
# -----------------------------------------------------------------------------
function RunBuild {
    PrintHeader "Rebuilding All Images on Azure"
    CheckSSHKey
    PrintInfo "Building universal-logging-hook-microservice (this may take a few minutes)..."
    RunSSHCmd "cd $HOOK_DIR && docker compose build"
    PrintInfo "Building REPLAY-ENGINE..."
    RunSSHCmd "cd $ENGINE_DIR && docker compose build"
    PrintOk "All images rebuilt. Run '.\dltrf-cloud.ps1 up' to start them."
}

# -----------------------------------------------------------------------------
#  reset
# -----------------------------------------------------------------------------
function RunReset {
    PrintHeader "Factory Reset (Blank Slate)"
    CheckSSHKey
    PrintWarn "This permanently destroys ALL containers, volumes, and recorded data on Azure VM."
    $confirm = Read-Host "  Are you sure? (y/N)"
    if ($confirm -ne "y" -and $confirm -ne "Y") { PrintInfo "Cancelled."; return }
    PrintInfo "Wiping hook microservice volumes..."
    RunSSHCmd "cd $HOOK_DIR && docker compose down -v"
    PrintInfo "Wiping REPLAY-ENGINE volumes..."
    RunSSHCmd "cd $ENGINE_DIR && docker compose down -v"
    PrintInfo "Booting fresh environment..."
    RunSSHCmd "cd $HOOK_DIR && docker compose up -d"
    PrintOk "Factory reset complete!"
    PrintWarn "Wait 45-60 seconds before visiting http://${VM_IP}"
    PrintWarn "Default Login: admin@admin.com / password"
}

# -----------------------------------------------------------------------------
#  status
# -----------------------------------------------------------------------------
function RunStatus {
    PrintHeader "Status"
    CheckSSHKey
    Write-Host "  Running containers on Azure VM:" -ForegroundColor White
    Write-Host ""
    RunSSHCmd "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"
    Write-Host ""
    PrintInfo "Redis recorded events:"
    RunSSHCmd "docker exec $REDIS_C redis-cli XLEN $STREAM_KEY 2>/dev/null || echo '  Redis not running'"
    Write-Host ""
    PrintInfo "Disk usage on VM:"
    RunSSHCmd "df -h / | tail -1"
}

# -----------------------------------------------------------------------------
#  prune
# -----------------------------------------------------------------------------
function RunPrune {
    PrintHeader "Docker System Prune"
    CheckSSHKey
    PrintWarn "This removes all unused containers, images, and networks on Azure VM."
    $confirm = Read-Host "  Continue? (y/N)"
    if ($confirm -ne "y" -and $confirm -ne "Y") { PrintInfo "Cancelled."; return }
    RunSSHCmd "docker system prune -f"
    PrintOk "Prune complete."
}

# -----------------------------------------------------------------------------
#  logs
# -----------------------------------------------------------------------------
function RunLogs {
    PrintHeader "Logs"
    CheckSSHKey
    Write-Host "  Fetching last 40 lines from both services..." -ForegroundColor White
    Write-Host ""
    PrintInfo "--- universal-logging-hook-microservice ---"
    RunSSHCmd "cd $HOOK_DIR && docker compose logs --tail=40 2>&1"
    Write-Host ""
    PrintInfo "--- REPLAY-ENGINE ---"
    RunSSHCmd "cd $ENGINE_DIR && docker compose logs --tail=40 2>&1"
}

# -----------------------------------------------------------------------------
#  ssh
# -----------------------------------------------------------------------------
function RunShell {
    PrintHeader "SSH Shell"
    CheckSSHKey
    PrintInfo "Connecting to Azure VM... (type 'exit' to return to local terminal)"
    Write-Host ""
    ssh -i $SSH_KEY -o StrictHostKeyChecking=no "${VM_USER}@${VM_IP}"
}

# -----------------------------------------------------------------------------
#  pull
# -----------------------------------------------------------------------------
function RunPull {
    PrintHeader "Git Pull (Update Code on Azure)"
    CheckSSHKey
    PrintInfo "Pulling universal-logging-hook-microservice..."
    RunSSHCmd "cd $HOOK_DIR && git pull"
    PrintInfo "Pulling REPLAY-ENGINE..."
    RunSSHCmd "cd $ENGINE_DIR && git pull"
    PrintOk "Code updated on Azure VM."
    PrintInfo "To apply changes run: .\dltrf-cloud.ps1 build  then  .\dltrf-cloud.ps1 up"
}

# -----------------------------------------------------------------------------
#  checkpoint
# -----------------------------------------------------------------------------
function RunCheckpoint {
    PrintHeader "Checkpoint"
    CheckSSHKey
    PrintInfo "Saving DB checkpoint on Azure VM..."
    RunSSHCmd "cd $ENGINE_DIR && chmod +x scripts/checkpoint.sh && bash scripts/checkpoint.sh save"
    if ($LASTEXITCODE -eq 0) { PrintOk "Checkpoint saved successfully." }
    else { PrintWarn "Checkpoint returned non-zero. Check: .\dltrf-cloud.ps1 logs" }
}

# -----------------------------------------------------------------------------
#  replay
# -----------------------------------------------------------------------------
function RunReplay {
    PrintHeader "Replay"
    CheckSSHKey
    PrintInfo "Triggering replay via API at $ENGINE_API ..."

    $replayId = $null
    $headers  = @{ "Authorization" = "Bearer $ENGINE_TOKEN"; "Content-Type" = "application/json" }
    $bodyJson = '{"mode":"replay","max_events":200,"start_ts":"0","end_ts":"+"}'

    try {
        $response = Invoke-RestMethod -Uri "$ENGINE_API/replay/start" -Method POST -Headers $headers -Body $bodyJson -ErrorAction Stop
        $replayId = $response.replay_id
        PrintOk "Replay started - ID: $replayId"
    } catch {
        PrintFail "Could not reach replay API. Is the VM running? Run: .\dltrf-cloud.ps1 status"
    }

    PrintInfo "Waiting for report to generate..."
    $elapsed = 0
    $found   = $false

    while ($elapsed -lt 300) {
        Start-Sleep -Seconds 3
        $elapsed += 3
        try {
            # ADD THE /$replayId HERE
            $check = Invoke-WebRequest -Uri "$ENGINE_API/report/$replayId" -UseBasicParsing -ErrorAction Stop
            if ($check.StatusCode -eq 200) { $found = $true; break }
        } catch {}
        Write-Host "." -NoNewline -ForegroundColor DarkCyan
    }
    Write-Host ""

    if ($found) {
        PrintOk "Report ready! Opening in browser..."
        # ADD THE /$replayId HERE TOO
        Start-Process "$ENGINE_API/report/$replayId"
    } else {
        PrintWarn "Timed out. Open manually: $ENGINE_API/report/$replayId"
    }
}

# -----------------------------------------------------------------------------
#  ip
# -----------------------------------------------------------------------------
function RunIp {
    PrintHeader "VM Info"
    Write-Host "  Configured VM IP : $VM_IP" -ForegroundColor White
    Write-Host "  SSH User         : $VM_USER" -ForegroundColor White
    Write-Host "  SSH Key          : $SSH_KEY" -ForegroundColor White
    Write-Host ""
    PrintWarn "Azure VM IPs can change after a VM restart!"
    PrintInfo "If SSH fails, check Azure Portal -> dltrf-server -> Overview -> Public IP"
    PrintInfo "Then update VM_IP at the top of this script."
}

# -----------------------------------------------------------------------------
#  help
# -----------------------------------------------------------------------------
function RunHelp {
    Write-Host ""
    Write-Host "  DLTRF Cloud CLI - Azure VM Controller" -ForegroundColor Blue
    Write-Host "  =======================================" -ForegroundColor Blue
    Write-Host "  Usage: .\dltrf-cloud.ps1 [command]" -ForegroundColor White
    Write-Host ""
    Write-Host "  VM Target: ${VM_USER}@${VM_IP}" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Container Commands:" -ForegroundColor Yellow
    Write-Host "    up          Start all containers on Azure VM" -ForegroundColor White
    Write-Host "    down        Stop all containers on Azure VM" -ForegroundColor White
    Write-Host "    build       Rebuild all Docker images on Azure VM" -ForegroundColor White
    Write-Host "    reset       Factory reset - wipe all volumes and restart fresh" -ForegroundColor White
    Write-Host ""
    Write-Host "  Monitoring Commands:" -ForegroundColor Yellow
    Write-Host "    status      Show running containers + Redis count + disk usage" -ForegroundColor White
    Write-Host "    logs        Tail last 40 lines from both services" -ForegroundColor White
    Write-Host ""
    Write-Host "  DLTRF Workflow Commands:" -ForegroundColor Yellow
    Write-Host "    checkpoint  Save DB checkpoint on Azure VM" -ForegroundColor White
    Write-Host "    replay      Trigger replay and open HTML report in browser" -ForegroundColor White
    Write-Host ""
    Write-Host "  Utility Commands:" -ForegroundColor Yellow
    Write-Host "    pull        Git pull latest code in both repos on Azure VM" -ForegroundColor White
    Write-Host "    prune       docker system prune (free up disk on VM)" -ForegroundColor White
    Write-Host "    ssh         Open interactive SSH shell into Azure VM" -ForegroundColor White
    Write-Host "    ip          Show VM IP info and what to do if it changed" -ForegroundColor White
    Write-Host ""
    Write-Host "  Access URLs:" -ForegroundColor Yellow
    Write-Host "    Webapp      :  http://${VM_IP}" -ForegroundColor White
    Write-Host "    Dashboard   :  http://${VM_IP}:5000" -ForegroundColor White
    Write-Host "    Replay API  :  http://${VM_IP}:8000" -ForegroundColor White
    Write-Host "    HTML Report :  http://${VM_IP}:8000/report" -ForegroundColor White
    Write-Host ""
}

# -----------------------------------------------------------------------------
#  Command Router
# -----------------------------------------------------------------------------
switch ($Command.ToLower()) {
    "up"         { RunUp }
    "down"       { RunDown }
    "build"      { RunBuild }
    "reset"      { RunReset }
    "status"     { RunStatus }
    "logs"       { RunLogs }
    "prune"      { RunPrune }
    "ssh"        { RunShell }
    "pull"       { RunPull }
    "checkpoint" { RunCheckpoint }
    "replay"     { RunReplay }
    "ip"         { RunIp }
    "help"       { RunHelp }
    default      { PrintWarn "Unknown command: '$Command'"; RunHelp; exit 1 }
}