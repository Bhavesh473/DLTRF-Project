# =============================================================================
#  DLTRF — Terraform Infrastructure
#  File: terraform/main.tf
#
#  Provisions:
#    - Resource Group
#    - Virtual Network + Subnet
#    - Network Security Group (SSH + all DLTRF app ports)
#    - Public IP (Static)
#    - Network Interface
#    - Ubuntu VM (Standard_B2als_v2) with SSH key auth
# =============================================================================

terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
  }
  required_version = ">= 1.5.0"
}

provider "azurerm" {
  features {}
}

# -----------------------------------------------------------------------------
#  Resource Group
# -----------------------------------------------------------------------------
resource "azurerm_resource_group" "dltrf_rg" {
  name     = var.resource_group_name
  location = var.location

  tags = {
    project     = "DLTRF"
    environment = var.environment
    managed_by  = "Terraform"
  }
}

# -----------------------------------------------------------------------------
#  Virtual Network + Subnet
# -----------------------------------------------------------------------------
resource "azurerm_virtual_network" "dltrf_vnet" {
  name                = "${var.prefix}-vnet"
  resource_group_name = azurerm_resource_group.dltrf_rg.name
  location            = azurerm_resource_group.dltrf_rg.location
  address_space       = ["10.0.0.0/16"]

  tags = azurerm_resource_group.dltrf_rg.tags
}

resource "azurerm_subnet" "dltrf_subnet" {
  name                 = "${var.prefix}-subnet"
  resource_group_name  = azurerm_resource_group.dltrf_rg.name
  virtual_network_name = azurerm_virtual_network.dltrf_vnet.name
  address_prefixes     = ["10.0.1.0/24"]
}

# -----------------------------------------------------------------------------
#  Network Security Group
#  Ports sourced directly from docker-compose.yml files:
#    - 22    : SSH (management)
#    - 80    : app-proxy / Nginx (HTTP frontend)
#    - 8080  : app-proxy / Nginx (alternate HTTP)
#    - 5000  : log-dashboard (Python Flask)
#    - 8000  : replay-engine (FastAPI control API)
#    - 8050  : replay-engine (Flask report dashboard)
#    - 8200  : replay-sidecar
#    - 9880  : Fluentd HTTP input
#    - 24224 : Fluentd Forward protocol
#    - 5140  : Fluentd Syslog (TCP + UDP)
#    - 6379  : Redis — RESTRICTED to VNet only (never expose to internet)
# -----------------------------------------------------------------------------
resource "azurerm_network_security_group" "dltrf_nsg" {
  name                = "${var.prefix}-nsg"
  resource_group_name = azurerm_resource_group.dltrf_rg.name
  location            = azurerm_resource_group.dltrf_rg.location

  # ── SSH ──────────────────────────────────────────────────────────────────
  security_rule {
    name                       = "Allow-SSH"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = var.allowed_ssh_cidr
    destination_address_prefix = "*"
  }

  # ── HTTP Frontend (Nginx / OpenResty) ─────────────────────────────────────
  security_rule {
    name                       = "Allow-HTTP-80"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "80"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "Allow-HTTP-8080"
    priority                   = 120
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "8080"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  # ── Log Dashboard ─────────────────────────────────────────────────────────
  security_rule {
    name                       = "Allow-LogDashboard-5000"
    priority                   = 130
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "5000"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  # ── Replay Engine (FastAPI) ───────────────────────────────────────────────
  security_rule {
    name                       = "Allow-ReplayEngine-8000"
    priority                   = 140
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "8000"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  # ── Replay Engine (Flask Report Dashboard) ───────────────────────────────
  security_rule {
    name                       = "Allow-ReplayDashboard-8050"
    priority                   = 150
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "8050"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  # ── Replay Sidecar ────────────────────────────────────────────────────────
  security_rule {
    name                       = "Allow-ReplaySidecar-8200"
    priority                   = 160
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "8200"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  # ── Fluentd HTTP Input ───────────────────────────────────────────────────
  security_rule {
    name                       = "Allow-Fluentd-HTTP-9880"
    priority                   = 170
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "9880"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  # ── Fluentd Forward Protocol ─────────────────────────────────────────────
  security_rule {
    name                       = "Allow-Fluentd-Forward-24224"
    priority                   = 180
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "24224"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  # ── Fluentd Syslog TCP ───────────────────────────────────────────────────
  security_rule {
    name                       = "Allow-Fluentd-Syslog-TCP-5140"
    priority                   = 190
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "5140"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  # ── Fluentd Syslog UDP ───────────────────────────────────────────────────
  security_rule {
    name                       = "Allow-Fluentd-Syslog-UDP-5140"
    priority                   = 200
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Udp"
    source_port_range          = "*"
    destination_port_range     = "5140"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  # ── Redis — VNet ONLY (never public) ────────────────────────────────────
  security_rule {
    name                       = "Allow-Redis-VNetOnly-6379"
    priority                   = 210
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "6379"
    source_address_prefix      = "VirtualNetwork"
    destination_address_prefix = "*"
  }

  # ── Deny all other inbound (explicit, belt-and-suspenders) ───────────────
  security_rule {
    name                       = "Deny-All-Other-Inbound"
    priority                   = 4000
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  tags = azurerm_resource_group.dltrf_rg.tags
}

# Associate NSG → Subnet
resource "azurerm_subnet_network_security_group_association" "dltrf_nsg_assoc" {
  subnet_id                 = azurerm_subnet.dltrf_subnet.id
  network_security_group_id = azurerm_network_security_group.dltrf_nsg.id
}

# -----------------------------------------------------------------------------
#  Static Public IP
# -----------------------------------------------------------------------------
resource "azurerm_public_ip" "dltrf_pip" {
  name                = "${var.prefix}-pip"
  resource_group_name = azurerm_resource_group.dltrf_rg.name
  location            = azurerm_resource_group.dltrf_rg.location
  allocation_method   = "Static"
  sku                 = "Standard"

  tags = azurerm_resource_group.dltrf_rg.tags
}

# -----------------------------------------------------------------------------
#  Network Interface
# -----------------------------------------------------------------------------
resource "azurerm_network_interface" "dltrf_nic" {
  name                = "${var.prefix}-nic"
  resource_group_name = azurerm_resource_group.dltrf_rg.name
  location            = azurerm_resource_group.dltrf_rg.location

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.dltrf_subnet.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.dltrf_pip.id
  }

  tags = azurerm_resource_group.dltrf_rg.tags
}

# Associate NSG → NIC (double-layer; belt AND suspenders AND braces)
resource "azurerm_network_interface_security_group_association" "dltrf_nic_nsg" {
  network_interface_id      = azurerm_network_interface.dltrf_nic.id
  network_security_group_id = azurerm_network_security_group.dltrf_nsg.id
}

# -----------------------------------------------------------------------------
#  Virtual Machine
# -----------------------------------------------------------------------------
resource "azurerm_linux_virtual_machine" "dltrf_vm" {
  name                  = "${var.prefix}-vm"
  resource_group_name   = azurerm_resource_group.dltrf_rg.name
  location              = azurerm_resource_group.dltrf_rg.location
  size                  = "Standard_B2als_v2"
  admin_username        = var.admin_username
  network_interface_ids = [azurerm_network_interface.dltrf_nic.id]

  # ── SSH Key Authentication (no password) ─────────────────────────────────
  disable_password_authentication = true

  admin_ssh_key {
    username   = var.admin_username
    public_key = file(var.ssh_public_key_path)
  }

  # ── OS Disk ───────────────────────────────────────────────────────────────
  os_disk {
    name                 = "${var.prefix}-osdisk"
    caching              = "ReadWrite"
    storage_account_type = "Premium_LRS"
    disk_size_gb         = 64
  }

  # ── Ubuntu 22.04 LTS image ────────────────────────────────────────────────
  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts-gen2"
    version   = "latest"
  }

  # ── cloud-init: Install Git on first boot ─────────────────────────────────
  custom_data = base64encode(<<-EOT
    #!/bin/bash
    apt-get update -y
    apt-get install -y git curl
    # Clone the DLTRF repo so Ansible git-pull works immediately
    sudo -u ${var.admin_username} git clone https://github.com/${var.github_repo}.git \
      /home/${var.admin_username}/DLTRF-Project || true
  EOT
  )

  tags = azurerm_resource_group.dltrf_rg.tags
}
