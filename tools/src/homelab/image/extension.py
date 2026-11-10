"""System extensions: a workload's packages over an image-based image's tree, built by mkosi with the
`sysext` profile and the extension's own, and released at a cadence of their own.

The lock records what each was last released from, for each image it's built over: the hash of what its build
reads, and the packages it adds, as apt resolves them over the image's tree. Only a moved lock rebuilds one, at
upstream's version and a rebuild count, as Arch's pkgrel: a dependency the base provides moves with the base,
under Debian's ABI, while one the extension bundles is a copy only a rebuild updates."""

import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomli_w

from ..bootstrap.shell import BootstrapError, say
from ..repo import Repo
from . import build, builder, images as catalog, publish, signing, snapshot
from .images import Image
from .test import _check, _fresh, _report
from .vm import Vm

PROFILE = "sysext"
# As dpkg names the architectures mkosi does
DEBIAN_ARCHITECTURES = {"x86-64": "amd64", "arm64": "arm64"}

# apt's answer to what installing the packages over the base would add, from Debian's archives at the
# snapshot and the vendor's train, and nothing of the builder's own
RESOLVE = r"""
set -eu
for tree in /trees/*; do if [ -d "$tree" ]; then cp -a "$tree/." /; fi; done
mkdir -p /work/state/lists/partial /work/cache/archives/partial /work/parts
architecture=$1
shift
apt() {
  apt-get -q -o Dir::State::status=/base/var/lib/dpkg/status -o Dir::State=/work/state -o Dir::Cache=/work/cache \
    -o Dir::Etc::SourceList=/dev/null -o Dir::Etc::SourceParts=/work/sources -o Dir::Etc::Parts=/work/parts \
    -o APT::Architecture="$architecture" -o APT::Architectures::="$architecture" -o Debug::NoLocking=1 -o APT::Sandbox::User=root \
    -o APT::Install-Recommends=false "$@"
}
apt update >&2
apt --simulate install "$@" | sed -n 's/^Inst \([^ ]*\) \(\[[^]]*\] \)\{0,1\}(\([^ ]*\) .*/\1=\3/p'
"""

# A machine taking the extension, as it would its release's, from a test's channel instead
INSTALL = """
sudo sh -ec '
mkdir -p /etc/sysupdate.{name}.d
sed "s#^Path=http.*#Path={url}#" /usr/lib/sysupdate.{name}.d/{name}.transfer > /etc/sysupdate.{name}.d/{name}.transfer
/usr/lib/systemd/systemd-sysupdate enable-component {name} >/dev/null
/usr/lib/systemd/systemd-sysupdate --component={name} update
'
"""

# Where sysupdate reads an extension's versions, and their sums, signed
UPDATES = """
set -eu
mkdir -p {into}
mv /cache/output/{name}.raw {into}/{name}_{version}.raw
cd {into}
rm -f SHA256SUMS SHA256SUMS.gpg
sha256sum -- *.raw > SHA256SUMS
GNUPGHOME=/out/keys/gnupg gpg --batch --quiet --yes --detach-sign --output SHA256SUMS.gpg SHA256SUMS
cat > {name}_{version}.packages
chown -R {owner} /out/extensions
"""

# Every file it holds that links, by what the merged /usr resolves it against: how many, then any that don't
LIBRARIES = """
files=$(sudo sh -c 'systemd-dissect --mtree /var/lib/extensions/{name}.raw.v/{file}' \\
  | awk '$2 == "type=file" && ($3 ~ /mode=0?[0-7]*[1357]$/ || $1 ~ /\\.so(\\.|$)/) {{ sub(/^\\./, "", $1); print $1 }}')
echo "$files" | grep -c .
for f in $files; do ldd "$f" 2>/dev/null | sed "s#^#$f: #"; done | grep "not found" || true
"""

# Every unit it holds, a template by an instance of it
UNITS = """
sudo sh -c 'systemd-dissect --list /var/lib/extensions/{name}.raw.v/{file}' \\
  | sed -n 's#^usr/lib/systemd/system/\\([^/]*\\.\\(service\\|socket\\|timer\\|path\\|target\\|mount\\)\\)$#\\1#p' \\
  | sed 's/@\\./@check./' \\
  | while read -r unit; do echo "$unit $(systemctl show -P LoadState "$unit")"; done
"""


@dataclass(frozen=True)
class Extension:
    name: str
    images: tuple[str, ...]
    # Whose upstream version it's released at
    package: str
    packages: tuple[str, ...]
    snapshot: str | None
    sandbox_trees: tuple[Path, ...]
    # Our own beside its packages, by where it holds each: a role's, which a package-managed host takes from it
    files: tuple[tuple[str, Path], ...]
    # What its build reads, besides the packages it resolves to
    inputs: tuple[Path, ...]

    def args(self, image: Image) -> list[str]:
        # Its release file is named by its output, which systemd-sysext looks for under any version: bare, here,
        # since a profile's Output=%i expands before the extension's own sets the name
        return ["--architecture", image.architecture, "--profile", PROFILE, "--profile", self.name,
                "--output", self.name]


def register(verbs) -> None:
    extension = verbs.add_parser(
        "extension", help="build and test system extensions",
        description=__doc__.split("\n\n")[0],
    )
    subverbs = extension.add_subparsers(dest="extension_verb", required=True)

    lock = subverbs.add_parser(
        "lock", help="resolve what each extension adds over its images, and record it",
        description="Builds each image's tree, resolves what the extension would add over it, and writes the lock "
        "where that or the extension's own files moved, at its next version.",
    )
    lock.add_argument("names", nargs="*", help="extensions in images/extensions.toml (default: every one)")
    lock.add_argument("--check", action="store_true", help="write nothing, and fail if any would be rebuilt")
    _add_image(lock)
    lock.set_defaults(run=_lock)

    built = subverbs.add_parser(
        "build", help="build an extension at its locked version",
        description=f"Builds the extension over each of its images' trees, into {builder.CACHE}/<image>/extensions/"
        "<extension>/, refusing a lock that no longer resolves.",
    )
    built.add_argument("name", help="an extension in images/extensions.toml, e.g. pdns-recursor")
    build.add_sign(built)
    _add_image(built)
    built.set_defaults(run=_build)

    published = subverbs.add_parser(
        "publish", help="publish an extension's build to its canary channels",
        description=f"Adds the extension built in the cache to each of its images' canary channels, keeping their "
        f"newest {publish.KEEP} versions, their sums signed with the release key. The bucket is IMAGES_BUCKET.",
    )
    published.add_argument("name", help="an extension in images/extensions.toml, e.g. pdns-recursor")
    _add_image(published)
    published.set_defaults(run=_publish)

    promoted = subverbs.add_parser(
        "promote", help="promote a version of an extension from canary to stable",
        description="Copies a version from each of the extension's images' canary channels to stable.",
    )
    promoted.add_argument("name", help="an extension in images/extensions.toml, e.g. pdns-recursor")
    promoted.add_argument("version", help="a version canary has, e.g. 5.4.9-1")
    _add_image(promoted)
    promoted.set_defaults(run=_promote)

    test = subverbs.add_parser(
        "test", help="boot an image with its extensions merged and check what they hold",
        description="Boots the image's build as a new VM, installs its built extensions through their sysupdate "
        "components, and checks each merges at boot, its users exist, its units load, and everything in it links.",
    )
    test.add_argument("image", help="an image-based image in images/images.toml, e.g. ymir-amd64-vm")
    test.add_argument("names", nargs="*", help="its extensions to merge (default: every one built for it)")
    test.add_argument("--playbook", type=Path, action="append",
                      help="a playbook to run against each new VM, as host `build`, twice: the second must change "
                      "nothing. Each gets a VM of its own")
    test.set_defaults(run=_test)


def _add_image(parser) -> None:
    parser.add_argument("--image", help="one of the extension's images alone, as a runner of its architecture builds it")


def _images(extension: Extension, image: str | None) -> tuple[str, ...]:
    if image and image not in extension.images:
        raise BootstrapError(f"{extension.name} isn't built over {image}: images/extensions.toml")
    return (image,) if image else extension.images


def channel(image: str, name: str) -> publish.Channel:
    """Where an image's machines take the extension from."""
    return publish.Channel(f"{image}/extensions/{name}", rf"{re.escape(name)}_(.+)\.raw", r"\d+(\.\d+)*-\d+")


def load(images: Path, name: str) -> Extension:
    entries = catalog.extensions(images)
    if name not in entries:
        raise BootstrapError(f"{name!r} is not an extension in {images / 'extensions.toml'}")
    entry = entries[name]
    image = catalog.load(images, entry["images"][0])
    config = catalog.summary(images, "--architecture", image.architecture, "--profile", PROFILE, "--profile", name)
    if config["ImageId"] != name:
        raise BootstrapError(f"the {name} profile makes {config['ImageId']!r}: its ImageId= is its name")
    trees = tuple(images / Path(t["Source"]).relative_to("/images") for t in config["SandboxTrees"]
                  if t["Source"].startswith("/images/"))
    files = tuple((dest, images.parent / source) for dest, source in entry.get("files", {}).items())
    return Extension(
        name=name, images=tuple(entry["images"]), package=entry["package"], packages=tuple(config["Packages"]),
        snapshot=config["Snapshot"], sandbox_trees=trees, files=files,
        inputs=(images / "mkosi.profiles" / PROFILE, images / "mkosi.profiles" / name, *trees,
                *(source for _, source in files)),
    )


def _lockfile(images: Path) -> Path:
    return images / "extensions.lock"


def read_lock(images: Path) -> dict:
    path = _lockfile(images)
    return tomllib.loads(path.read_text()) if path.exists() else {}


def tree(images: Path, image: Image) -> Path:
    """The image's tree as its build lays it out, before any boot chain, built again unless its inputs are
    unchanged: what an extension is built over."""
    path = builder.OUTPUT / f"{image.name}-tree"
    stamp = path.with_suffix(".inputs")
    inputs = build._fingerprint(images.parent, *build.BAKE_INPUTS, *_base_inputs(images))
    if path.is_dir() and stamp.is_file() and stamp.read_text() == inputs:
        return path
    build.bake(images, image.architecture)
    say(f"laying out {image.name}'s tree")
    stamp.unlink(missing_ok=True)
    build._clear(path.name)
    out = builder.CACHE / image.name
    builder.mkosi(images, *image.args, "--image-version", "dev", "--format", "directory", "--output", path.name,
                  "--bootable", "no", "--secure-boot", "no", "--verity", "disabled", "--sign-expected-pcr", "no",
                  *snapshot.sandbox(image.snapshot, image.sandbox_trees, out), "build", mounts={out: "/out"})
    stamp.write_text(inputs)
    return path


def _base_inputs(images: Path) -> list[str]:
    """Everything of images/ but the extensions'."""
    names = set(catalog.extensions(images)) | {PROFILE}
    paths = [p for p in images.iterdir() if p.name not in {"mkosi.profiles", "vendors", "extensions.toml",
                                                           "extensions.lock"}]
    paths += [p for p in (images / "mkosi.profiles").iterdir() if p.name not in names]
    return [str(p.relative_to(images.parent)) for p in sorted(paths)]


def resolve(images: Path, extension: Extension, image: Image) -> list[str]:
    """What building the extension over the image's tree would add, as `package=version`."""
    base = tree(images, image)
    work = builder.CACHE / "resolve" / extension.name
    if work.exists():
        builder.run_in({work.parent: "/parent"}, "rm", "-rf", f"/parent/{work.name}")
    (work / "sources").mkdir(parents=True)
    debian = (f"Types: deb\nURIs: http://deb.debian.org/debian\nSuites: {image.release}\nComponents: main\n"
              "Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n\n"
              f"Types: deb\nURIs: http://deb.debian.org/debian-security\nSuites: {image.release}-security\n"
              "Components: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n")
    keys = snapshot.shipped(extension.sandbox_trees)
    sources = [debian] + [p.read_text() for t in extension.sandbox_trees
                          for p in sorted(t.glob("etc/apt/sources.list.d/*.sources"))]
    for i, text in enumerate(sources):
        (work / "sources" / f"{i}.sources").write_text(
            snapshot.pinned(text, extension.snapshot, keys) if extension.snapshot else text)
    mounts = {work: "/work", base: "/base:ro",
              **{t: f"/trees/{i}:ro" for i, t in enumerate(extension.sandbox_trees)}}
    added = builder.run_in(mounts, "sh", "-c", RESOLVE, "resolve", DEBIAN_ARCHITECTURES[image.architecture],
                           *extension.packages, capture=True)
    return sorted(added.split())


def entry(images: Path, extension: Extension, image: Image) -> dict:
    return {"inputs": build._fingerprint(images.parent, *(str(p.relative_to(images.parent)) for p in extension.inputs)),
            "packages": resolve(images, extension, image)}


def next_version(extension: Extension, locked: dict, entries: dict[str, dict]) -> str | None:
    """The version entries are released at, or None if the lock already holds them."""
    if locked and all(locked.get(image) == entries[image] for image in entries):
        return None
    upstream = _upstream(extension.package, next(iter(entries.values()))["packages"])
    if locked and locked["version"].rsplit("-", 1)[0] == upstream:
        return f"{upstream}-{int(locked['version'].rsplit('-', 1)[1]) + 1}"
    return f"{upstream}-1"


def _upstream(package: str, packages: list[str]) -> str:
    """Upstream's version of the package, without Debian's epoch or revision."""
    for line in packages:
        name, version = line.split("=", 1)
        if name == package:
            return re.sub(r"^\d+:", "", version).rsplit("-", 1)[0]
    raise BootstrapError(f"{package} isn't among what it adds: {packages}")


def _lock(args) -> int:
    images = builder.ensure()
    lock = read_lock(images)
    moved = []
    for name in args.names or list(catalog.extensions(images)):
        extension = load(images, name)
        entries = {}
        if args.image and args.image not in extension.images:
            continue
        for image_name in _images(extension, args.image):
            say(f"resolving {name} over {image_name}")
            entries[image_name] = entry(images, extension, catalog.load(images, image_name))
        if version := next_version(extension, lock.get(name, {}), entries):
            _describe(name, lock.get(name, {}), entries, version)
            moved.append(name)
            lock[name] = {**lock.get(name, {}), "version": version, **entries}
    if args.check:
        return 1 if moved else 0
    if moved:
        header = "# What each extension was last released from: `homelab image extension lock` writes it\n\n"
        _lockfile(images).write_text(header + tomli_w.dumps(dict(sorted(lock.items())), multiline_strings=True))
    say(f"{', '.join(moved)} moved" if moved else "nothing moved")
    return 0


def _describe(name: str, locked: dict, entries: dict[str, dict], version: str) -> None:
    say(f"{name} {locked.get('version', '(new)')} -> {version}")
    for image, now in entries.items():
        was = locked.get(image, {"inputs": None, "packages": []})
        if locked and was["inputs"] != now["inputs"]:
            print(f"  {image}: its files changed")
        for line in sorted(set(was["packages"]) ^ set(now["packages"])):
            print(f"  {image}: {'+' if line in now['packages'] else '-'}{line}")


def _build(args) -> int:
    images = builder.ensure()
    extension = load(images, args.name)
    locked = read_lock(images).get(extension.name)
    if not locked:
        raise BootstrapError(f"{extension.name} isn't locked: `homelab image extension lock {extension.name}`")
    for image_name in _images(extension, args.image):
        image = catalog.load(images, image_name)
        if entry(images, extension, image) != locked[image_name]:
            raise BootstrapError(f"{extension.name}'s lock no longer resolves over {image_name}: "
                                 f"`homelab image extension lock {extension.name}`")
        _build_over(images, extension, image, locked["version"], locked[image_name]["packages"], args.sign)
    return 0


def _build_over(images: Path, extension: Extension, image: Image, version: str, packages: list[str],
                sign: str) -> None:
    say(f"building {extension.name} {version} over {image.name}")
    out = builder.CACHE / image.name
    work = build.signer(images, image, sign, out)
    keys = signing.verity_args() if work else [
        a for flag, name in (("--verity-key", "db.key"), ("--verity-certificate", "db.crt"))
        for a in (flag, f"/out/keys/{name}")]
    base = tree(images, image)
    build._clear(extension.name)
    staged = out / "extension-files" / extension.name
    shutil.rmtree(staged, ignore_errors=True)
    for dest, source in extension.files:
        (staged / dest.lstrip("/")).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, staged / dest.lstrip("/"))
    files = ["--extra-tree", f"/out/extension-files/{extension.name}:/"] if extension.files else []
    files += ["--environment", f"EXTENSION_PACKAGES={' '.join(p.split('=', 1)[0] for p in packages)}"]
    builder.mkosi(images, *extension.args(image), "--image-version", version, "--base-tree", f"/cache/output/{base.name}",
                  "--workspace-directory", "/cache/workspace", *keys, *files,
                  *snapshot.sandbox(extension.snapshot, extension.sandbox_trees, out), "build",
                  mounts={out: "/out"}, signing=work)
    listed = builder.OUTPUT / f"{extension.name}_{version}.packages"
    built = {"=".join(line.split(" ", 1)) for line in listed.read_text().splitlines()}
    beneath = builder.run_in({base: "/base:ro"}, "dpkg-query", "--admindir=/base/var/lib/dpkg", "--show",
                             "--showformat", "${Package}=${Version}\n", capture=True).split()
    added = sorted(built - set(beneath))
    if added != packages:
        raise BootstrapError(f"{extension.name} added {sorted(set(added) ^ set(packages))} other than its lock says")
    into = f"/out/extensions/{extension.name}"
    builder.run_in({out: "/out", builder.CACHE: "/cache", **(signing.mounts(work) if work else {})},
                   "sh", "-ec", f"rm -rf {into}\n" + UPDATES.format(name=extension.name, version=version, into=into,
                                                                    owner=f"{os.getuid()}:{os.getgid()}"),
                   input="\n".join(packages) + "\n", env=signing.env() if work else None)
    listed.unlink()
    say(f"built {out / 'extensions' / extension.name / f'{extension.name}_{version}.raw'}")


def _publish(args) -> int:
    images = builder.ensure()
    extension = load(images, args.name)
    work = signing.prepare(images, builder.CACHE / "signing")
    for image in _images(extension, args.image):
        out = builder.CACHE / image
        files = out / "extensions" / extension.name
        publish.publish(channel(image, extension.name), out, work, (files / "SHA256SUMS").read_text(), files)
    return 0


def _promote(args) -> int:
    images = builder.ensure()
    extension = load(images, args.name)
    for name in _images(extension, args.image):
        image = catalog.load(images, name)
        out = builder.CACHE / name
        publish.promote(channel(name, extension.name), args.version, out,
                        lambda: build.signer(images, image, "kms", out))
    return 0


def _test(args) -> int:
    images = builder.ensure()
    image = catalog.load(images, args.image)
    out = builder.CACHE / image.name
    disk = out / f"{image.name}.{image.format}"
    if not image.image_based or not disk.is_file():
        raise BootstrapError(f"no image-based build at {disk}: `homelab image build {image.name}`")
    built = {p.parent.name: p for p in (out / "extensions").glob("*/*.raw")}
    names = args.names or sorted(built)
    if missing := [n for n in names if n not in built]:
        raise BootstrapError(f"not built for {image.name}: {missing}")
    failures = []
    for playbook in args.playbook or [None]:
        failures += _boot(image, out, disk, names, built, playbook)
    return _report(f"{image.name} with {', '.join(names)}", failures)


def _boot(image: Image, out: Path, disk: Path, names: list[str], built: dict[str, Path],
          playbook: Path | None) -> list[str]:
    """A new VM taking the extensions, checked, and the playbook run against it twice, the second changing nothing."""
    work = _fresh(out)
    vm = Vm(work, "test", image.boot, mounts={out: "/image:ro"}, log=out / "extensions.serial.log")
    failures = []
    try:
        builder.run_in(vm.mounts, "qemu-img", "create", "-q", "-f", "qcow2", "-F", "qcow2",
                       "-b", f"/image/{disk.name}", "/work/disk.qcow2", "4G")
        vm.start("disk.qcow2")
        vm.wait_for_ssh()
        url = vm.serve("/image/extensions")
        for name in names:
            vm.ssh(INSTALL.format(name=name, url=f"{url}{name}/"))
            installed = vm.ssh(f"sudo ls /var/lib/extensions/{name}.raw.v 2>&1 || true").split()
            failures += _check(f"sysupdate installs {name} once its component is enabled",
                               installed == [built[name].name], " ".join(installed))
        vm.reboot()
        vm.ssh("systemctl is-system-running --wait >/dev/null || true")
        merged = vm.ssh("systemd-sysext status --no-pager")
        for name in names:
            failures += _extension(vm, name, built[name].name, merged)
        state = vm.ssh("systemctl is-system-running || true").strip()
        failures += _check("it's running with them merged, with no unit failed", state == "running",
                           state + "\n" + vm.ssh("systemctl --failed --no-legend --plain || true"))
        if playbook:
            extra = {"extensions_enabled": names, "extensions_url": url}
            first = _play(vm, playbook, extra, out / f"{playbook.stem}.first.log")
            if first is None:
                (out / f"{playbook.stem}.journal.log").write_text(vm.ssh(
                    "systemctl --failed --no-pager; sudo journalctl -b --no-pager -n 400 || true"))
            failures += _check(f"{playbook.name} runs", first is not None,
                               f"see {out / f'{playbook.stem}.first.log'} and its journal.log")
            if first is not None:
                again = _play(vm, playbook, extra, out / f"{playbook.stem}.second.log")
                failures += _check(f"{playbook.name} changes nothing run again", again == 0,
                                   f"{again} changed: see {out / f'{playbook.stem}.second.log'}")
    finally:
        vm.remove()
        builder.run_in({out: "/out"}, "sh", "-c", "cp /out/test/serial.log /out/extensions.serial.log; rm -rf /out/test")
    return failures


def _play(vm: Vm, playbook: Path, extra: dict, log: Path) -> int | None:
    """How many tasks the playbook changed on the VM, as the trees run it, or None if it failed."""
    ansible = Repo.discover().root / "ansible"
    env = os.environ | {
        "ANSIBLE_ROLES_PATH": str(ansible / "roles"),
        "ANSIBLE_COLLECTIONS_PATH": f"{ansible / 'collections'}:{Path.home() / '.ansible/collections'}",
        "ANSIBLE_PIPELINING": "true",
        "ANSIBLE_INTERPRETER_PYTHON": "auto_silent",
        "ANSIBLE_FORCE_HANDLERS": "true",
        "ANSIBLE_HOST_KEY_CHECKING": "false",
    }
    with open(log, "w") as stream:
        result = subprocess.run(
            ["ansible-playbook", "-i", str(vm.inventory()), "-i", str(ansible / "baseline"), str(playbook.resolve()),
             "--extra-vars", json.dumps(extra)], env=env, stdout=stream, stderr=subprocess.STDOUT, check=False)
    recap = re.search(r"^build\s*:.*changed=(\d+)", log.read_text(), re.MULTILINE)
    return int(recap[1]) if result.returncode == 0 and recap else None


def _extension(vm: Vm, name: str, file: str, merged: str) -> list[str]:
    failed = _check(f"{name} is merged at boot", re.search(rf"\b{re.escape(name)}\b", merged) is not None, merged)
    users = vm.ssh(f"cat /usr/lib/sysusers.d/{name}.conf 2>/dev/null || true").splitlines()
    for kind, user in (line.split()[:2] for line in users if line.split()[:1] in (["u"], ["g"])):
        found = vm.ssh(f"getent {'passwd' if kind == 'u' else 'group'} {user} || true").strip()
        failed += _check(f"{name}'s {'user' if kind == 'u' else 'group'} {user} exists", bool(found), found)
    units = vm.ssh(UNITS.format(name=name, file=file)).splitlines()
    unloaded = [u for u in units if not u.endswith(" loaded")]
    failed += _check(f"{name}'s {len(units)} units load", bool(units) and not unloaded, "\n".join(unloaded or units))
    count, *unlinked = vm.ssh(LIBRARIES.format(name=name, file=file)).strip().splitlines()
    return failed + _check(f"all {count} of {name}'s executables and libraries link", int(count) > 0 and not unlinked,
                           "\n".join(unlinked))
