#!/usr/bin/env python3
"""Schema and semantic validation for ipam.yaml. Run in CI on every commit."""
import ipaddress
import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from addressing import (
    site_bgp_ipv4_pool,
    site_bgp_ipv6_pool,
    site_ipv4_supernet,
    site_ipv6_supernet,
    vlan_ipv4_cidr,
    vlan_ipv6_ula_cidr,
)

ROOT = Path(__file__).parent


def load():
    schema = json.loads((ROOT / "schema.json").read_text())
    data = yaml.safe_load((ROOT / "ipam.yaml").read_text())
    return schema, data


def check_schema(schema, data) -> list[str]:
    validator = Draft202012Validator(schema)
    return [
        f"schema: {'.'.join(str(p) for p in err.path) or '<root>'}: {err.message}"
        for err in sorted(validator.iter_errors(data), key=str)
    ]


def check_semantics(data) -> list[str]:
    errors = []

    vlans = data.get("vlans", {})
    seen_ids: dict[int, str] = {}
    for name, vlan in vlans.items():
        if vlan["id"] in seen_ids:
            errors.append(f"vlans: ID {vlan['id']} used by both {seen_ids[vlan['id']]!r} and {name!r}")
        seen_ids[vlan["id"]] = name

    sites = data.get("sites", {})
    seen_index: dict[int, str] = {}
    for name, site in sites.items():
        if site["index"] in seen_index:
            errors.append(f"sites: index {site['index']} used by both {seen_index[site['index']]!r} and {name!r}")
        seen_index[site["index"]] = name
        for vlan_name in site["vlans"]:
            if vlan_name not in vlans:
                errors.append(f"sites.{name}.vlans: {vlan_name!r} not in the vlans catalog")
        try:
            site_v4 = site_ipv4_supernet(data["ipv4_supernet"], site["index"])
            site_v6 = site_ipv6_supernet(data["ipv6_ula_supernet"], site["index"])
            for vlan_name in site["vlans"]:
                if vlan_name in vlans:
                    vlan_ipv4_cidr(site_v4, vlans[vlan_name]["id"])
                    if vlans[vlan_name]["ula"]:
                        vlan_ipv6_ula_cidr(site_v6, vlans[vlan_name]["id"])
            dns_anycast = site.get("dns_anycast") or {}
            if dns_anycast.get("ipv4"):
                bgp_v4 = site_bgp_ipv4_pool(site_v4)
                if ipaddress.ip_address(dns_anycast["ipv4"]) not in bgp_v4:
                    errors.append(f"sites.{name}.dns_anycast.ipv4: {dns_anycast['ipv4']} isn't inside {bgp_v4}")
            if dns_anycast.get("ipv6_ula"):
                bgp_v6 = site_bgp_ipv6_pool(site_v6)
                if ipaddress.ip_address(dns_anycast["ipv6_ula"]) not in bgp_v6:
                    errors.append(f"sites.{name}.dns_anycast.ipv6_ula: {dns_anycast['ipv6_ula']} isn't inside {bgp_v6}")
            if dns_anycast.get("ipv6_gua"):
                if not site.get("gua_prefix"):
                    errors.append(f"sites.{name}.dns_anycast.ipv6_gua: set but the site has no gua_prefix")
                else:
                    bgp_gua = site_bgp_ipv6_pool(ipaddress.ip_network(site["gua_prefix"]))
                    if ipaddress.ip_address(dns_anycast["ipv6_gua"]) not in bgp_gua:
                        errors.append(f"sites.{name}.dns_anycast.ipv6_gua: {dns_anycast['ipv6_gua']} isn't inside {bgp_gua}")
        except ValueError as exc:
            errors.append(f"sites.{name}: {exc}")

    for name, host in data.get("hosts", {}).items():
        if host["site"] not in sites:
            errors.append(f"hosts.{name}: unknown site {host['site']!r}")
            continue
        vlan_name = host.get("vlan")
        if vlan_name is not None and vlan_name not in sites[host["site"]]["vlans"]:
            errors.append(f"hosts.{name}: vlan {vlan_name!r} not enabled at site {host['site']!r}")
            continue

        if vlan_name is None or vlan_name not in vlans:
            continue
        site = sites[host["site"]]
        vlan_id = vlans[vlan_name]["id"]
        if host.get("ipv4"):
            expected_v4 = vlan_ipv4_cidr(site_ipv4_supernet(data["ipv4_supernet"], site["index"]), vlan_id)
            if ipaddress.ip_address(host["ipv4"]) not in expected_v4:
                errors.append(f"hosts.{name}.ipv4: {host['ipv4']} isn't inside {vlan_name}'s {expected_v4}")
        if host.get("ipv6") and vlans[vlan_name]["ula"]:
            expected_v6 = vlan_ipv6_ula_cidr(site_ipv6_supernet(data["ipv6_ula_supernet"], site["index"]), vlan_id)
            if ipaddress.ip_address(host["ipv6"]) not in expected_v6:
                errors.append(f"hosts.{name}.ipv6: {host['ipv6']} isn't inside {vlan_name}'s {expected_v6}")

    return errors


def main() -> int:
    schema, data = load()
    errors = check_schema(schema, data)
    if not errors:
        errors = check_semantics(data)

    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1

    print("ipam.yaml: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
