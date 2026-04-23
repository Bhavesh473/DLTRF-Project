# =============================================================================
#  DLTRF — Terraform Variables
#  File: terraform/variables.tf
# =============================================================================

variable "prefix" {
  description = "Short prefix used to name all Azure resources (e.g. 'dltrf')."
  type        = string
  default     = "dltrf"
}

variable "resource_group_name" {
  description = "Name of the Azure Resource Group to create."
  type        = string
  default     = "DLTRF-RG"
}

variable "location" {
  description = "Azure region. Keep this the same as your existing resources to avoid egress costs."
  type        = string
  default     = "East Asia"   # Change to your preferred region, e.g. "Central India"
}

variable "environment" {
  description = "Environment tag applied to all resources."
  type        = string
  default     = "dev"
}

variable "admin_username" {
  description = "Linux admin user created on the VM. Must match what Ansible expects (ansible_user in inventory.ini)."
  type        = string
  default     = "azureuser"
}

variable "ssh_public_key_path" {
  description = <<-EOT
    Path to your SSH PUBLIC key file on the machine running Terraform.
    PowerShell example : C:/Users/YourName/.ssh/id_rsa.pub
    WSL example        : /home/yourname/.ssh/id_rsa.pub
  EOT
  type    = string
  default = "~/.ssh/id_rsa.pub"
}

variable "allowed_ssh_cidr" {
  description = <<-EOT
    CIDR block allowed to reach port 22. 
    BEST PRACTICE: restrict this to your own public IP, e.g. "203.0.113.5/32".
    Setting "*" (any) is acceptable for a lab/demo project but not for production.
  EOT
  type    = string
  default = "*"   # TODO: Replace with your actual public IP for better security
}

variable "github_repo" {
  description = <<-EOT
    GitHub repository in 'owner/repo' format.
    Used by cloud-init to clone the project on first boot.
    Example: "bhavesh/DLTRF-Project"
  EOT
  type    = string
  default = "YourGitHubUsername/DLTRF-Project"   # ← MUST CHANGE THIS
}
