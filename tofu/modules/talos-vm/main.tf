resource "proxmox_virtual_environment_vm" "this" {
  node_name   = var.node_name
  pool_id     = var.pool_id
  name        = var.hostname
  description = var.description
  tags        = var.tags
  started     = var.started
  on_boot     = var.on_boot

  bios       = "ovmf"
  machine    = "q35"
  boot_order = ["scsi0", "ide3"]

  agent {
    enabled = true
    trim    = true
    type    = "virtio"
    timeout = var.agent_timeout

    wait_for_ip {
      ipv6 = true
      ipv4 = true
    }
  }

  cpu {
    cores = var.cpu_cores
    numa  = true
    type  = "host"
  }

  memory {
    dedicated = var.memory_mb
    floating  = 0
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
    size         = var.disk_size_gb
    ssd          = true
    discard      = "on"
    iothread     = true
    aio          = "io_uring"
    cache        = "none"
    backup       = false
  }

  cdrom {
    interface = "ide3"
    file_id   = var.iso_file_id
  }

  # Setup mode, so the Secure Boot ISO enrolls Sidero's keys on first boot
  efi_disk {
    datastore_id      = var.disk_datastore_id
    file_format       = "raw"
    type              = "4m"
    pre_enrolled_keys = false
  }

  tpm_state {
    datastore_id = var.disk_datastore_id
    version      = "v2.0"
  }

  dynamic "network_device" {
    for_each = var.vnets
    content {
      bridge = network_device.value
      model  = "virtio"
      # The bridge's, so a guest runs as large as its VNet carries
      mtu      = 1
      firewall = true
      queues   = 2 * var.cpu_cores
    }
  }

  dynamic "hostpci" {
    for_each = var.pci_mapping == null ? [] : [var.pci_mapping]
    content {
      device  = "hostpci0"
      mapping = hostpci.value
      pcie    = true
      rombar  = true
    }
  }

  serial_device {
    device = "socket"
  }

  tablet_device = false

  operating_system {
    type = "l26"
  }
}

# Against what the guest sends, not what reaches it: what it admits is its own firewall's
resource "proxmox_virtual_environment_firewall_options" "this" {
  node_name = proxmox_virtual_environment_vm.this.node_name
  vm_id     = proxmox_virtual_environment_vm.this.vm_id

  enabled   = true
  macfilter = true
  ipfilter  = false
  radv      = false
  ndp       = true
  dhcp      = true

  input_policy  = "ACCEPT"
  output_policy = "ACCEPT"
}
