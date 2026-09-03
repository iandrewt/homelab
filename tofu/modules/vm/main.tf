locals {
  mac_address = format("bc:24:11:00:%02x:%02x", floor(var.vm_id / 256), var.vm_id % 256)
}

# need to set a vendor_data_file with a cloud-config snippet that installs and enables qemu-guest-agent.
resource "proxmox_virtual_environment_file" "qemu_guest_agent" {
  content_type = "snippets"
  datastore_id = "local"
  node_name    = var.node_name

  source_raw {
    file_name = "${var.hostname}-qemu-guest-agent.yaml"
    data      = <<-EOF
      #cloud-config
      package_update: true
      packages:
        - qemu-guest-agent
      runcmd:
        - systemctl enable --now qemu-guest-agent
    EOF
  }
}

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
    source    = "/dev/urandom"
    max_bytes = 1024
    period    = 1000
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
    bridge      = var.bridge
    vlan_id     = var.vlan_id
    mac_address = local.mac_address
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

    vendor_data_file_id = proxmox_virtual_environment_file.qemu_guest_agent.id
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

    precondition {
      condition     = var.vm_id <= 65535
      error_message = "vm_id must fit in two bytes - it's spliced directly into the locally-administered MAC address."
    }
  }
}
