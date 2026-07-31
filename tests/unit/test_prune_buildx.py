"""
Reaping buildx's leftovers.

buildx is a CLI plugin that speaks the API directly rather than shelling out,
so the buildkitd container it creates never passes through the shim: no
role=job label, and positive selection cannot find it. Name is all we have.
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
    from preoccupied.gh_runner_ctl import _run, prune

    monkeypatch.setattr(_run, "as_service_user", lambda argv: argv)
    return prune, _run


def _podman_says(mod, monkeypatch, stdout, rc=0):
    prune, _run = mod

    def spy(cmd, **kw):
        return subprocess.CompletedProcess(cmd, rc, stdout, "")

    monkeypatch.setattr(_run.subprocess, "run", spy)


class TestContainers:
    def test_reaps_stopped_builders(self, mod, monkeypatch):
        prune, _ = mod
        _podman_says(mod, monkeypatch,
                     "buildx_buildkit_builder-abc0\texited\n")
        assert prune.abandoned_buildx_containers() == ["buildx_buildkit_builder-abc0"]

    def test_leaves_running_builders_alone(self, mod, monkeypatch):
        """
        A persistent builder kept for its cache stays up. Reaping it would
        silently destroy the cache it exists to hold.
        """
        prune, _ = mod
        _podman_says(mod, monkeypatch,
                     "buildx_buildkit_gh-runner0\trunning\n")
        assert prune.abandoned_buildx_containers() == []

    def test_ignores_everything_else(self, mod, monkeypatch):
        prune, _ = mod
        _podman_says(mod, monkeypatch,
                     "gh-runner-01\trunning\nsome-job-container\texited\n")
        assert prune.abandoned_buildx_containers() == []

    def test_survives_a_podman_failure(self, mod, monkeypatch):
        prune, _ = mod
        _podman_says(mod, monkeypatch, "", rc=125)
        assert prune.abandoned_buildx_containers() == []


class TestVolumes:
    def test_finds_state_volumes(self, mod, monkeypatch):
        prune, _ = mod
        _podman_says(mod, monkeypatch,
                     "buildx_buildkit_builder-abc0_state\nsomething-else\n")
        assert prune.abandoned_buildx_volumes() == [
            "buildx_buildkit_builder-abc0_state"]


# The end.
