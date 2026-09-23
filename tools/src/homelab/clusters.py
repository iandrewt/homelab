import yaml

from . import schema


def check(data: dict, source: str, stem: str, ipam, schema_path=None) -> list[str]:
    data = data or {}
    errors = schema.errors(schema_path, data, source) if schema_path else []
    if errors:
        return errors

    def error(message: str) -> None:
        errors.append(f"{source}: {message}")

    for rule in (
        _site,
        _name,
        _references_into_ipam,
        _storage_bindings,
        _snippets,
        _zfs_pools,
        _identities,
    ):
        rule(data, stem, ipam, error)

    return errors


def _site(data, stem, ipam, error) -> None:
    if data.get("site") and data["site"] not in ipam["sites"]:
        error(f"site {data['site']!r} isn't a site in NetBox")


def _name(data, stem, ipam, error) -> None:
    # terraform.workspace picks the file by name
    if data.get("name") and data["name"] != stem:
        error(f"name {data['name']!r} must match the filename ({stem!r})")


def _references_into_ipam(data, stem, ipam, error) -> None:
    """Every node this file names has to resolve, and every VLAN has to exist.

    A Proxmox node is one of the few things NetBox is told a name for, because the
    installer freezes its address. A node missing here means either the cluster file names
    something that was never built, or NetBox was never told - both worth failing on.
    """
    site = ipam["sites"].get(data.get("site"))
    if site is None:
        return

    named = {name.split(".")[0] for name in site["named"].values()}
    for node in data.get("nodes") or {}:
        if node not in named:
            error(f"{node!r} has no named address at site {site['slug']!r}")

    for name in (data.get("firewall") or {}).get("management_vlans") or []:
        if name not in site["vlans"]:
            error(
                f"firewall.management_vlans: {name!r} isn't a VLAN at site {site['slug']!r}"
            )


def _storage_bindings(data, stem, ipam, error) -> None:
    storages = data.get("storages") or {}
    guest_defaults = data.get("guest_defaults") or {}

    bindings = {
        "image_storage": data.get("image_storage"),
        "guest_defaults.disk_storage": guest_defaults.get("disk_storage"),
        "guest_defaults.snippet_storage": guest_defaults.get("snippet_storage"),
    }
    for group, workload in (data.get("workloads") or {}).items():
        for name, integration in (workload.get("integrations") or {}).items():
            for path in integration.get("acls") or {}:
                if path.startswith("/storage/"):
                    where = f"workloads.{group}.integrations.{name}.acls[{path}]"
                    bindings[where] = path.removeprefix("/storage/")

    for where, storage_id in bindings.items():
        if storage_id is not None and storage_id not in storages:
            error(f"{where} names {storage_id!r}, which isn't in storages")


def _snippets(data, stem, ipam, error) -> None:
    storages = data.get("storages") or {}
    storage = storages.get((data.get("guest_defaults") or {}).get("snippet_storage"))
    if storage and storage.get("type") not in ("directory", None):
        error(
            f"guest_defaults.snippet_storage is a {storage['type']} storage - "
            "snippets must be node-local"
        )


def _zfs_pools(data, stem, ipam, error) -> None:
    nodes = data.get("nodes") or {}
    pools = {p for node in nodes.values() for p in (node.get("pools") or {})}
    for storage_id, storage in (data.get("storages") or {}).items():
        if storage.get("type") == "zfspool" and storage.get("pool") not in pools:
            error(
                f"storages.{storage_id}.pool {storage.get('pool')!r} isn't a pool on any "
                "node - nothing would create it"
            )


def _identities(data, stem, ipam, error) -> None:
    """The access layer merges workloads and integrations into one map of users, and
    their ACLs into one map of roles. A collision there drops an entry silently."""
    workloads = data.get("workloads") or {}
    derived: dict[str, str] = {}

    def derive(name: str, source: str) -> None:
        if name in derived:
            error(f"{source} and {derived[name]} both derive {name!r}")
        derived[name] = source

    for group, workload in workloads.items():
        derive(f"wl-{group}@pve", f"workloads.{group}")
        for name, integration in (workload.get("integrations") or {}).items():
            where = f"workloads.{group}.integrations.{name}"
            derive(f"wl-{group}-{name}@pve", where)

            for path in integration.get("acls") or {}:
                suffix = "root" if path == "/" else path.lstrip("/").replace("/", "-")
                derive(f"role wl-{group}-{name}-{suffix}", f"{where}.acls[{path}]")

                if (
                    path.startswith("/pool/")
                    and path.removeprefix("/pool/") not in workloads
                ):
                    error(
                        f"{where}.acls[{path}] names a pool that isn't a workload group"
                    )


def load_all(repo, ipam) -> tuple[dict[str, dict], list[str]]:
    clusters, errors = {}, []
    for path in repo.cluster_files():
        data = yaml.safe_load(path.read_text()) or {}
        found = check(data, repo.relative(path), path.stem, ipam, repo.cluster_schema)
        errors += found
        if not found:
            clusters[path.stem] = data
    return clusters, errors
