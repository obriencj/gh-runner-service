"""
Credentials are per instance. These tests pin that down, because a shared
credential is the specific failure that made multi-org runners impossible.
"""

import os
import stat

import pytest


@pytest.fixture
def mod(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_RUNNER_ROOT", str(tmp_path))
    for name in list(os.sys.modules):
        if name.startswith("preoccupied.gh_runner_ctl"):
            del os.sys.modules[name]
    from preoccupied.gh_runner_ctl import secret as s

    return s


class TestNaming:
    def test_path_is_keyed_by_instance(self, mod):
        assert mod.credential_path("01").name == "01"
        assert mod.credential_path("02").name == "02"
        assert mod.credential_path("01") != mod.credential_path("02")

    def test_secret_name_is_keyed_by_instance(self, mod):
        assert mod.secret_name("01") == "gh-runner-token-01"
        assert mod.secret_name("build-a") == "gh-runner-token-build-a"


class TestWriteRead:
    def test_round_trip(self, mod):
        mod.write_credential("01", "  ghp_abc123\n")
        assert mod.read_credential("01") == "ghp_abc123"

    def test_written_0600(self, mod):
        mod.write_credential("01", "tok")
        mode = stat.S_IMODE(mod.credential_path("01").stat().st_mode)
        assert mode == 0o600

    def test_directory_is_root_only(self, mod):
        mod.write_credential("01", "tok")
        mode = stat.S_IMODE(mod.CREDENTIALS_DIR.stat().st_mode)
        assert mode == 0o700

    def test_instances_do_not_share(self, mod):
        mod.write_credential("01", "token-one")
        mod.write_credential("02", "token-two")
        assert mod.read_credential("01") == "token-one"
        assert mod.read_credential("02") == "token-two"

    def test_empty_refused(self, mod):
        from preoccupied.gh_runner_ctl import CtlError

        with pytest.raises(CtlError, match="empty"):
            mod.write_credential("01", "   \n ")

    def test_missing_names_the_fix(self, mod):
        from preoccupied.gh_runner_ctl import CtlError

        with pytest.raises(CtlError, match="set-credential 01"):
            mod.read_credential("01")

    def test_loose_permissions_refused(self, mod):
        mod.write_credential("01", "tok")
        os.chmod(mod.credential_path("01"), 0o644)
        from preoccupied.gh_runner_ctl import CtlError

        with pytest.raises(CtlError, match="readable"):
            mod.read_credential("01")

    def test_has_credential(self, mod):
        assert not mod.has_credential("01")
        mod.write_credential("01", "tok")
        assert mod.has_credential("01")


class TestOrphans:
    def test_reports_credentials_without_instances(self, mod):
        mod.write_credential("01", "a")
        mod.write_credential("99", "b")
        assert mod.orphan_credentials({"01"}) == ["99"]

    def test_no_instances_no_orphans_when_dir_absent(self, mod):
        assert mod.orphan_credentials({"01"}) == []


# The end.
