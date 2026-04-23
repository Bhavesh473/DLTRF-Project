#!/bin/bash
# =============================================================================
#  DLTRF — Ansible Bridge Script
#  File: ansible/update_and_deploy.sh
#
#  PURPOSE:
#    1. Reads the VM's public IP from Terraform output
#    2. Rewrites ansible/inventory.ini with the new IP
#    3. Waits for SSH to become available on the new VM
#    4. Runs the Ansible deployment playbook
#
#  USAGE (from WSL, run from DLTRF-Project root):
#    chmod +x ansible/update_and_deploy.sh
#    ./ansible/update_and_deploy.sh
#
#  PREREQUISITE:
#    - Terraform has already been applied (terraform apply done in PowerShell)
#    - 'terraform' CLI is accessible from WSL (or path adjusted below)
#    - 'ansible-playbook' is installed in WSL
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
INVENTORY_FILE="$SCRIPT_DIR/inventory.ini"
TERRAFORM_DIR="$PROJECT_ROOT/terraform"

# ── Step 1: Get the new IP from Terraform output ──────────────────────────
echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   DLTRF Terraform → Ansible Bridge          ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "[1/4] Reading VM public IP from Terraform output..."

# Convert Windows Terraform path to WSL path if needed
# If terraform.exe is in Windows PATH it will be accessible as 'terraform.exe'
# otherwise install terraform in WSL with: sudo snap install terraform --classic
TF_CMD="terraform"
if ! command -v terraform &>/dev/null; then
  TF_CMD="terraform.exe"
fi

NEW_IP=$($TF_CMD -chdir="$TERRAFORM_DIR" output -raw vm_public_ip 2>/dev/null)

if [[ -z "$NEW_IP" ]]; then
  echo "ERROR: Could not read vm_public_ip from Terraform."
  echo "       Make sure 'terraform apply' was completed successfully first."
  exit 1
fi

echo "    ✓ New VM IP: $NEW_IP"

# ── Step 2: Rewrite inventory.ini ─────────────────────────────────────────
echo ""
echo "[2/4] Updating $INVENTORY_FILE with new IP..."

# Replace the ansible_host= value on the dltrf-azure line
sed -i "s/ansible_host=[0-9.]\+/ansible_host=$NEW_IP/" "$INVENTORY_FILE"

echo "    ✓ inventory.ini updated:"
grep "ansible_host" "$INVENTORY_FILE"

# ── Step 3: Wait for SSH ───────────────────────────────────────────────────
echo ""
echo "[3/4] Waiting for SSH to become available on $NEW_IP:22..."
echo "      (VM needs ~60-90s for cloud-init to finish on first boot)"

MAX_WAIT=180   # seconds
INTERVAL=10
ELAPSED=0
SSH_USER=$(grep "ansible_user=" "$INVENTORY_FILE" | head -1 | sed 's/.*ansible_user=//' | awk '{print $1}')
SSH_KEY=$(grep "ansible_ssh_private_key_file=" "$INVENTORY_FILE" | head -1 | sed "s/.*ansible_ssh_private_key_file=//" | awk '{print $1}' | sed "s|~|$HOME|")

while ! ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no \
            -i "$SSH_KEY" "$SSH_USER@$NEW_IP" "exit" 2>/dev/null; do
  ELAPSED=$((ELAPSED + INTERVAL))
  if [[ $ELAPSED -ge $MAX_WAIT ]]; then
    echo "ERROR: SSH not available after ${MAX_WAIT}s. Check Azure NSG and VM status."
    exit 1
  fi
  echo "      ... still waiting (${ELAPSED}s elapsed, max ${MAX_WAIT}s)"
  sleep $INTERVAL
done

echo "    ✓ SSH is ready!"

# ── Step 4: Run Ansible deployment ────────────────────────────────────────
echo ""
echo "[4/4] Running Ansible deployment playbook..."
echo "──────────────────────────────────────────────"

ansible-playbook -i "$INVENTORY_FILE" "$SCRIPT_DIR/deploy.yml"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║  ✓ DLTRF fully deployed!                    ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "  Webapp          → http://$NEW_IP"
echo "  Log Dashboard   → http://$NEW_IP:5000"
echo "  Replay API      → http://$NEW_IP:8000"
echo "  HTML Report     → http://$NEW_IP:8000/report"
echo ""
