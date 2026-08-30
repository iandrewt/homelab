resource "proxmox_virtual_environment_vm" "this" {
  node_name   = var.node_name
  vm_id       = var.vm_id
  name        = var.hostname
  description = var.description
  tags        = var.tags
  started     = var.started
  on_boot     = var.on_boot

  bios    = "ovmf"
  machine = "q35"

  scsi_hardware = "virtio-scsi-single"

  audio_device {
    enabled = false
  }

  agent {
    enabled = true
    trim    = true

    wait_for_ip {
      ipv4 = true
      ipv6 = true
    }
  }

  cpu {
    cores = var.cpu_cores
    type  = "host"
  }

  memory {
    dedicated = var.memory_mb
    floating  = var.memory_mb
  }

  rng {
    source = "/dev/urandom"
  }

  efi_disk {
    datastore_id      = var.disk_datastore_id
    type              = "4m"
    pre_enrolled_keys = true
  }

  disk {
    datastore_id = var.disk_datastore_id
    interface    = "scsi0"
    import_from  = var.image_file_id
    size         = var.disk_size_gb
    ssd          = true
    discard      = "on"
    iothread     = true
    aio          = "io_uring"
    cache        = "none"
  }

  network_device {
    bridge  = var.bridge
    vlan_id = var.vlan_id
  }

  # Cloud images output to the serial console, not a VGA framebuffer.
  serial_device {
    device = "socket"
  }

  vga {
    type = "serial0"
  }

  operating_system {
    type = "l26"
  }

  initialization {
    datastore_id = var.disk_datastore_id

    ip_config {
      ipv4 {
        address = var.ipv4_cidr
        gateway = var.ipv4_cidr == "dhcp" ? null : var.ipv4_gateway
      }

      ipv6 {
        address = var.ipv6_cidr
        gateway = contains(["auto", "dhcp"], var.ipv6_cidr) ? null : var.ipv6_gateway
      }
    }

    user_account {
      username = var.ci_username
      keys     = var.ssh_public_keys
    }
  }

  lifecycle {
    precondition {
      condition     = var.ipv4_cidr == "dhcp" || var.ipv4_gateway != null
      error_message = "ipv4_gateway is required unless ipv4_cidr is \"dhcp\"."
    }

    precondition {
      condition     = contains(["auto", "dhcp"], var.ipv6_cidr) || var.ipv6_gateway != null
      error_message = "ipv6_gateway is required unless ipv6_cidr is \"auto\" or \"dhcp\"."
    }
  }
}
