#!/usr/bin/env python3
"""Derive artifacts from ipam.yaml. Never hand-edit anything this writes - edit
ipam.yaml and rerun. CI fails the build if the working tree changes afterwards.

TODO: DHCP reservations once the VLAN catalog is populated.
"""
import ipaddress
from pathlib import Path

import yaml

from addressing import reverse_zone, site_ipv4_supernet, site_ipv6_supernet


class IndentedDumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


ROOT = Path(__file__).parent
INVENTORY_OUT = ROOT.parent / "ansible" / "inventory" / "hosts.yml"

ANSIBLE_MANAGED_ROLES = {"hypervisor", "dns-resolver"}


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
            host_vars["vlan_id"] = data["vlans"][host["vlan"]]["id"]
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
        site_index = data["sites"][site_name]["index"]
        site_ipv4_16 = site_ipv4_supernet(data["ipv4_supernet"], site_index)
        site_ipv6_48 = site_ipv6_supernet(data["ipv6_ula_supernet"], site_index)
        ipv4_reverse_zone = reverse_zone(site_ipv4_16)
        ipv6_reverse_zone = reverse_zone(site_ipv6_48)
        # Named individually (not just positions in the list below) so records.yml can
        # address "the v4 one" directly rather than relying on list order.
        all_vars["ipv4_reverse_zone"] = ipv4_reverse_zone
        all_vars["ipv6_reverse_zone"] = ipv6_reverse_zone
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
        if dns_resolver_ns_fqdns:
            all_vars["dns_resolver_ns_fqdns"] = dns_resolver_ns_fqdns

        dns_records = []
        for name, host in data.get("hosts", {}).items():
            if host["site"] != site_name:
                continue
            record_name = fqdn(name, host["site"], internal_domain) + "."
            for family, rtype in (("ipv4", "A"), ("ipv6", "AAAA")):
                if host.get(family):
                    dns_records.append(
                        {
                            "name": record_name,
                            "type": rtype,
                            "ttl": 3600,
                            "changetype": "REPLACE",
                            "records": [{"content": host[family], "disabled": False}],
                        }
                    )
        if dns_records:
            all_vars["dns_records"] = dns_records

        # ptr_records_v4/v6 kept separate (rather than one list) so records.yml can push
        # each to its own reverse zone without having to work out which zone a given
        # rrset belongs to.
        ptr_records_v4 = []
        ptr_records_v6 = []
        for name, host in data.get("hosts", {}).items():
            if host["site"] != site_name:
                continue
            record_name = fqdn(name, host["site"], internal_domain) + "."
            if host.get("ipv4"):
                ptr_records_v4.append(
                    {
                        "name": ipaddress.ip_address(host["ipv4"]).reverse_pointer + ".",
                        "type": "PTR",
                        "ttl": 3600,
                        "changetype": "REPLACE",
                        "records": [{"content": record_name, "disabled": False}],
                    }
                )
            if host.get("ipv6"):
                ptr_records_v6.append(
                    {
                        "name": ipaddress.ip_address(host["ipv6"]).reverse_pointer + ".",
                        "type": "PTR",
                        "ttl": 3600,
                        "changetype": "REPLACE",
                        "records": [{"content": record_name, "disabled": False}],
                    }
                )
        if ptr_records_v4:
            all_vars["ptr_records_v4"] = ptr_records_v4
        if ptr_records_v6:
            all_vars["ptr_records_v6"] = ptr_records_v6

    return {
        "all": {
            "hosts": all_hosts,
            "vars": all_vars,
            "children": {group: {"hosts": {n: None for n in names}} for group, names in groups.items()},
        }
    }


def main() -> int:
    data = yaml.safe_load((ROOT / "ipam.yaml").read_text())
    inventory = build_inventory(data)

    INVENTORY_OUT.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# GENERATED by ipam/generate.py from ipam/ipam.yaml. Do not hand-edit -\n"
        "# edit the source and regenerate. CI fails the build if this drifts.\n"
    )
    INVENTORY_OUT.write_text(header + yaml.dump(inventory, Dumper=IndentedDumper, sort_keys=False))
    print(f"wrote {INVENTORY_OUT.relative_to(ROOT.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
