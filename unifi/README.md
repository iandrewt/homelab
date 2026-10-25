# UniFi

The UDM Pro reads neither NetBox nor anything this repo applies. Its official API exposes
no FRR, and the reverse-engineered one isn't something to build on, so what it needs is
generated here and pasted in by hand.

## `generated/<site>-frr.conf`

BGP toward every speaker at the site, from NetBox: one peer group per VLAN tagged
`bgp-listen`, and per purpose a prefix-list and a community-list that a route has to match
both of. It names no speaker, so adding or replacing one needs no change here.

```
uv run homelab generate           # rewrite it after changing NetBox
uv run homelab generate --check   # exit 1 if it no longer matches NetBox
```

Paste it into the UDM Pro's FRR configuration.

## Fixed IPs

Reservations are UniFi's, entered in the console. NetBox records a leased address as status
`dhcp` whether or not it has one.

## `firewall.md`

The zones, rules and switching settings applied by hand in the console - the intent, with
the console as the record of whether it is true.
