import pytest
from homelab.repo import Repo


def test_discover_walks_up_to_the_marker(tmp_path):
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    assert Repo.discover(deep).root == tmp_path.resolve()


def test_discover_fails_outside_a_repo(tmp_path):
    with pytest.raises(SystemExit):
        Repo.discover(tmp_path)


def test_cluster_files_are_sorted_and_exclude_the_schema(repo):
    (repo.clusters / "b.yaml").write_text("{}")
    (repo.clusters / "a.yaml").write_text("{}")
    (repo.clusters / "schema.json").write_text("{}")
    assert [p.name for p in repo.cluster_files()] == ["a.yaml", "b.yaml"]

