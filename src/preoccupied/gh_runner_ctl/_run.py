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


def podman_json(*args: str) -> list:
    """
    Podman with --format json, always as a list.

    Podman is looser about this than it looks. An empty result may be "",
    "[]", "null", or "{}" depending on the subcommand and version, and it
    sometimes prefixes stdout with a warning line. All of those mean "nothing
    here", not "the tool is broken", so none of them raise.
    """

    proc = podman(*args, "--format", "json")
    out = proc.stdout.strip()

    if not out or out == "null":
        return []

    # Podman occasionally writes a WARN/INFO line to stdout rather than
    # stderr. Find the first *line* that opens a JSON document, not the first
    # bracket character -- "WARN[0000]" has a bracket four characters in, and
    # slicing there produces garbage.
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("[", "{")):
            out = "\n".join(lines[i:])
            break
    else:
        raise CtlError(
            f"`podman {' '.join(args)}` produced no JSON.\n"
            f"stdout: {out[:500]!r}\n"
            f"stderr: {proc.stderr.strip()[:500]!r}"
        )

    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        # Include what podman actually said. Reporting only the parser's
        # complaint hides the one piece of information needed to fix this.
        raise CtlError(
            f"could not parse `podman {' '.join(args)}` output: {exc}\n"
            f"stdout: {out[:500]!r}\n"
            f"stderr: {proc.stderr.strip()[:500]!r}"
        ) from exc

    if data is None:
        return []
    if isinstance(data, dict):
        return [data] if data else []
    if not isinstance(data, list):
        raise CtlError(
            f"`podman {' '.join(args)}` returned {type(data).__name__}, "
            f"expected a list: {out[:200]!r}"
        )
    return data


def daemon_reload() -> None:
    systemctl("daemon-reload")


# The end.
