"""
Telling a healthy restart loop from a crash loop.

    01: active/running restarts=2

An ephemeral runner restarts after every job it completes, so a restart
count on its own is unreadable -- 2 restarts is either two finished jobs or
two failures to start. The runner writes one _diag/Worker_*.log per job, so
comparing the two answers it.
"""

import os

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_RUNNER_ROOT", str(tmp_path))
    for name in list(os.sys.modules):
        if name.startswith("preoccupied.gh_runner_ctl"):
            del os.sys.modules[name]
    from preoccupied.gh_runner_ctl import conf, state

    conf.INSTANCES_DIR.mkdir(parents=True)
    conf.instance_path("01").write_text("RUNNER_URL=https://github.com/o/r\n")
    return conf, state


def _worker_logs(inst, n):
    diag = inst.state_dir / "_diag"
    diag.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (diag / f"Worker_2026073{i}-100000-utc.log").write_text("")


class TestJobsRun:
    def test_none_before_any_job(self, env):
        conf, state = env
        assert state.jobs_run(conf.load("01")) == 0

    def test_counts_worker_logs(self, env):
        conf, state = env
        inst = conf.load("01")
        _worker_logs(inst, 3)
        assert state.jobs_run(inst) == 3

    def test_ignores_runner_session_logs(self, env):
        """
        Runner_*.log is one per listener start, not per job. Counting those
        would just reproduce the restart count and answer nothing.
        """
        conf, state = env
        inst = conf.load("01")
        _worker_logs(inst, 2)
        diag = inst.state_dir / "_diag"
        (diag / "Runner_20260730-100000-utc.log").write_text("")
        (diag / "Runner_20260730-110000-utc.log").write_text("")
        assert state.jobs_run(inst) == 2

    def test_missing_state_dir_is_zero(self, env):
        conf, state = env
        inst = conf.load("01")
        assert not inst.state_dir.exists()
        assert state.jobs_run(inst) == 0


# The end.
