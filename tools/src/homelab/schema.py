"""NetBox's schema, from netbox/schema.yaml and the team catalog: the Roles, tags, custom fields,
templates and policies every consumer reads NetBox by. Allocations - prefixes, VLANs, addresses,
sessions, devices - are NetBox's own, and nothing here touches them.

`plan` prints what differs; `apply` makes it so, with a token that may write. An object the
schema doesn't state is reported, and deleted only with `--prune`. Users, groups and object
permissions are left alone unless `--identity` asks, with an admin's token: whoever can grant
a permission can grant themselves any.
"""

import os
import secrets
import sys
from dataclasses import dataclass, field

import yaml

from . import netbox
from .repo import Repo

WRITE_TOKEN_REF = "op://Network/NetBox API Token - Schema"
DEFAULT_OWNER = "Network"
# A team's kind, as the catalog says it, and the tenant group it's in
TENANT_GROUPS = {"infrastructure": "infrastructure", "application": "applications"}


@dataclass(frozen=True)
class Kind:
    """One type of object the schema states, keyed by its natural key. A field is compared as
    `plain`, a `choice` (its value), a `set`, or a `ref:<kind>`/`refs:<kind>` by that kind's key."""
    name: str
    endpoint: str
    key: str
    fields: dict[str, str]
    owned: bool = True
    noun: str = ""
    identity: bool = False
    prunable: bool = True

    @property
    def label(self) -> str:
        return self.noun or self.name.removesuffix("s").replace("_", " ")


KINDS = [
    Kind("owner_groups", "users.owner_groups", "name", {"description": "plain"}, owned=False),
    Kind("owners", "users.owners", "name", {"group": "ref:owner_groups", "description": "plain"}, owned=False),
    Kind("tenant_groups", "tenancy.tenant_groups", "slug", {"name": "plain", "description": "plain"}),
    Kind("tenants", "tenancy.tenants", "slug",
         {"name": "plain", "group": "ref:tenant_groups", "description": "plain"}),
    Kind("rirs", "ipam.rirs", "slug", {"name": "plain", "is_private": "plain"}, noun="RIR"),
    Kind("roles", "ipam.roles", "slug", {"name": "plain", "description": "plain"}),
    Kind("tags", "extras.tags", "slug", {"name": "plain", "description": "plain", "object_types": "set"},
         owned=False),
    Kind("custom_fields", "extras.custom_fields", "name",
         {"object_types": "set", "type": "choice", "related_object_type": "plain", "label": "plain",
          "description": "plain"}),
    Kind("cluster_types", "virtualization.cluster_types", "slug", {"name": "plain"}),
    Kind("asn_ranges", "ipam.asn_ranges", "slug",
         {"name": "plain", "rir": "ref:rirs", "start": "plain", "end": "plain"}, noun="ASN range"),
    Kind("service_templates", "ipam.service_templates", "name", {"protocol": "choice", "ports": "set"}),
    Kind("dns_views", "plugins.netbox_dns.views", "name", {"description": "plain"}, owned=False,
         noun="DNS view"),
    Kind("routing_policies", "plugins.bgp.routing_policy", "name", {}, owned=False, noun="routing policy"),
    Kind("peer_groups", "plugins.bgp.peer_group", "name",
         {"export_policies": "refs:routing_policies", "extra_attributes": "plain"}, owned=False),
    Kind("groups", "users.groups", "name", {"description": "plain"}, owned=False, identity=True),
    # Never pruned: a person's account, and the superuser applying this, aren't the schema's
    Kind("users", "users.users", "username", {"groups": "refs:groups", "is_active": "plain"}, owned=False,
         identity=True, prunable=False),
    Kind("permissions", "users.permissions", "name",
         {"description": "plain", "enabled": "plain", "object_types": "set", "actions": "set",
          "constraints": "plain", "groups": "refs:groups", "users": "refs:users"}, owned=False, identity=True),
]
BY_NAME = {k.name: k for k in KINDS}


@dataclass
class Op:
    action: str  # create, update, prune, or extra: undeclared and left alone
    kind: Kind
    key: object
    values: dict = field(default_factory=dict)

    def __str__(self) -> str:
        sign = {"create": "+", "update": "~", "prune": "-", "extra": "?"}[self.action]
        text = f"{sign} {self.kind.label} {self.key}"
        if self.action == "extra":
            return f"{text}: not in the schema"
        return text + ("" if not self.values else " " + ", ".join(f"{k}={v}" for k, v in self.values.items()))


def diff(kind: Kind, wanted: dict, existing: dict, prune: bool = False) -> list[Op]:
    """What makes `existing` what `wanted` says, both by natural key with their fields in
    natural-key form. A field the schema doesn't state is never compared."""
    ops = []
    for key, values in wanted.items():
        have = existing.get(key)
        if have is None:
            ops.append(Op("create", kind, key, values))
            continue
        changed = {f: v for f, v in values.items() if _same(kind.fields.get(f, "plain"), have.get(f)) != _same(
            kind.fields.get(f, "plain"), v)}
        if changed:
            ops.append(Op("update", kind, key, changed))
    for key in existing.keys() - wanted.keys():
        ops.append(Op("prune" if prune else "extra", kind, key))
    return ops


def _same(how: str, value):
    """A field's value in the one form both sides compare in."""
    if how in ("set",) or how.startswith("refs:"):
        return sorted(value or [], key=str)
    if value in ("", None, {}):
        return None
    return value


def wanted(repo: Repo) -> dict[str, dict]:
    """Everything the schema states, by kind and key, owner filled in."""
    schema = yaml.safe_load((repo.root / "netbox" / "schema.yaml").read_text())
    schema["tenants"] = {
        path.stem: {"name": team["title"], "group": TENANT_GROUPS[team["kind"]], "description": team["comment"]}
        for path in sorted((repo.root / "tofu" / "platform" / "tenancy" / "teams").glob("*.yaml"))
        for team in [yaml.safe_load(path.read_text())]
    }
    rules = {}
    for name, policy in (schema.get("routing_policies") or {}).items():
        rules |= {(name, int(index)): {"action": action} for index, action in (policy or {}).pop("rules", {}).items()}
    schema["routing_policies"] = {name: {} for name in schema.get("routing_policies") or {}}
    schema["routing_policy_rules"] = rules
    for kind in KINDS:
        if kind.owned:
            for values in (schema.get(kind.name) or {}).values():
                values.setdefault("owner", DEFAULT_OWNER)
    for values in (schema.get("users") or {}).values():
        values.setdefault("is_active", True)
    for values in (schema.get("permissions") or {}).values():
        values.setdefault("enabled", True)
    return schema


def expand(permissions: dict, object_types: list[str]) -> None:
    """`*` as every public type NetBox has, but those a permission excepts."""
    for values in permissions.values():
        excepted = set(values.pop("except", []))
        if "*" in values["object_types"]:
            values["object_types"] = sorted(t for t in object_types if t not in excepted)


class Live:
    """NetBox's objects of each kind, in natural-key form, and the records behind them."""

    def __init__(self, nb):
        self.nb, self.records, self.forms = nb, {}, {}

    def endpoint(self, kind: Kind):
        found = self.nb
        for part in kind.endpoint.split("."):
            found = getattr(found, part)
        return found

    def read(self, kind: Kind):
        records = {getattr(r, kind.key): r for r in self.endpoint(kind).all()}
        self.records[kind.name] = records
        self.forms[kind.name] = {key: self._form(kind, r) for key, r in records.items()}

    def _form(self, kind: Kind, record) -> dict:
        form = {}
        for name, how in {**kind.fields, **({"owner": "ref:owners"} if kind.owned else {})}.items():
            value = getattr(record, name, None)
            if how == "choice":
                value = getattr(value, "value", value)
            elif how == "set":
                value = sorted(value or [])
            elif how.startswith("ref:"):
                value = value and getattr(value, BY_NAME[how[4:]].key, None)
            elif how.startswith("refs:"):
                value = sorted(getattr(v, BY_NAME[how[5:]].key) for v in value or [])
            elif value is not None and not isinstance(value, (str, int, float, bool, list, dict)):
                value = dict(value)  # a JSON field, as pynetbox gives it back
            form[name] = value
        return form

    def resolve(self, kind: Kind, values: dict) -> dict:
        """A natural-key form as the API takes it: each reference as its id."""
        out = {}
        for name, value in values.items():
            how = {**kind.fields, "owner": "ref:owners"}.get(name, "plain")
            if how.startswith("ref:"):
                out[name] = value and self.records[how[4:]][value].id
            elif how.startswith("refs:"):
                out[name] = [self.records[how[5:]][v].id for v in value]
            else:
                out[name] = value
        return out


def run(apply: bool, prune: bool, identity: bool) -> int:
    repo = Repo.discover()
    schema = wanted(repo)
    try:
        live = Live(netbox.api(os.environ.get(netbox.ITEM_REF_ENV, WRITE_TOKEN_REF) if apply else None))
    except netbox.Unreachable as down:
        print(f"FAIL: NetBox: {down}", file=sys.stderr)
        return 1
    if identity:
        expand(schema.get("permissions") or {},
               [f"{t.app_label}.{t.model}" for t in live.nb.core.object_types.all() if t.public])
    changes = 0
    for kind in KINDS:
        if kind.identity and not identity:
            continue
        live.read(kind)
        for op in diff(kind, schema.get(kind.name) or {}, live.forms[kind.name], prune and kind.prunable):
            print(op)
            if op.action == "extra":
                continue
            changes += 1
            if apply:
                _do(live, kind, op)
        if apply:
            live.read(kind)
    changes += _rules(live, schema["routing_policy_rules"], apply, prune)
    if not identity:
        print("users, groups and permissions not checked: --identity, with an admin's token")
    print(f"{changes} changes {'applied' if apply else 'planned'}")
    return 0


def _do(live: Live, kind: Kind, op: Op):
    endpoint = live.endpoint(kind)
    if op.action == "create":
        values = live.resolve(kind, op.values)
        if kind.name == "users":
            # A consumer signs in by token alone, so its password is one nobody knows
            values["password"] = secrets.token_urlsafe(32)
        endpoint.create({kind.key: op.key, **values})
    elif op.action == "update":
        live.records[kind.name][op.key].update(live.resolve(kind, op.values))
    else:
        live.records[kind.name][op.key].delete()


def _rules(live: Live, wanted_rules: dict, apply: bool, prune: bool) -> int:
    """A routing policy's rules, keyed by its name and their index."""
    kind = Kind("routing_policy_rules", "plugins.bgp.routing_policy_rule", "index", {"action": "choice"},
                owned=False, noun="routing policy rule")
    records = {(r.routing_policy.name, r.index): r for r in live.endpoint(kind).all()}
    forms = {key: {"action": getattr(r.action, "value", r.action)} for key, r in records.items()}
    changes = 0
    for op in diff(kind, wanted_rules, forms, prune):
        print(op)
        if op.action == "extra":
            continue
        changes += 1
        if not apply:
            continue
        if op.action == "create":
            policy, index = op.key
            live.endpoint(kind).create({"routing_policy": live.records["routing_policies"][policy].id,
                                        "index": index, **op.values})
        elif op.action == "update":
            records[op.key].update(op.values)
        else:
            records[op.key].delete()
    return changes


def register(subparsers) -> None:
    parser = subparsers.add_parser("netbox", help="bring NetBox's schema into line", description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True, title="actions")
    for name, apply, help_ in (("plan", False, "show what differs from the schema"),
                               ("apply", True, "make NetBox's schema what the repo says")):
        action = actions.add_parser(name, help=help_)
        action.add_argument("--prune", action="store_true", help="delete what the schema doesn't state")
        action.add_argument("--identity", action="store_true",
                            help="users, groups and permissions too, which needs an admin's token")
        action.set_defaults(run=lambda args, apply=apply: run(apply, args.prune, args.identity))
