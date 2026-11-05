# Secure Boot

What every image enrolls on its first boot, in setup mode: our PK and KEK, and a db holding
[Debian's Secure Boot CA](https://dsa.debian.org/secure-boot-ca), which signs systemd-boot and
every kernel, and the release key, which signs an image-based release's UKI and verity.

| File | Is |
| --- | --- |
| `auto/PK.auth`, `auto/KEK.auth`, `auto/db.auth` | the signed variables systemd-boot enrolls from the ESP's `loader/keys/auto/` |
| `PK.crt`, `KEK.crt` | the interim PK and KEK, made by `homelab image keys`; their private keys are `Secure Boot Key - PK` and `- KEK` in Break-glass |
| `db.crt` | the release key's certificate, issued by KEK for `alias/images` in KMS, which only a release may sign with; made by `homelab image db` |
| `debian-secure-boot-ca.der` | Debian's CA, SHA-256 `079646974bce09b1f04da67bd722d1fb0947ae4c4010bccdbba52d5b23cbf1a2`, as dsa.debian.org and Debian's signed shim both have it |
