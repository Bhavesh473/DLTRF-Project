# =============================================================================
#  DLTRF — Terraform Outputs
#  File: terraform/outputs.tf
#
#  These values are printed after 'terraform apply' and can be consumed
#  by the Ansible bridge script (update_inventory.sh / update_inventory.ps1).
# =============================================================================

output "vm_public_ip" {
  description = "Static public IP of the DLTRF Azure VM. Feed this into Ansible inventory."
  value       = azurerm_public_ip.dltrf_pip.ip_address
}

output "vm_name" {
  description = "Name of the provisioned Virtual Machine."
  value       = azurerm_linux_virtual_machine.dltrf_vm.name
}

output "resource_group" {
  description = "Resource Group that contains all DLTRF infrastructure."
  value       = azurerm_resource_group.dltrf_rg.name
}

output "ssh_command" {
  description = "Ready-to-use SSH command to log into the new VM."
  value       = "ssh -i ~/.ssh/id_rsa ${var.admin_username}@${azurerm_public_ip.dltrf_pip.ip_address}"
}

output "ansible_run_command" {
  description = "Ready-to-use Ansible command (run from WSL after VM is reachable)."
  value       = "ansible-playbook -i ansible/inventory.ini ansible/deploy.yml"
}

output "access_urls" {
  description = "Application URLs once the Ansible playbook has run."
  value = {
    webapp         = "http://${azurerm_public_ip.dltrf_pip.ip_address}"
    log_dashboard  = "http://${azurerm_public_ip.dltrf_pip.ip_address}:5000"
    replay_api     = "http://${azurerm_public_ip.dltrf_pip.ip_address}:8000"
    html_report    = "http://${azurerm_public_ip.dltrf_pip.ip_address}:8000/report"
  }
}
