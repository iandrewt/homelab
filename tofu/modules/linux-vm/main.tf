locals {
  # <prefix>:00:<vmid as two bytes>.
  mac_address = format("%s:00:%02x:%02x", lower(var.mac_prefix), floor(var.vm_id / 256), var.vm_id % 256)
}

resource "random_id" "vm_instance_id" {
  byte_length = 20
}

resource "proxmox_virtual_environment_file" "meta_data" {
  content_type = "snippets"
  datastore_id = var.snippet_datastore_id
  node_name    = var.node_name

  source_raw {
    file_name = "${var.hostname}-meta-data.yaml"
    data = yamlencode({
      instance-id    = random_id.vm_instance_id.hex
      local-hostname = var.hostname
      public-keys    = var.ssh_public_keys
    })
  }
}

resource "proxmox_virtual_environment_file" "vendor_data" {
  content_type = "snippets"
  datastore_id = var.snippet_datastore_id
  node_name    = var.node_name

  source_raw {
    file_name = "${var.hostname}-vendor-data.yaml"
    data      = <<-EOF
      #cloud-config
      packages:
        - qemu-guest-agent
      runcmd:
        - udevadm trigger --subsystem-match=virtio-ports --attr-match=name=org.qemu.guest_agent.0
    EOF
  }
}

resource "proxmox_virtual_environment_file" "network_config" {
  content_type = "snippets"
  datastore_id = var.snippet_datastore_id
  node_name    = var.node_name

  source_raw {
    file_name = "${var.hostname}-network-config.yaml"
    data = yamlencode({
      version = 2
      ethernets = {
        eth0 = {
          match = {
            macaddress = local.mac_address
          }
          dhcp4     = var.enable_dhcpv4
          dhcp6     = var.enable_dhcpv6
          accept-ra = var.accept_ra

          addresses = var.static_ip_addresses
          routes = [for route in [
            var.ipv4_gateway != null ? {
              to  = "default"
              via = var.ipv4_gateway
            } : null,
            var.ipv6_gateway != null ? {
              to  = "default"
              via = var.ipv6_gateway
            } : null
          ] : route if route != null]

          nameservers = {
            addresses = var.dns_servers
            search    = var.search_domains
          }
        }
      }
    })
  }
}

resource "proxmox_virtual_environment_file" "user_data" {
  content_type = "snippets"
  datastore_id = var.snippet_datastore_id
  node_name    = var.node_name

  source_raw {
    file_name = "${var.hostname}-user-data.yaml"
    data      = var.user_data
  }
}

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
      ipv4 = true
      ipv6 = true
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
    bridge      = var.bridge
    vlan_id     = var.vlan_id
    mac_address = local.mac_address
    firewall    = var.enable_firewall
    model       = "virtio"
    queues      = var.enable_multiqueue ? var.cpu_cores * var.cpu_sockets : 1
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

  initialization {
    datastore_id = var.disk_datastore_id
    interface    = "ide2"

    meta_data_file_id    = proxmox_virtual_environment_file.meta_data.id
    network_data_file_id = proxmox_virtual_environment_file.network_config.id
    vendor_data_file_id  = proxmox_virtual_environment_file.vendor_data.id
    user_data_file_id    = proxmox_virtual_environment_file.user_data.id
  }

  lifecycle {
    precondition {
      condition     = var.vm_id <= 65535
      error_message = "vm_id must fit in two bytes - it's spliced directly into the locally-administered MAC address."
    }
  }
}
