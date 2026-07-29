"""
Enabling a Quadlet template instance.

    # gh-runner-ctl enable 01 --now
    command failed (1): systemctl --user enable gh-runner@01.service
    Failed to enable unit: Unit /run/user/987/systemd/generator/
    gh-runner@.service is transient or generated

systemd refuses to enable generated units. Quadlet's own answer is the
[Install] section of the .container file, which a template cannot use per
instance. So we write the .wants symlink ourselves, in exactly the form
systemctl enable uses for any template unit.
"""

import os
from unittest import mock

import pytest


FRAGMENT = "/run/user/987/systemd/generator/gh-runner@.service"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_RUNNER_ROOT", str(tmp_path))
    for name in list(os.sys.modules):
        if name.startswith("preoccupied.gh_runner_ctl"):
            del os.sys.modules[name]
    from preoccupied.gh_runner_ctl import conf, units

    conf.INSTANCES_DIR.mkdir(parents=True)
    units.SYSTEMD_USER_DIR.mkdir(parents=True)
    conf.instance_path("01").write_text("RUNNER_URL=https://github.com/o/r\n")

    monkeypatch.setattr(units, "daemon_reload", lambda: None)
    monkeypatch.setattr(
        units, "systemctl",
        lambda *a, **k: mock.Mock(stdout=f"FragmentPath={FRAGMENT}\n", returncode=0),
    )
    return conf, units


class TestEnable:
    def test_never_calls_systemctl_enable(self, env, monkeypatch):
        """The whole point: systemctl enable cannot do this."""
        conf, units = env
        calls = []
        monkeypatch.setattr(
            units, "systemctl",
            lambda *a, **k: (calls.append(a),
                             mock.Mock(stdout=f"FragmentPath={FRAGMENT}\n",
                                       returncode=0))[1],
        )
        units.enable(conf.load("01"), now=False)
        assert not any("enable" in c for c in calls)

    def test_link_is_named_for_the_instance(self, env):
        conf, units = env
        inst = conf.load("01")
        units.enable(inst, now=False)
        link = units.SYSTEMD_USER_DIR / "default.target.wants" / "gh-runner@01.service"
        assert link.is_symlink()

    def test_link_targets_the_template_fragment(self, env):
        conf, units = env
        inst = conf.load("01")
        units.enable(inst, now=False)
        link = units.SYSTEMD_USER_DIR / "default.target.wants" / inst.unit
        assert str(link.readlink()) == FRAGMENT

    def test_idempotent(self, env):
        conf, units = env
        inst = conf.load("01")
        units.enable(inst, now=False)
        units.enable(inst, now=False)
        assert units.is_enabled(inst)

    def test_refuses_when_systemd_does_not_know_the_unit(self, env, monkeypatch):
        conf, units = env
        from preoccupied.gh_runner_ctl import CtlError

        monkeypatch.setattr(
            units, "systemctl",
            lambda *a, **k: mock.Mock(stdout="FragmentPath=\n", returncode=0),
        )
        with pytest.raises(CtlError, match="Quadlet has not generated"):
            units.enable(conf.load("01"), now=False)


class TestDisable:
    def test_removes_the_link(self, env):
        conf, units = env
        inst = conf.load("01")
        units.enable(inst, now=False)
        units.disable(inst)
        assert not units.is_enabled(inst)

    def test_tolerates_not_enabled(self, env):
        conf, units = env
        units.disable(conf.load("01"))


# The end.
