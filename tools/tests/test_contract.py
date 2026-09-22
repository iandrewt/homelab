"""The invariant the model exists to hold.

If `load()` returns, the one remaining generator can index the result freely - so a
snapshot that validates must render without a KeyError or a None slipping through. A
generator needing a `.get()` is a missing check in the model, and this is what notices.
"""

import dataclasses

from homelab import render
from homelab.model import load
from homelab.repo import Repo


def rendered(tmp_path, snapshot):
    return render.plan(Repo(tmp_path), load(snapshot)).rendered


def test_the_frr_config_renders_from_a_snapshot_that_validates(tmp_path, snapshot):
    out = rendered(tmp_path, snapshot)
    assert [p.name for p in out] == ["one-frr.conf"]
    assert out[next(iter(out))].strip()


def test_a_site_with_no_bgp_purposes_generates_nothing(tmp_path, snapshot):
    bare = dataclasses.replace(snapshot, communities=[], devices=[])
    assert rendered(tmp_path, bare) == {}


def test_a_site_with_no_router_asn_generates_nothing(tmp_path, snapshot):
    quiet = [a for a in snapshot.asns if a["role"]["slug"] != "router"]
    assert rendered(tmp_path, dataclasses.replace(snapshot, asns=quiet, devices=[])) == {}
