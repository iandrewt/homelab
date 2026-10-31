# NetBox

Every address this repo uses is decided in NetBox, and every layer reads it there: OpenTofu
through the `netbox` provider, Ansible through `nb_inventory` and the `homelab` model, and the
UniFi router through a config `homelab generate` writes from it. This is what to put in yours,
and how to keep it.

There are two halves:

- **The schema** - Roles, tags, custom fields, templates, routing policies - is the repo's, in
  [`netbox/schema.yaml`](../netbox/schema.yaml). `homelab netbox apply` puts it in your NetBox.
- **The data** - your sites, VLANs, prefixes, addresses, devices - is yours. Nothing in the repo
  holds it; this page says what the code expects to find.

## Standing it up

Run [netbox-docker](https://github.com/netbox-community/netbox-docker), with three plugins:
[netbox-dns](https://github.com/peteeckel/netbox-plugin-dns),
[netbox-bgp](https://github.com/netbox-community/netbox-bgp) and
[netbox-dhcp](https://github.com/sys4/netbox-plugin-dhcp). The repo was built against NetBox 4.7,
netbox-dns 1.7, netbox-bgp 0.20 and netbox-dhcp 0.2. The plugins change shape between versions,
so match them, or expect `homelab netbox plan` to tell you what moved.

**Nothing runs as an admin.** Each consumer is a NetBox user of its own, so the changelog says
which one changed what, and its token can do only what its groups can:

| User | Groups | Its token | In 1Password, as |
|---|---|---|---|
| `homelab` | `readers` | read-only | `NetBox API Token - Homelab`, in Network |
| `ansible` | `readers` | read-only | `NetBox API Token - Ansible`, in Network |
| `tofu-network` | `readers` | read-only | `NetBox API Token - Tofu`, in Network |
| `tofu-platform` | `readers` | read-only | `NetBox API Token - Tofu`, in Platform |
| `schema` | `readers`, `schema` | write | `NetBox API Token - Schema`, in Network |

`readers` may view every type but users, groups, permissions and tokens; `schema` may change
only the types `netbox/schema.yaml` states. The users, groups and permissions are in the schema
too, but `homelab netbox apply` leaves them alone unless asked (below), since whoever can grant a
permission can grant themselves any.

A token is made by hand, on its user, because NetBox shows it only once: `write_enabled` off
unless the table says write. Each 1Password item is an API Credential: the token in
`credential`, NetBox's URL in `hostname`. `NETBOX_ITEM_REF` points the CLI at another item, and
the inventory also needs `NETBOX_API` in the environment, since `nb_inventory` doesn't template
its endpoint.

People sign in as themselves, not as these. Keep the superuser you made NetBox with for the
two things that need it - `apply --identity`, and a restore - and give yourself an ordinary
account for the rest.

## The schema

```
uv run homelab netbox plan      # what differs from netbox/schema.yaml
uv run homelab netbox apply     # make it so, as `schema`

# The users, groups and permissions too, as your superuser
NETBOX_ITEM_REF=op://<vault>/<your admin token> uv run homelab netbox apply --identity
```

On a new NetBox, run `--identity` first: it makes the `schema` user the plain `apply` runs as.
A user is never deleted, whatever `--prune` says.

It also makes a tenant for each team in `tofu/platform/tenancy/teams/`, in the tenant group
its `kind` names. It never touches an allocation. An object of a type it states but the schema
doesn't name is reported, and deleted only with `--prune`.

## The data

Look objects up by what they're called, never by ID: IDs are per instance, and an instance
moves - from a laptop, to a host, to a restore. A **site is its slug** everywhere in the repo;
its NetBox name is where it is, which the repo never needs.

**Sites and VLANs**

- A Site per place, with a short code in `facility` (`alf`), which prefixes its gear's names.
  Regions stay geographic, as NetBox means them.
- A Site Group per cloud region (`au1`), holding the site or sites its AZs are at. A site in
  none is a branch: a network people use, with nothing of the cloud's.
- An AZ is a Location of its site (`alf-az1`), holding the rooms with capacity in one failure
  domain. Other rooms stay directly under the site.
- A VLAN group per site, its slug the site's, scoped to the site, with `vid_ranges` the VIDs
  the site may have.
- A VLAN per network, whose Role is its firewall zone. A VLAN a guest's NIC can join names its
  Proxmox SDN VNet in `vnet`; a cluster's node or corosync network names it in `cluster`.

**Prefixes**

- An aggregate for each block you were given, and a container prefix scoped to each site for
  what it holds - an aggregate can't say whose site it is.
- A prefix per VLAN and family, with the VLAN set and no Role.
- Routed space is a segment: a container whose Role is its tier and kind (`site-services`,
  `home-instances`, `global-services` ...), and `zone` set to the zone its speakers peer from.
- A pool is a prefix inside a segment, its Role the purpose (`pods-default`, `lb-home`). A pool
  one speaker group allocates from names it in `origin`.
- `Is a pool` on everything routed, which has no network or broadcast address; never on a VLAN
  prefix. `Mark utilized` on what something else allocates - Cilium's pools - so NetBox's
  utilisation means something.
- Who may reach a prefix is its audience: `audience` (zone Roles, and `internet`),
  `audience_tags` (trust tags) and `audience_sites`.

**Addresses**

- A gateway is the router's address in a VLAN's prefix, on an interface of the router named
  for the network. A VLAN-only network has none.
- A leased address is status `dhcp`, recorded once it's been leased; SLAAC's are `slaac`.
  `active` is for what a host configures itself: a hypervisor's management address, gateways,
  routed addresses.
- A routed address is a host route, `/32` or `/128`, naming in `origins` every speaker group
  that originates it. One many hosts serve - anycast - goes on an FHRP group, protocol
  `other`, whose members are the interfaces serving it.
- A `dns_name` on every interface address the cloud runs: `<host>.<site>.infra.titmuss.zone`
  on a host's primary, `<network>.<host>.<site>.infra.titmuss.zone` on the rest. An address
  that exists for a service is named by it, under `<region>.sys.titmuss.zone`.

**Routing**

- An ASN per speaker group, assigned to its site, its Role `router`, `dns-fleet` or
  `kubernetes-cluster`; a cluster's names it in `cluster`. The router's device has the role
  `Router Firewall`.
- A BGP session per speaker group allowed on a listen range: the router's device and its
  address there, the group's ASN, the range as the session's remote prefix, and the peer group
  for the range.

**Machines**

- A device or VM for everything, with its role (`dns-resolver`, `hypervisor`, `nas` ...),
  `ansible-managed` if Ansible configures it, its interfaces, and its primary addresses. A BMC
  is its device's `oob_ip`.
- Names: a pet from a theme; gear `<site code>-<role>-<nn>`; the cabling plant as
  [TIA-606](https://en.wikipedia.org/wiki/TIA-606) labels it - a panel by its letter, a jack by
  the panel port it lands on.
- A Proxmox cluster as a Virtualization Cluster scoped to its AZ, named as gear is
  (`alf-pve-01`); a Kubernetes cluster scoped to its region's Site Group, named
  `<region>-<name>` (`au1-shared`), its `number` Cilium's cluster ID.
- A source narrower than a zone carries a trust tag (`connector`, `proxmox-api`, ...), on its
  prefix, its VLAN, or its device or VM.

**DNS, in netbox-dns**

- The `_default_` view names everything inside; give it your blocks as its prefixes. The
  `public` view holds what's published, and the nameservers' and recursor's pools.
- Zones by who answers for the names in them: `<site>.infra` for what the cloud runs at a site,
  `<region>.sys` for its services, `<site>.home` and `clients.<site>.home` for the home's
  network, and `<team>.int` for each team, all under `titmuss.zone`, and a reverse zone per
  block. One a team or external-dns writes itself is tagged `rfc2136`. A zone is signed while
  its DNSSEC policy is active. A name is in one view, never both.
- The reverse zone of the prefix an ISP delegates is `public`, and Route53's: PTRs for what's
  internet-facing, and an NS for each internal block's reverse zone, naming the nameservers.
- The resolvers are devices or VMs with role `dns-resolver`, holding their authoritative
  addresses; the zones' one primary is tagged `dns-primary`.

**DHCP, in netbox-dhcp**

- A server per site router, a subnet per IPv4 network with its pool (the VLAN's IP range),
  lease time, and options: the resolvers, and a search list where a short name is worth typing.

**Services**

- A Service per port a firewall should know of, made from a template, on its host - or on its
  FHRP group, for anycast - with an audience where the port needs other than its prefix's. An
  Ansible host's own firewall admits exactly these, so each port it serves needs one: SSH, and
  on a resolver DNS on its authoritative addresses as well as the recursor's.
- The primary's `PowerDNS API` Service, on its authoritative addresses, which HAProxy serves.

**Pins**

What a host is, recorded once it's checked, by you rather than a consumer (`NETBOX_ITEM_REF` at
your own token, so the changelog says who trusted it):

- `homelab pin ssh <host>`: its Ed25519 host key, in `ssh_host_key`, if your known_hosts already
  holds it or it has the fingerprint its console shows. Again after a rebuild.
- `homelab pin dns-api --fingerprint <sha256>`: the certificate its API serves, in the Service's
  `certificate`, with the fingerprint the resolvers play printed.

## Keeping it

The data is load-bearing in the way Git history is for the code, so back it up as you would
that. A `pg_dump` round-trip, because it keeps every object as it was:

```
docker compose exec -T postgres pg_dump -U netbox netbox | gzip > netbox-$(date +%F).sql.gz
```

**Restoring**, onto a fresh netbox-docker at the *same* NetBox and plugin versions:

```
docker compose up -d postgres
gunzip -c netbox-<date>.sql.gz | docker compose exec -T postgres psql -U netbox netbox
docker compose up -d
```

Then, if it moved, update `hostname` on every NetBox item in 1Password, and `NETBOX_API`.
Nothing else in the repo cares which instance it talks to. Run `homelab netbox plan`: a
restore from before a schema change shows exactly what to apply.
