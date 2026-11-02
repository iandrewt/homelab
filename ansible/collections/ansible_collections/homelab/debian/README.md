# homelab.debian

Hardened Debian: `hardening` applies CIS Debian 13 and the unattended-upgrades policy, and
`network` sets networkd's defaults for every link. Both take plain variables. What any systemd Linux shares is [`homelab.linux`](../linux)'s.

`hardening` includes [DEBIAN13-CIS](https://github.com/ansible-lockdown/DEBIAN13-CIS), a role,
so install it beside this collection:

```
ansible-galaxy role install -r requirements.yml
```
