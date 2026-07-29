"""
Subprocess plumbing, and the one piece of awkwardness this package exists to
hide: talking to a rootless service account's systemd and Podman from a root
shell.

An operator should never have to know about XDG_RUNTIME_DIR or the session
bus address. That is the whole job of this module.
"""

import json
import os
import pwd
import shutil
import subprocess
from typing import Any

from . import SERVICE_USER, CtlError

_uid_cache: int | None = None


def service_uid() -> int:
    global _uid_cache
    if _uid_cache is None:
        try:
            _uid_cache = pwd.getpwnam(SERVICE_USER).pw_uid
        except KeyError:
            raise CtlError(
                f"service account {SERVICE_USER!r} does not exist; "
                "is the gh-runner package installed?"
            ) from None
    return _uid_cache


def _user_env() -> dict[str, str]:
    uid = service_uid()
    return {
        "XDG_RUNTIME_DIR": f"/run/user/{uid}",
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus",
    }


def as_service_user(argv: list[str]) -> list[str]:
    """
    Wrap argv so it runs as the service account with a usable session
    """

    if os.geteuid() == service_uid():
        return argv

    if os.geteuid() != 0:
        raise CtlError(
            "must run as root or as the gh-runner service account "
            "(try: sudo gh-runner-ctl ...)"
        )
    env = [f"{k}={v}" for k, v in _user_env().items()]
    runuser = shutil.which("runuser") or "/sbin/runuser"
    return [runuser, "-u", SERVICE_USER, "--", "env", *env, *argv]


def run(argv: list[str],
        *,
        user: bool = True,
        check: bool = True,
        stdin: str | None = None) -> subprocess.CompletedProcess:
    """
    Run a command as the service account with a usable session.

    Args:
        argv: The command to run.
        user: Whether to run as the service account.
        check: Whether to raise an error if the command fails.
        stdin: The stdin to pass to the command.
    """

    cmd = as_service_user(argv) if user else argv

    # cwd matters. runuser drops privileges but inherits the caller's working
    # directory, and an operator invoking this from root's shell leaves it at
    # /root — which the service account cannot enter, so every command dies
    # with "cannot chdir to /root: Permission denied" before it starts. / is
    # the one directory guaranteed traversable by everyone.
    proc = subprocess.run(
        cmd,
        input=stdin,
        capture_output=True,
        text=True,
        cwd="/",
    )
    if check and proc.returncode != 0:
        raise CtlError(
            f"command failed ({proc.returncode}): {' '.join(argv)}\n"
            f"{proc.stderr.strip()}"
        )
    return proc


def systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return run(["systemctl", "--user", *args], check=check)


def podman(*args: str, check: bool = True, stdin: str | None = None):
    return run(["podman", *args], check=check, stdin=stdin)


def podman_json(*args: str) -> Any:
    """
    Podman with --format json. Empty output is an empty list, not a crash
    """

    out = podman(*args, "--format", "json").stdout.strip()
    if not out:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise CtlError(f"could not parse podman output: {exc}") from exc


def daemon_reload() -> None:
    systemctl("daemon-reload")


# The end.
