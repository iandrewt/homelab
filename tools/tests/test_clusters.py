from homelab import clusters


def check(cluster, ipam, stem="one"):
    return clusters.check(cluster, "one.yaml", stem, ipam)


def test_a_valid_cluster_has_no_errors(cluster, ipam):
    assert check(cluster, ipam) == []


def test_errors_name_the_file(cluster, ipam):
    cluster["site"] = "nowhere"
    assert check(cluster, ipam)[0].startswith("one.yaml: ")


def test_site_must_exist_in_netbox(cluster, ipam):
    cluster["site"] = "nowhere"
    assert "isn't a site in NetBox" in check(cluster, ipam)[0]


def test_name_must_match_the_filename(cluster, ipam):
    assert "must match the filename" in check(cluster, ipam, stem="elsewhere")[0]


def test_a_guest_default_naming_an_unknown_storage(cluster, ipam):
    cluster["guest_defaults"]["disk_storage"] = "nope"
    assert "isn't in storages" in check(cluster, ipam)[0]


def test_an_integration_acl_naming_an_unknown_storage(cluster, ipam):
    cluster["workloads"]["k8s"] = {
        "comment": "k8s",
        "integrations": {
            "csi": {"comment": "csi", "acls": {"/storage/nope": ["Datastore.Audit"]}}
        },
    }
    assert any("isn't in storages" in e for e in check(cluster, ipam))


def test_snippets_must_be_node_local(cluster, ipam):
    cluster["guest_defaults"]["snippet_storage"] = "pool"
    assert "must be node-local" in check(cluster, ipam)[0]


def test_a_zfspool_no_node_creates(cluster, ipam):
    cluster["storages"]["pool"]["pool"] = "orphan"
    assert "isn't a pool on any node" in check(cluster, ipam)[0]


def test_an_acl_pool_must_be_a_workload_group(cluster, ipam):
    cluster["workloads"]["k8s"] = {
        "comment": "k8s",
        "integrations": {
            "csi": {"comment": "csi", "acls": {"/pool/nope": ["VM.Audit"]}}
        },
    }
    assert any("isn't a workload group" in e for e in check(cluster, ipam))


def test_a_group_colliding_with_an_integration_user(cluster, ipam):
    """wl-k8s-csi@pve would be derived twice, and the access layer's merge would keep
    only one of them."""
    cluster["workloads"]["k8s"] = {
        "comment": "k8s",
        "integrations": {
            "csi": {"comment": "csi", "acls": {"/pool/k8s": ["VM.Audit"]}}
        },
    }
    cluster["workloads"]["k8s-csi"] = {"comment": "collides"}
    assert any("both derive" in e for e in check(cluster, ipam))


def test_missing_optional_sections_do_not_crash(ipam):
    assert clusters.check({}, "empty.yaml", "empty", ipam) == []


def test_a_node_netbox_was_never_told_about(cluster, ipam):
    cluster["nodes"]["ghost"] = {"pools": {}}
    assert any("has no named address" in e for e in check(cluster, ipam))


def test_a_management_vlan_the_site_does_not_have(cluster, ipam):
    cluster["firewall"]["management_vlans"] = ["nope"]
    assert "isn't a VLAN at site" in check(cluster, ipam)[0]
