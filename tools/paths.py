from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

IPAM = REPO / "ipam" / "ipam.yaml"
IPAM_SCHEMA = REPO / "ipam" / "schema.json"

CLUSTERS = REPO / "tofu" / "clusters"
CLUSTER_SCHEMA = CLUSTERS / "schema.json"
CLUSTER_EXTRACTS = CLUSTERS / "generated"

ANSIBLE_INVENTORY = REPO / "ansible" / "inventory" / "hosts.yml"
