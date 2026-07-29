"""
The parser implements Podman's --env-file rules, not systemd's
EnvironmentFile rules. These tests pin that difference in place, because it
is the sort of thing a well-meaning future edit "fixes" into a bug.
"""

import os
from pathlib import Path

import pytest


@pytest.fixture
def conf(tmp_path, monkeypatch):
    """Point the whole package at a staging tree."""
    monkeypatch.setenv("GH_RUNNER_ROOT", str(tmp_path))
    for mod in list(os.sys.modules):
        if mod.startswith("preoccupied.gh_runner_ctl"):
            del os.sys.modules[mod]
    from preoccupied.gh_runner_ctl import conf as c

    c.INSTANCES_DIR.mkdir(parents=True)
    return c


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class TestParse:
    def test_basic(self, conf, tmp_path):
        p = write(tmp_path / "a.conf", "A=1\nB=two\n")
        assert conf.parse_env_file(p) == {"A": "1", "B": "two"}

    def test_comments_and_blanks(self, conf, tmp_path):
        p = write(tmp_path / "a.conf", "# hi\n\n  \nA=1\n#B=2\n")
        assert conf.parse_env_file(p) == {"A": "1"}

    def test_quotes_are_literal(self, conf, tmp_path):
        """systemd would strip these. Podman does not, so neither do we."""
        p = write(tmp_path / "a.conf", 'RUNNER_NAME="build box"\n')
        assert conf.parse_env_file(p)["RUNNER_NAME"] == '"build box"'

    def test_value_may_contain_equals(self, conf, tmp_path):
        p = write(tmp_path / "a.conf", "X=a=b=c\n")
        assert conf.parse_env_file(p)["X"] == "a=b=c"

    def test_bare_name_rejected(self, conf, tmp_path):
        p = write(tmp_path / "a.conf", "JUST_A_NAME\n")
        with pytest.raises(conf.CtlError, match="no '='"):
            conf.parse_env_file(p)

    def test_bad_key_rejected(self, conf, tmp_path):
        p = write(tmp_path / "a.conf", "not-a-key=1\n")
        with pytest.raises(conf.CtlError, match="valid key"):
            conf.parse_env_file(p)

    def test_missing_file_is_empty(self, conf, tmp_path):
        assert conf.parse_env_file(tmp_path / "nope.conf") == {}


class TestLint:
    def test_warns_on_quotes(self, conf, tmp_path):
        p = write(tmp_path / "a.conf", 'RUNNER_NAME="x"\n')
        warnings = conf.lint_env_file(p)
        assert any("does not strip quotes" in w for w in warnings)

    def test_clean_file_is_silent(self, conf, tmp_path):
        p = write(tmp_path / "a.conf", "RUNNER_NAME=x\n")
        assert conf.lint_env_file(p) == []


class TestMerge:
    def test_instance_beats_global(self, conf):
        conf.GLOBAL_CONF.write_text("RUNNER_LABELS=global\nMEMORY_MAX=1G\n")
        conf.instance_path("01").write_text("RUNNER_LABELS=local\n")
        inst = conf.load("01")
        assert inst.get("RUNNER_LABELS") == "local"
        assert inst.get("MEMORY_MAX") == "1G"

    def test_default_name_is_host_and_id(self, conf):
        conf.instance_path("01").write_text("RUNNER_URL=x\n")
        assert conf.load("01").get("RUNNER_NAME").endswith("-01")

    def test_sample_is_not_an_instance(self, conf):
        conf.instance_path("01").write_text("RUNNER_URL=x\n")
        (conf.INSTANCES_DIR / "example.conf.sample").write_text("RUNNER_URL=y\n")
        assert [i.iid for i in conf.all_instances()] == ["01"]

    def test_missing_instance_is_an_error(self, conf):
        with pytest.raises(conf.CtlError, match="no such instance"):
            conf.load("99")


class TestClassify:
    def test_keys_route_to_three_places(self, conf):
        conf.instance_path("01").write_text(
            "RUNNER_URL=u\nJOB_MEMORY_MAX=8G\nMEMORY_MAX=2G\nWHAT=x\n"
        )
        groups = conf.load("01").classify()
        assert "RUNNER_URL" in groups["runtime"]
        assert "JOB_MEMORY_MAX" in groups["job"]
        assert "MEMORY_MAX" in groups["unit"]
        assert "WHAT" in groups["unknown"]

    def test_job_shaping_is_not_unit_shaping(self, conf):
        """
        The trap this split exists to avoid: JOB_MEMORY_MAX must never end up
        in a systemd drop-in, where it would silently do nothing, and
        MEMORY_MAX must never be mistaken for a job limit.
        """
        conf.instance_path("01").write_text("JOB_MEMORY_MAX=8G\nMEMORY_MAX=2G\n")
        inst = conf.load("01")
        assert inst.unit_shaping() == {"MemoryMax": "2G"}


class TestInstanceId:
    @pytest.mark.parametrize("iid", ["01", "a", "build-1", "x_2"])
    def test_valid(self, conf, iid):
        assert conf.valid_instance_id(iid)

    @pytest.mark.parametrize("iid", ["", "-x", "a/b", "a b", "../etc", "a.conf"])
    def test_invalid(self, conf, iid):
        assert not conf.valid_instance_id(iid)
