resource "proxmox_virtual_environment_vm" "this" {
  node_name   = var.node_name
  pool_id     = var.pool_id
  vm_id       = var.vm_id
  name        = var.hostname
  description = var.description
  tags        = var.tags
  started     = var.started
  on_boot     = var.on_boot

  bios       = "ovmf"
  machine    = "q35"
  boot_order = ["scsi0"]

  agent {
    enabled = true
    trim    = true
    type    = "virtio"

    wait_for_ip {
      ipv4 = var.ipv4 != null
      ipv6 = var.ipv6 != null
    }
  }

  cpu {
    cores   = var.cpu_cores
    sockets = var.cpu_sockets
    numa    = true
    type    = "host"
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

  scsi_hardware = "virtio-scsi-single"

  disk {
    datastore_id = var.disk_datastore_id
    file_format  = "raw"
    interface    = "scsi0"
    import_from  = var.image_file_id
    size         = var.disk_size_gb
    ssd          = true
    discard      = "on"
    iothread     = true
    aio          = "io_uring"
    cache        = "none"
    backup       = var.enable_backup
  }

  efi_disk {
    datastore_id      = var.disk_datastore_id
    file_format       = "raw"
    type              = "4m"
    pre_enrolled_keys = var.pre_enrolled_keys
  }

  tpm_state {
    datastore_id = var.disk_datastore_id
    version      = "v2.0"
  }

  network_device {
    bridge   = var.vnet
    firewall = var.enable_firewall
    model    = "virtio"
    queues   = var.enable_multiqueue ? var.cpu_cores * var.cpu_sockets : 1
  }

  # Cloud images output to the serial console, not a VGA framebuffer.
  serial_device {
    device = "socket"
  }

  vga {
    type = "serial0"
  }

  tablet_device = false

  operating_system {
    type = "l26"
  }

  watchdog {
    enabled = true
    model   = "i6300esb"
    action  = var.watchdog_action
  }

  # Proxmox writes the seed from these: no snippet, so it's on the VM's own storage and a tenant writes
  # nothing a node reads
  initialization {
    datastore_id = var.disk_datastore_id
    interface    = "ide2"
    upgrade      = false

    ip_config {
      dynamic "ipv4" {
        for_each = var.ipv4 != null ? [var.ipv4] : []
        content {
          address = ipv4.value
          gateway = var.ipv4_gateway
        }
      }

      dynamic "ipv6" {
        for_each = var.ipv6 != null ? [var.ipv6] : []
        content {
          address = ipv6.value
          gateway = var.ipv6_gateway
        }
      }
    }

    dynamic "dns" {
      for_each = length(var.dns_servers) > 0 ? [var.dns_servers] : []
      content {
        servers = dns.value
      }
    }

    user_account {
      keys = var.ssh_public_keys
    }
  }
}
