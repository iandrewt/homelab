"""The octoDNS source the repo adds: the DS of each zone PowerDNS signs, read from the primary's
keys at plan time and published in its parent, so nothing holds a copy of them."""

import json
import logging
import ssl
import urllib.request

from octodns.record import Record
from octodns.source.base import BaseSource


class DelegationError(Exception):
    pass


class PowerDnsDelegations(BaseSource):
    log = logging.getLogger("PowerDnsDelegations")
    SUPPORTS = {"DS"}
    SUPPORTS_GEO = False
    SUPPORTS_DYNAMIC = False

    def __init__(self, id, url, api_key, ca_file, ttl=3600):
        super().__init__(id)
        self.url, self.api_key, self.ttl = url, api_key, ttl
        # The primary's pinned certificate, and nothing else
        self.context = ssl.create_default_context(cafile=ca_file)

    def _get(self, path: str):
        request = urllib.request.Request(f"{self.url}{path}", headers={"X-API-Key": self.api_key})
        with urllib.request.urlopen(request, timeout=10, context=self.context) as response:
            return json.load(response)

    def populate(self, zone, target=False, lenient=False):
        delegated = {r.name for r in zone.records if r._type == "NS" and r.name}
        names = [z["name"] for z in self._get("/zones")]
        for name in names:
            if not name.endswith(f".{zone.name}"):
                continue
            # A grandchild's DS is in its own parent, delegated from this one
            if any(name.endswith(f".{between}") and between.endswith(f".{zone.name}") for between in names):
                continue
            label = name.removesuffix(f".{zone.name}")
            values = [
                {"key_tag": int(tag), "algorithm": int(algorithm), "digest_type": 2, "digest": digest}
                for key in self._get(f"/zones/{name}/cryptokeys")
                if key["active"] and key["keytype"] in ("ksk", "csk")
                for tag, algorithm, digest_type, digest in (ds.split() for ds in key.get("ds", []))
                if digest_type == "2"
            ]
            if not values:
                continue
            # A DS with no NS beside it delegates nothing, and its zone stays unreachable
            if label not in delegated:
                raise DelegationError(f"{name} is signed, but {zone.name} has no NS for it in NetBox")
            zone.add_record(
                Record.new(zone, label, {"type": "DS", "ttl": self.ttl, "values": values},
                           source=self, lenient=lenient),
                lenient=lenient,
            )
        return True
