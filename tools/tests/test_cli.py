import tomli_w
import yaml
from conftest import SITE
from homelab import cli, netbox
from homelab.bootstrap import answers, pi
from homelab.model import load


def test_every_entry_point_renders_from_the_model(snapshot, monkeypatch, capsys):
    """Each reads the model's keys itself, and only a rebuild runs them: a key renamed under
    them is found mid-reinstall, or here."""
    monkeypatch.setattr(netbox, "fetch", lambda item=None: snapshot)
    args = cli.build_parser().parse_args(["network-config", SITE, "--resolver", "ratatoskr"])
    assert args.run(args) == 0
    assert yaml.safe_load(capsys.readouterr().out)["network"]

    site = load(snapshot)["sites"][SITE]
    assert pi.documents(site, "ratatoskr", "ssh-ed25519 AAAA", None)["network-config"]

    facts = {"site": SITE, "fqdn": "yggdrasil.one.test", "cidr": "2001:db8:1:1300::5/64",
             "mac": "AA:BB:CC:DD:EE:FF", "wwn": "0x5000", "serial": "S1"}
    cluster = {"mailto": "root@one.test", "timezone": "UTC"}
    answer = answers.answer(facts, cluster, site, "$6$hash", "ssh-ed25519 AAAA")
    tomli_w.dumps(answer)
    assert (answer["network"]["gateway"], answer["network"]["dns"]) == ("2001:db8:1:1300::1", "2001:db8::53")
