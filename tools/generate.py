#!/usr/bin/env python3
"""Derive artifacts from ipam.yaml: the Ansible inventory, and a per-cluster addressing
extract for OpenTofu. Never hand-edit anything this writes - edit ipam.yaml and rerun.
CI fails the build if the working tree changes afterwards.

Run from anywhere: python3 tools/generate.py

TODO: DHCP reservations once the VLAN catalog is populated.
"""
import ipaddress
import json

import yaml

from paths import ANSIBLE_INVENTORY, CLUSTER_EXTRACTS, CLUSTERS, IPAM, REPO
from addressing import (
    reverse_zone,
    site_ipv4_supernet,
    site_ipv6_supernet,
    vlan_ipv4_cidr,
    vlan_ipv6_ula_cidr,
)


class IndentedDumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


ANSIBLE_MANAGED_ROLES = {"hypervisor", "dns-resolver"}


def cluster_sites() -> dict[str, str]:
    """Which clusters get an ipam extract, and from which site. Read from the cluster
    files rather than listed here as well - a second list could disagree with them, and
    adding a cluster should mean touching one file.
    """
    return {
        path.stem: yaml.safe_load(path.read_text())["site"]
        for path in sorted(CLUSTERS.glob("*.yaml"))
    }


def fqdn(name: str, site: str, internal_domain: str) -> str:
    return f"{name}.{site}.{internal_domain}"


def build_inventory(data: dict) -> dict:
    internal_domain = data["domains"]["internal"]
    all_hosts: dict[str, dict] = {}
    groups: dict[str, list[str]] = {}
    sites_seen: set[str] = set()

    for name, host in data.get("hosts", {}).items():
        if host["role"] not in ANSIBLE_MANAGED_ROLES:
            continue
        host_vars = {
            "ansible_host": fqdn(name, host["site"], internal_domain),
            "site": host["site"],
            "role": host["role"],
        }
        if host.get("vlan"):
            vlan_id = data["vlans"][host["vlan"]]["id"]
            host_vars["vlan_id"] = vlan_id
            # .1 / ::1 of the host's own VLAN. Emitted so nothing downstream has to
            # hand-write a router address ipam already knows.
            site_index = data["sites"][host["site"]]["index"]
            v4_net = vlan_ipv4_cidr(site_ipv4_supernet(data["ipv4_supernet"], site_index), vlan_id)
            host_vars["gateway_ipv4"] = str(v4_net.network_address + 1)
            if data["vlans"][host["vlan"]]["ula"]:
                v6_net = vlan_ipv6_ula_cidr(site_ipv6_supernet(data["ipv6_ula_supernet"], site_index), vlan_id)
                host_vars["gateway_ipv6"] = str(v6_net.network_address + 1)
        if host.get("ipv4"):
            host_vars["static_ipv4"] = host["ipv4"]
        if host.get("ipv6"):
            host_vars["static_ipv6"] = host["ipv6"]
        if host.get("os"):
            host_vars["os"] = host["os"]
            groups.setdefault(host["os"], []).append(name)
        all_hosts[name] = host_vars
        sites_seen.add(host["site"])
        role_group = host["role"].replace("-", "_")
        groups.setdefault(role_group, []).append(name)

    all_vars: dict = {}
    # Only meaningful while every Ansible-managed host is at one site -- if that ever
    # changes, this needs to become per-site vars instead of one blanket "all" value.
    if len(sites_seen) == 1:
        site_name = next(iter(sites_seen))
        forward_zone = f"{site_name}.{internal_domain}"
        site_index = data["sites"][site_name]["index"]
        site_ipv4_16 = site_ipv4_supernet(data["ipv4_supernet"], site_index)
        site_ipv6_48 = site_ipv6_supernet(data["ipv6_ula_supernet"], site_index)
        ipv4_reverse_zone = reverse_zone(site_ipv4_16)
        ipv6_reverse_zone = reverse_zone(site_ipv6_48)
        all_vars["powerdns_resolver_zone"] = forward_zone
        all_vars["powerdns_resolver_reverse_zones"] = [ipv4_reverse_zone, ipv6_reverse_zone]

        site = data["sites"][site_name]
        dns_anycast = site.get("dns_anycast") or {}
        if dns_anycast.get("ipv4"):
            all_vars["dns_anycast_v4"] = dns_anycast["ipv4"]
        if dns_anycast.get("ipv6_ula"):
            all_vars["dns_anycast_v6_ula"] = dns_anycast["ipv6_ula"]
        if dns_anycast.get("ipv6_gua"):
            all_vars["dns_anycast_v6_gua"] = dns_anycast["ipv6_gua"]
        if site.get("gua_prefix"):
            all_vars["site_gua_prefix"] = site["gua_prefix"]

        # Every dns-resolver at this site belongs in the NS rrset at every zone's apex -
        # not just whichever one happens to be primary, or a zone only ever gets one NS
        # record and looks like it has no redundancy.
        dns_resolver_ns_fqdns = [
            fqdn(name, host["site"], internal_domain) + "."
            for name, host in data.get("hosts", {}).items()
            if host["site"] == site_name and host["role"] == "dns-resolver"
        ]

        all_zones = [forward_zone, ipv4_reverse_zone, ipv6_reverse_zone]
        if dns_resolver_ns_fqdns:
            all_vars["pdns_provision_zones"] = [
                {"name": zone + ".", "kind": "Master", "nameservers": dns_resolver_ns_fqdns} for zone in all_zones
            ]
            all_vars["pdns_provision_zone_metadata"] = [
                {"zone": zone + ".", "kind": "SOA-EDIT-API", "metadata": ["DEFAULT"]} for zone in all_zones
            ]

        pdns_provision_records = []
        for name, host in data.get("hosts", {}).items():
            if host["site"] != site_name:
                continue
            record_name = fqdn(name, host["site"], internal_domain) + "."
            for family, rtype, zone in (
                ("ipv4", "A", forward_zone),
                ("ipv6", "AAAA", forward_zone),
            ):
                if host.get(family):
                    pdns_provision_records.append(
                        {
                            "zone": zone + ".",
                            "name": record_name,
                            "type": rtype,
                            "ttl": 3600,
                            "records": [{"content": host[family], "disabled": False}],
                        }
                    )
            for family, zone in (("ipv4", ipv4_reverse_zone), ("ipv6", ipv6_reverse_zone)):
                if host.get(family):
                    pdns_provision_records.append(
                        {
                            "zone": zone + ".",
                            "name": ipaddress.ip_address(host[family]).reverse_pointer + ".",
                            "type": "PTR",
                            "ttl": 3600,
                            "records": [{"content": record_name, "disabled": False}],
                        }
                    )
        if pdns_provision_records:
            all_vars["pdns_provision_records"] = pdns_provision_records

    return {
        "all": {
            "hosts": all_hosts,
            "vars": all_vars,
            "children": {group: {"hosts": {n: None for n in names}} for group, names in groups.items()},
        }
    }


def build_tofu_ipam(data: dict, site_name: str) -> dict:
    internal_domain = data["domains"]["internal"]
    site = data["sites"][site_name]
    site_ipv4_16 = site_ipv4_supernet(data["ipv4_supernet"], site["index"])
    site_ipv6_48 = site_ipv6_supernet(data["ipv6_ula_supernet"], site["index"])
    dns_anycast = site.get("dns_anycast") or {}

    hosts, nodes = {}, {}
    for name, host in data.get("hosts", {}).items():
        if host["site"] != site_name:
            continue
        # Hypervisors for their management addressing, guests because OpenTofu builds
        # them. A physical non-hypervisor is neither.
        is_node = host["role"] == "hypervisor"
        if not is_node and host.get("kind") != "vm":
            continue

        vlan_id = data["vlans"][host["vlan"]]["id"]
        v4_net = vlan_ipv4_cidr(site_ipv4_16, vlan_id)
        v6_net = vlan_ipv6_ula_cidr(site_ipv6_48, vlan_id)
        # .1 / ::1 of the VLAN is the UDM Pro, consistent with frr_bgp_peer_address.
        v4_gateway = str(v4_net.network_address + 1)
        v6_gateway = str(v6_net.network_address + 1)

        if host["role"] == "dns-resolver":
            # A resolver can't be pointed at the anycast VIP it serves - and at
            # cloud-init time it isn't running yet either. Use the gateway.
            dns_servers = [v4_gateway, v6_gateway]
        else:
            dns_servers = [a for a in (dns_anycast.get("ipv4"), dns_anycast.get("ipv6_ula")) if a]

        entry = {
            "vlan_id": vlan_id,
            "ipv4_address": f"{host['ipv4']}/{v4_net.prefixlen}",
            "ipv6_address": f"{host['ipv6']}/{v6_net.prefixlen}",
            "ipv4_gateway": v4_gateway,
        }

        if host["role"] == "hypervisor":
            # No IPv6 gateway: the router advertises a link-local next-hop, so the
            # default route is discovered rather than pinned to an address that changes
            # whenever the prefix does.
            nodes[name] = entry
        else:
            hosts[name] = entry | {
                "dns_servers": dns_servers,
                "search_domains": [f"{site_name}.{internal_domain}", internal_domain],
            }

    return {"hosts": hosts, "nodes": nodes}


def main() -> int:
    data = yaml.safe_load(IPAM.read_text())
    inventory = build_inventory(data)

    ANSIBLE_INVENTORY.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# GENERATED by tools/generate.py from ipam/ipam.yaml. Do not hand-edit -\n"
        "# edit the source and regenerate. CI fails the build if this drifts.\n"
    )
    ANSIBLE_INVENTORY.write_text(header + yaml.dump(inventory, Dumper=IndentedDumper, sort_keys=False))
    print(f"wrote {ANSIBLE_INVENTORY.relative_to(REPO)}")

    CLUSTER_EXTRACTS.mkdir(parents=True, exist_ok=True)
    sites = cluster_sites()
    for cluster_name, site_name in sites.items():
        if site_name not in data["sites"]:
            raise SystemExit(f"tofu/clusters/{cluster_name}.yaml names site {site_name!r}, which isn't in ipam.yaml")
        out = CLUSTER_EXTRACTS / f"{cluster_name}.json"
        out.write_text(json.dumps(build_tofu_ipam(data, site_name), indent=2, sort_keys=True) + "\n")
        print(f"wrote {out.relative_to(REPO)}")

    # Deleting a cluster file used to mean editing a list here too, which is what stopped
    # its extract being left behind. Nothing prompts for that now, so prune.
    for stale in sorted(set(CLUSTER_EXTRACTS.glob("*.json")) - {CLUSTER_EXTRACTS / f"{c}.json" for c in sites}):
        stale.unlink()
        print(f"removed {stale.relative_to(REPO)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
