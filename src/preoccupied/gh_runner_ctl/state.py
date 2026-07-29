"""
The per-instance state directory.

/var/lib/gh-runner/<id> must exist on the host, owned by the service account,
before the container starts. Podman will not invent a bind-mount source:

    Error: statfs /var/lib/gh-runner/01: no such file or directory

and entrypoint.sh's mkdir runs *inside* the container, which is far too late.
It also cannot simply be created: the identical-path invariant (design §5)
means job containers reach the same paths as siblings, so the directory needs
the service account's ownership and the container_file_t label the package's
%post fcontext rule describes. A root-owned, var_lib_t directory exists and
still fails, one layer further in.

author: Christopher O'Brien <obriencj@gmail.com>
license: GPLv3
"""

import os
import shutil

from ._run import run, service_gid, service_uid
from .conf import Instance


def _restorecon(path) -> None:
    """
    Apply the fcontext rule %post installed.

    A newly created subdirectory inherits its parent's label rather than
    matching the rule, so this is not optional on an SELinux host. Best
    effort: on a host without the tooling there is nothing to label.
    """

    tool = shutil.which("restorecon")
    if tool:
        run([tool, "-R", str(path)], user=False, check=False)


def ensure(inst: Instance) -> bool:
    """
    Create the state directory if absent. True if it was created.
    """

    d = inst.state_dir
    existed = d.is_dir()

    d.mkdir(parents=True, exist_ok=True)

    # Applied unconditionally, not just on creation. An earlier version had
    # drain.mark() create this as root with default permissions, which leaves
    # a directory that exists and that the container cannot write to.
    os.chown(d, service_uid(), service_gid())
    os.chmod(d, 0o700)
    _restorecon(d)

    return not existed


def remove(inst: Instance) -> bool:
    d = inst.state_dir
    if not d.exists():
        return False
    shutil.rmtree(d, ignore_errors=True)
    return True


# The end.
