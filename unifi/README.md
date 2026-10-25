# UniFi

The UDM Pro reads neither NetBox nor anything this repo applies. Its official API exposes
no FRR, and the reverse-engineered one isn't something to build on, so what it needs is
generated here and pasted in by hand.

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

## Fixed IPs

None. A firewall rule names a host by its EUI-64, so nothing needs a reservation; NetBox
records a leased address as status `dhcp`.
