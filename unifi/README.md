# UniFi

The UniFi gateways read nothing from NetBox. Their firewall is written through Site Manager's
connector by `homelab unifi`; their FRR has no API, official or otherwise worth building on, so
it's generated here and pasted in by hand.

## `homelab unifi`

```sh
uv run homelab unifi status                        # Site Magic's tunnels
uv run homelab unifi export <dir>                  # every site's policies, zones and networks
uv run homelab unifi apply <site> <step> [--apply] # egress, ipv6, gateway <zone>, vpn
uv run homelab unifi show <site> <from> <to>       # a zone pair's policies of ours, in order
```

A stopgap until a provider writes the firewall whole. Each step is a set of zone policies,
a dry run unless `--apply`; one its zone pair already has, by name or by match, is left
alone, and a pair's allows go above its blocks. A site's console is the one whose serial is
its router's in NetBox. The key is `UniFi API` in `Network`, a Site Manager key.

Every block matches NEW and INVALID alone: UniFi's return traffic is per policy, so a block on
every state would drop the replies to what the gateway opens itself. The networks the
appliances are on keep *Internet Access* ticked, and a policy toward External keeps them
offline instead: unticked, UniFi counts every IPv6 destination outside its own networks as the
internet, replies to a VPN client or a routed service included.

## `generated/<site>-frr.conf`

BGP toward every speaker at the site, from NetBox: a peer group per listen range in the BGP
plugin, accepting only the speaker groups with a session on it, and only what each one
originates. It names no speaker, so adding or replacing one needs no change here.

It isn't committed: its listen ranges and prefix lists are the site's own public prefix.

```
uv run homelab generate           # write it after changing NetBox
uv run homelab generate --check   # exit 1 if it no longer matches NetBox
```

Paste it, whole, into the UDM Pro's BGP configuration. It has no `router ospf`: UniFi
writes that itself, and refuses a pasted one.

## `generated/<site>-ipv6.txt`

Site Magic carries IPv4 alone, so another site's IPv6 is reached over the internet, where it
answers only what it serves the internet. Each site gets an Allow to the other sites'
`internet`-audience IPv6 prefixes, then a Reject of their GUAs, from each of its zones to
External: Happy Eyeballs falls back to IPv4 at once rather than timing out. `homelab unifi apply
<site> ipv6` writes the same policies; this file is what to read them by. It goes the day Site
Magic carries IPv6.

## Fixed IPs

None. A firewall rule names a host by its EUI-64, so nothing needs a reservation; NetBox
records a leased address as status `dhcp`.
