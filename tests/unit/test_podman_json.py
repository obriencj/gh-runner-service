"""
podman_json tolerance.

`gh-runner-ctl enable 01` died with

    could not parse podman output: Expecting value: line 1 column 1 (char 0)

which reported the parser's complaint and discarded what podman actually
said. Both halves are bugs: the fragility, and the error that made it
undiagnosable.
"""

import os
import subprocess

import pytest


@pytest.fixture
def mod(monkeypatch, tmp_path):
    monkeypatch.setenv("GH_RUNNER_ROOT", str(tmp_path))
    for name in list(os.sys.modules):
        if name.startswith("preoccupied.gh_runner_ctl"):
            del os.sys.modules[name]
    from preoccupied.gh_runner_ctl import _run

    # There is no gh-runner account on a dev box; as_service_user would fail
    # long before podman_json got a chance to parse anything.
    monkeypatch.setattr(_run, "as_service_user", lambda argv: argv)
    return _run


def _fake(mod, monkeypatch, stdout, stderr=""):
    def spy(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout, stderr)

    monkeypatch.setattr(mod.subprocess, "run", spy)


class TestEmptyForms:
    @pytest.mark.parametrize("out", ["", "  \n ", "[]", "null", "{}", "\n[]\n"])
    def test_all_mean_nothing_here(self, mod, monkeypatch, out):
        _fake(mod, monkeypatch, out)
        assert mod.podman_json("secret", "ls") == []


class TestRealData:
    def test_list_passes_through(self, mod, monkeypatch):
        _fake(mod, monkeypatch, '[{"Name": "gh-runner-token-01"}]')
        assert mod.podman_json("secret", "ls")[0]["Name"] == "gh-runner-token-01"

    def test_bare_object_becomes_a_list(self, mod, monkeypatch):
        _fake(mod, monkeypatch, '{"Name": "x"}')
        assert mod.podman_json("secret", "ls") == [{"Name": "x"}]

    def test_leading_warning_is_skipped(self, mod, monkeypatch):
        _fake(mod, monkeypatch, 'WARN[0000] something\n[{"Name": "x"}]')
        assert mod.podman_json("secret", "ls") == [{"Name": "x"}]


class TestErrors:
    def test_error_includes_podman_output(self, mod, monkeypatch):
        from preoccupied.gh_runner_ctl import CtlError

        _fake(mod, monkeypatch, "totally not json", "and a stderr line")
        with pytest.raises(CtlError) as e:
            mod.podman_json("secret", "ls")
        msg = str(e.value)
        assert "totally not json" in msg
        assert "and a stderr line" in msg
        assert "secret ls" in msg

    def test_broken_json_still_reports_the_text(self, mod, monkeypatch):
        from preoccupied.gh_runner_ctl import CtlError

        _fake(mod, monkeypatch, '[{"Name": ')
        with pytest.raises(CtlError, match="Name"):
            mod.podman_json("secret", "ls")


# The end.
