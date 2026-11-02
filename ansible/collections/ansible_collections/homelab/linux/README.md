# homelab.linux

Host configuration for any systemd Linux. Every role takes plain variables, and loads what
differs by OS family from its `vars/<family>.yml`; a family without one fails.

| Role | Does |
| --- | --- |
| `sshd` | key-only sshd, and any options a host adds |
| `host` | what's true of one host: when it upgrades, its service accounts |
| `host_firewall` | nftables from a list of rules, rolled back unless Ansible can still connect |

Families: Debian, RedHat. [`tests/linux.yml`](tests/linux.yml) applies every role to a systemd
container of each. A distribution's hardening is its own collection's, Debian's
[`homelab.debian`](../debian).
