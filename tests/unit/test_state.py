"""
The instance state directory.

    Error: statfs /var/lib/gh-runner/01: no such file or directory

podman will not create a bind-mount source. Nothing on the host created it:
entrypoint.sh's mkdir runs inside the container, and the only other creation
site was drain.mark(), which made it as root with default permissions --
a directory that exists and that the container cannot write to.
"""

import os
import stat

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_RUNNER_ROOT", str(tmp_path))
    for name in list(os.sys.modules):
        if name.startswith("preoccupied.gh_runner_ctl"):
            del os.sys.modules[name]
    from preoccupied.gh_runner_ctl import _run, conf, drain, state

    conf.INSTANCES_DIR.mkdir(parents=True)
    conf.instance_path("01").write_text("RUNNER_URL=https://github.com/o/r\n")

    # No gh-runner account on a dev box, and chown to a real uid would need
    # root. Own it as ourselves and record that the call was made.
    chowned = []
    monkeypatch.setattr(_run, "service_uid", lambda: os.getuid())
    monkeypatch.setattr(_run, "service_gid", lambda: os.getgid())
    monkeypatch.setattr(state, "service_uid", lambda: os.getuid())
    monkeypatch.setattr(state, "service_gid", lambda: os.getgid())
    monkeypatch.setattr(state, "_restorecon", lambda p: chowned.append(p))
    return conf, drain, state, chowned


class TestEnsure:
    def test_creates_the_directory(self, env):
        conf, _, state, _ = env
        inst = conf.load("01")
        assert not inst.state_dir.exists()
        assert state.ensure(inst) is True
        assert inst.state_dir.is_dir()

    def test_reports_false_when_already_there(self, env):
        conf, _, state, _ = env
        inst = conf.load("01")
        state.ensure(inst)
        assert state.ensure(inst) is False

    def test_mode_is_0700(self, env):
        conf, _, state, _ = env
        inst = conf.load("01")
        state.ensure(inst)
        assert stat.S_IMODE(inst.state_dir.stat().st_mode) == 0o700

    def test_fixes_permissions_on_an_existing_directory(self, env):
        """
        Applied unconditionally, because the bug left behind a directory that
        existed with the wrong ownership and mode.
        """
        conf, _, state, _ = env
        inst = conf.load("01")
        inst.state_dir.mkdir(parents=True)
        os.chmod(inst.state_dir, 0o755)
        state.ensure(inst)
        assert stat.S_IMODE(inst.state_dir.stat().st_mode) == 0o700

    def test_relabels(self, env):
        conf, _, state, calls = env
        state.ensure(conf.load("01"))
        assert calls, "restorecon not called; a new subdir inherits var_lib_t"


class TestDrainGoesThroughIt:
    def test_marking_a_drain_does_not_leave_a_bad_directory(self, env):
        conf, drain, state, _ = env
        inst = conf.load("01")
        drain.mark(inst)
        assert stat.S_IMODE(inst.state_dir.stat().st_mode) == 0o700
        assert drain.is_draining(inst)


class TestRemove:
    def test_removes(self, env):
        conf, _, state, _ = env
        inst = conf.load("01")
        state.ensure(inst)
        assert state.remove(inst) is True
        assert not inst.state_dir.exists()

    def test_absent_is_false(self, env):
        conf, _, state, _ = env
        assert state.remove(conf.load("01")) is False


# The end.
