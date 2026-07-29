"""
Control commands for the gh-runner service.

Host-side only. Everything that runs *inside* the runner container is POSIX
shell under container/context/, so the image never acquires a Python runtime
it would only need for three scripts.

author: Christopher O'Brien <obriencj@gmail.com>
license: GPLv3
"""

import os
from pathlib import Path


__version__ = "0.1.0"

SERVICE_USER = "gh-runner"

#: Credentials are per instance, so the secret name is too. Each worker
#: registers against its own repo or org and needs its own token.
SECRET_PREFIX = "gh-runner-token"

#: entrypoint.sh exits with this when it finds a drain marker;
#: the unit carries RestartPreventExitStatus=78. See design §7.3.
DRAIN_EXIT = 78

#: Test hook. Every path below is derived from this, so the whole module can
#: be pointed at a staging tree without touching a real host.
_ROOT = Path(os.environ.get("GH_RUNNER_ROOT", "/"))


def _p(*parts: str) -> Path:
    return _ROOT.joinpath(*parts)


CONF_DIR = _p("etc/gh-runner")
GLOBAL_CONF = CONF_DIR / "gh-runner.conf"
INSTANCES_DIR = CONF_DIR / "instances.d"
#: One file per instance, named for the instance id. 0700 on the directory:
#: only root reads these, and only to hand them to `podman secret create`.
CREDENTIALS_DIR = CONF_DIR / "credentials.d"

STATE_ROOT = _p("var/lib/gh-runner")
RUNNER_TEMPLATE = _p("usr/lib/gh-runner/current")

QUADLET_SRC = _p("usr/share/gh-runner/quadlet")
SYSTEMD_USER_DIR = _p("etc/systemd/user")
QUADLET_USER_DIR = _p("etc/containers/systemd/users")


class CtlError(Exception):
    """
    Operator-facing failure. cli.main prints these without a traceback
    """


# The end.
