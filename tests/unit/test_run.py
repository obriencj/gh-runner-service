"""
Subprocess plumbing.

The cwd test exists because of a real failure: an operator running
`gh-runner-ctl rm 01` from root's shell got

    command failed (125): podman secret ls --format json
    cannot chdir to /root: Permission denied

runuser drops to the service account but inherits the caller's working
directory, and gh-runner cannot enter /root. It broke every podman and
systemctl call, from any cwd root happened to be sitting in.
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

    return _run


class TestCwd:
    def test_runs_from_root_directory(self, mod, monkeypatch):
        """Whatever the caller's cwd, the child starts somewhere traversable."""
        seen = {}
        real = subprocess.run

        def spy(cmd, **kw):
            seen.update(kw)
            return real(["true"], capture_output=True, text=True)

        monkeypatch.setattr(mod.subprocess, "run", spy)
        mod.run(["echo", "hi"], user=False)
        assert seen["cwd"] == "/"


# The end.
