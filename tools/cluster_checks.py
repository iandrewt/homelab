"""Schema and semantic checks for tofu/clusters/*.yaml"""
import yaml

import schema
from paths import CLUSTER_SCHEMA, CLUSTERS, IPAM, REPO


def check_storages(rel, cluster, errors) -> None:
    """Storage bindings live with whatever owns them rather than in one map, so gather
    them back up and check each names a storage that exists."""
    storages = cluster.get("storages") or {}
    guest_defaults = cluster.get("guest_defaults") or {}

    bindings = {
        "image_storage": cluster.get("image_storage"),
        "guest_defaults.disk_storage": guest_defaults.get("disk_storage"),
        "guest_defaults.snippet_storage": guest_defaults.get("snippet_storage"),
    }
    for group, workload in (cluster.get("workloads") or {}).items():
        for name, integration in (workload.get("integrations") or {}).items():
            for acl_path in integration.get("acls") or {}:
                if acl_path.startswith("/storage/"):
                    where = f"workloads.{group}.integrations.{name}.acls[{acl_path}]"
                    bindings[where] = acl_path.removeprefix("/storage/")

    for where, storage_id in bindings.items():
        if storage_id is not None and storage_id not in storages:
            errors.append(f"{rel}: {where} names {storage_id!r}, which isn't in storages")

    # bpg uploads snippets over SSH, which only works against a node-local datastore.
    snippet_storage = storages.get(guest_defaults.get("snippet_storage"))
    if snippet_storage and snippet_storage.get("type") not in ("directory", None):
        errors.append(
            f"{rel}: guest_defaults.snippet_storage is a {snippet_storage['type']} storage - "
            "snippets must be node-local"
        )

    pools = {p for node in (cluster.get("nodes") or {}).values() for p in (node.get("pools") or {})}
    for storage_id, storage in storages.items():
        pool = storage.get("pool")
        if storage.get("type") == "zfspool" and pool not in pools:
            errors.append(
                f"{rel}: storages.{storage_id}.pool {pool!r} isn't a pool on any node - "
                "nothing would create it"
            )


def check_identities(rel, cluster, errors) -> None:
    """The access layer merge()s workloads and their integrations into one map of users,
    and their ACL entries into one map of roles. A collision there silently drops an
    entry rather than failing, so derived names have to be unique here."""
    workloads = cluster.get("workloads") or {}
    derived: dict[str, str] = {}

    def derive(name: str, source: str) -> None:
        if name in derived:
            errors.append(f"{rel}: {source} and {derived[name]} both derive {name!r}")
        derived[name] = source

    for group, workload in workloads.items():
        derive(f"wl-{group}@pve", f"workloads.{group}")
        for name, integration in (workload.get("integrations") or {}).items():
            where = f"workloads.{group}.integrations.{name}"
            derive(f"wl-{group}-{name}@pve", where)

            for acl_path in integration.get("acls") or {}:
                suffix = "root" if acl_path == "/" else acl_path.lstrip("/").replace("/", "-")
                derive(f"role wl-{group}-{name}-{suffix}", f"{where}.acls[{acl_path}]")

                if acl_path.startswith("/pool/"):
                    pool = acl_path.removeprefix("/pool/")
                    if pool not in workloads:
                        errors.append(
                            f"{rel}: {where}.acls[{acl_path}] names {pool!r}, "
                            "which isn't a workload group"
                        )


def check_nodes(rel, cluster, errors) -> None:
    for node_name, node in (cluster.get("nodes") or {}).items():
        uplink = (node.get("management") or {}).get("uplink")
        if uplink and uplink in (node.get("uplinks") or []):
            errors.append(
                f"{rel}: nodes.{node_name}.management.uplink {uplink!r} is also in uplinks - "
                "a NIC can't carry VM traffic and be given over to management"
            )


def check() -> list[str]:
    errors: list[str] = []
    sites = (yaml.safe_load(IPAM.read_text()) or {}).get("sites", {})

    for path in sorted(CLUSTERS.glob("*.yaml")):
        rel = path.relative_to(REPO)
        cluster = yaml.safe_load(path.read_text())

        errors += schema.errors(CLUSTER_SCHEMA, cluster, str(rel))
        if cluster.get("site") and cluster["site"] not in sites:
            errors.append(f"{rel}: site {cluster['site']!r} isn't in ipam.yaml")

        check_storages(rel, cluster, errors)
        check_identities(rel, cluster, errors)
        check_nodes(rel, cluster, errors)

        if cluster.get("name") and cluster["name"] != path.stem:
            errors.append(f"{rel}: name {cluster['name']!r} must match the filename ({path.stem!r})")

    return errors
