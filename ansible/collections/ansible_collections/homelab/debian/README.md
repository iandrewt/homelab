# homelab.debian

Hardened Debian. Every role takes plain variables; none reads NetBox or 1Password.

| Role | Does |
| --- | --- |
| `hardening` | CIS Debian 13, sshd, the unattended-upgrades policy |
| `host` | what's true of one host: when it upgrades, its service accounts |
| `host_firewall` | nftables from a list of rules, rolled back unless Ansible can still connect |

`hardening` includes [DEBIAN13-CIS](https://github.com/ansible-lockdown/DEBIAN13-CIS), which a
collection can't depend on, so install it beside this one:

```
ansible-galaxy role install -r requirements.yml
```
