"""
The credential has one path.

    /etc/gh-runner/credentials  --[ set-credential | sync ]-->  podman secret
            0600, %ghost                                        gh-runner-token

The file is the source of truth; the secret is derived state, consistent with
the instances.d invariant. `podman secret create` cannot update in place, so
sync removes and recreates. A container already running keeps the old value
until its next start, which is the usual job-boundary behaviour and needs no
special handling.
"""

import hashlib
import os
import stat

from . import CREDENTIALS, SECRET_NAME, CtlError
from ._run import podman, podman_json

_LABEL = "io.preoccupied.gh-runner.digest"


def read_credential() -> str:
    if not CREDENTIALS.exists() or CREDENTIALS.stat().st_size == 0:
        raise CtlError(
            f"no credential at {CREDENTIALS}\n"
            "run: gh-runner-ctl set-credential"
        )
    mode = stat.S_IMODE(CREDENTIALS.stat().st_mode)
    if mode & 0o077:
        raise CtlError(
            f"{CREDENTIALS} is mode {mode:04o}; must not be group- or "
            "world-readable. Fix with: chmod 0600 " + str(CREDENTIALS)
        )
    return CREDENTIALS.read_text().strip()


def write_credential(value: str) -> None:
    value = value.strip()
    if not value:
        raise CtlError("refusing to write an empty credential")
    CREDENTIALS.parent.mkdir(parents=True, exist_ok=True)
    # Create with the right mode from the start; never widen then narrow.
    fd = os.open(CREDENTIALS, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(value + "\n")
    os.chmod(CREDENTIALS, 0o600)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def current_digest() -> str | None:
    for entry in podman_json("secret", "ls"):
        if entry.get("Name") != SECRET_NAME:
            continue
        labels = (entry.get("Spec") or {}).get("Labels") or entry.get("Labels") or {}
        return labels.get(_LABEL)
    return None


def exists() -> bool:
    return any(e.get("Name") == SECRET_NAME for e in podman_json("secret", "ls"))


def sync() -> bool:
    """Make the podman secret match the file. True if it changed."""
    value = read_credential()
    want = _digest(value)

    if exists():
        if current_digest() == want:
            return False
        podman("secret", "rm", SECRET_NAME, check=False)

    podman(
        "secret",
        "create",
        "--label",
        f"{_LABEL}={want}",
        SECRET_NAME,
        "-",
        stdin=value,
    )
    return True
