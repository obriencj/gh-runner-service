"""
Graceful stop, design §7.3.

Neither systemd verb does what is wanted: `disable` only affects the next boot
and leaves the restart loop running, while `stop` kills the in-flight job.
That is precisely the distinction `--now` is meant to express, so it needs its
own mechanism.

    ctl disable <id>          touch .drain; the current job finishes, the
                              container exits, and nothing restarts it
    ctl disable <id> --now    systemctl stop; the job dies
    ctl enable <id>           rm .drain; enable --now

entrypoint.sh checks for the marker before registering and exits DRAIN_EXIT.
The unit carries RestartPreventExitStatus=78, so Restart=always declines.
"""

from .conf import Instance


def is_draining(inst: Instance) -> bool:
    return inst.drain_marker.exists()


def mark(inst: Instance) -> bool:
    """
    Ask the instance to stop at its next job boundary
    """

    if is_draining(inst):
        return False
    inst.state_dir.mkdir(parents=True, exist_ok=True)
    inst.drain_marker.write_text(
        "Created by gh-runner-ctl disable.\n"
        "Remove this file, or run `gh-runner-ctl enable`, to resume.\n"
    )
    return True


def clear(inst: Instance) -> bool:
    if not is_draining(inst):
        return False
    inst.drain_marker.unlink()
    return True


# The end.
