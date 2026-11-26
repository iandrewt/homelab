---
paths:
  - "images/**"
  - "tools/src/homelab/image/**"
  - "tools/src/homelab/bootstrap/pi.py"
  - ".github/workflows/images.yml"
  - ".github/workflows/lock.yml"
  - ".github/workflows/trains.yml"
  - ".github/workflows/snapshot.yml"
  - "ansible/tests/**"
  - "docs/images.md"
---

# Images

**`images/` is one mkosi project**: the distribution in `mkosi.conf.d/<distribution>/`,
each family (`mutable`, `ymir`) and target (`vm`, `raspi3`) a profile in `mkosi.profiles/`, and an
image a pair of them, which a `mkosi.conf.d` file matching both names; `images.toml` holds only what
mkosi has no setting for. **Every package a build takes is Debian's snapshot's** (`Snapshot=` in
`mkosi.conf.d/debian/`, a bake input), the tools tree's too; a source mkosi doesn't make (ymir's forky,
the bake's apt while the baseline runs) is rewritten to the snapshot, or refused, since apt falls back to
the live archive without a word. The snapshot workflow opens its weekly bump as a PR, the lock with it,
whose images build when dispatched at its branch, and the kernel is the bake's, so it moves with the snapshot. **A distribution is baked once per architecture**: the `bake` profile,
booted by `mkosi vm` with a login its credentials make, `baseline.yml` run against it with no
NetBox or 1Password loaded, then sealed into a tar every image takes as `BaseTrees=`. A family and
target lay themselves over it with mkosi alone, adding what CIS doesn't touch or taking away what
they don't want, and run no Ansible of their own. A mutable image is a tree laid out on its
target's disk; **ymir** is laid out as a signed image: a UKI, `/usr` on erofs under verity, its
`/etc` a confext in `/usr` under the machine's own, updated A/B by sysupdate, with systemd from
forky. Whatever systemd reads before tmpfiles restores `/etc` on first boot moves to `/usr`;
`homelab image test` checks the first boot is hardened as the second is. **On a Pi** (`ymir-raspi3`) its
firmware loads U-Boot, from experimental since boot counting renames a file and 2025.07 is the first
whose EFI can, and U-Boot's EFI loads systemd-boot: no TPM so nothing encrypted, no Secure Boot, its
initrd's modules listed so two UKIs fit the ESP, its console the mini UART. `card.py` lays a card out
whole before first boot from the image's own repart definitions, as `bootstrap pi` does, so nothing
repartitions it; its test boots qemu's raspi3b, which has no network, and reads its checks off the
console, as the extension test does with each extension laid in its `/var`, so no playbook runs there.

**A workload on ymir is a system extension**, on a cadence of its own: a profile in `mkosi.profiles/`
with the `sysext` one, built `Overlay=yes` over the image's tree (`--format directory`, no boot chain),
so it holds only what it adds, named in `extensions.toml`. A vendor's train has no snapshot, so its
source is let through only signed by a key in `images/vendors/`. **`extensions.lock` is what each was
last released from**, per image: what apt adds over the tree, every bundled package at its version, and
its own files' hash. Only a moved lock rebuilds one, at upstream's version and a count (`5.4.9-2`, as
Arch's pkgrel): the base's libraries move with the base, under Debian's ABI and the release file's
`VERSION_ID`, but a bundled one is a copy. A build refuses a lock that no longer resolves, and an
extension that installs anything else; CI checks the lock on every change, and `trains.yml` locks a
vendor's release again in a PR, each image on a runner of its architecture (`lock.yml`). What a package's scripts write in `/etc` and `/var` is gone: its users
and their groups become `sysusers.d`, the directories its packages hold in `/var` tmpfiles, and its
enablement and configuration are the machine's. It may hold a role's own files (`files` in
`extensions.toml`), the anycast loop and the resolvers' health check, which a package-managed host
takes from the role. Each is a disabled
sysupdate component in ymir, which a machine enables, so a new extension reaches machines with the
image's next release; ymir merges only images db's key signed. A newer one, like a newer image, is taken by
sysupdate's reboot, never by a live refresh. **An update is kept once the host does its job**
(`host_upgrade_check`, a resolver's what anycast-check asks), by `health-check` before
`boot-complete.target`: everything new has one try, so a failing boot renames what it tried with none left
and reboots into what came before. Hosts are staggered by channel, never by a window: hugin follows
`canary` and ratatoskr `stable`, so a promotion is a release a resolver has served on. Extensions can't
merge in the initrd: `/var` isn't mounted there, so `systemd-sysext-activate` asks again for what they enable.

**`bootstrap pi` writes a card**: the newest `ymir-arm64-raspi3` in a channel (`IMAGES_UPDATE_URL`'s), its
signed sums checked against `images/secure-boot/updates.pgp` by fingerprint, laid out by `card.py` at the
card's size with the hybrid MBR the Pi's boot ROM reads, its identity as ESP credentials: a machine ID from
the board's serial in NetBox (a reflash keeps it, a new board is a new machine), the hostname, the key, and
networkd's own unit, a resolver's gateways its resolvers.

**An image is released by a signed tag**, `image/<image>/<YYYY.MM.N>` on `main`: `images.yml`
builds and tests it at that commit, and publishes it with its package manifest and `SHA256SUMS`,
signed keylessly by the workflow (`docs/images.md` says how to check one). An image-based one is signed by the release key,
`alias/images` in KMS, which only the `release` environment's role may sign with (`--sign kms`). Both
are in the Artifact Signing account (`platform/aws/signing`), which, like Tofu State, only the bootstrap
role administers, so no one who administers Platform can make themselves the signer; it only signs, and
`images-publish` writes the update bucket, in Artifact Distribution (`platform/aws/images`), as the
profile `publish` names. mkosi's
signing tools have no network, so its PKCS#11 module runs behind `p11-kit server`, which `mkosi box`
starts from the tools tree, and db trusts it by `images/secure-boot/db.crt`, issued by KEK
(`homelab image db`). Its release publishes its update files and its disk (a qcow2, or a card's raw image as
`.raw.xz`) to the `canary` channel
of the update bucket (`homelab image publish`), which a site's image library pins from `stable`,
behind CloudFront on the Free flat-rate plan, whose WAF admits every site's public aggregates from NetBox; promoting to `stable` is `gh workflow run images.yml
--ref image/<image>/<version> -f promote=true`. Each channel keeps five versions, its `SHA256SUMS`
checked against the release key before anything's added. **An extension is released alike**, by
`extension/<extension>/<version>` at the version its lock holds, tested on its image's newest release,
into `<image>/extensions/<extension>/{canary,stable}/`.
