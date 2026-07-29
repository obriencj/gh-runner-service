"""
gh-runner-prune — reap job leftovers.

Age-based, not idle-gated. An earlier form of this no-op'd whenever any
Runner.Worker process existed; on a host with several instances and steady
traffic there is essentially always one, so the pruner would never run,
failing exactly under the load that makes it necessary.

Positive selection, not exclusion. Reaping "everything that is not
role=runner" would also reap anything else the service account happens to own,
and would race a job container between create and start. We touch only objects
carrying the label the shim stamps on creation, and the age floor covers the
creation race.
"""

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import STATE_ROOT, CtlError
from ._run import podman, podman_json

JOB_LABEL = "io.preoccupied.gh-runner.role=job"

DEFAULT_MAX_AGE = "2h"
DEFAULT_DIAG_AGE = "14d"

_DURATION = re.compile(r"^(\d+)([smhdw])$")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(text: str) -> int:
    m = _DURATION.match(text.strip())
    if not m:
        raise CtlError(f"not a duration: {text!r} (try 30m, 2h, 14d)")
    return int(m.group(1)) * _UNITS[m.group(2)]


def _age_seconds(stamp: str) -> float | None:
    """Podman emits RFC3339 with a variable-width fractional part."""
    if not stamp or stamp.startswith("0001-01-01"):
        return None
    cleaned = re.sub(r"\.(\d{6})\d+", r".\1", stamp).replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds()


def stale_containers(max_age: int) -> list[str]:
    out = []
    for c in podman_json("ps", "-a", "--filter", f"label={JOB_LABEL}"):
        if (c.get("State") or "").lower() in ("running", "created", "paused"):
            continue
        age = _age_seconds(c.get("Created") or c.get("CreatedAt") or "")
        if age is not None and age >= max_age:
            out.extend(c.get("Names") or [c.get("Id", "")])
    return [n for n in out if n]


def stale_networks(max_age: int) -> list[str]:
    out = []
    for n in podman_json("network", "ls", "--filter", f"label={JOB_LABEL}"):
        name = n.get("Name") or n.get("name")
        if not name or name == "podman":
            continue
        age = _age_seconds(n.get("Created") or n.get("created") or "")
        if age is None or age >= max_age:
            out.append(name)
    return out


def stale_diag(max_age: int) -> list[Path]:
    cutoff = time.time() - max_age
    out = []
    if not STATE_ROOT.is_dir():
        return out
    for diag in STATE_ROOT.glob("*/_diag"):
        for f in diag.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff:
                out.append(f)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gh-runner-prune",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Reap gh-runner job containers, networks and diagnostics by age.\n\n"
            "Normally run by gh-runner-prune.timer every 30 minutes."
        ),
        epilog="""\
why age-based, not idle-gated:
  An earlier form of this no-op'd whenever a Runner.Worker process existed.
  On a host with several instances and steady traffic there is essentially
  always one, so the pruner would never run -- failing exactly under the
  load that makes it necessary. Age is per-object and composes with
  concurrent jobs: a container stopped two hours ago is garbage regardless
  of what else is running.

why positive selection:
  Reaping "everything that is not role=runner" would also reap anything
  else the service account owns, and would race a job container between
  create and start. Only objects labelled
  io.preoccupied.gh-runner.role=job -- stamped by the docker shim at
  creation -- are touched, and the age floor covers the creation race.

durations:
  30s, 30m, 2h, 14d, 1w
""",
    )
    ap.add_argument(
        "--max-age",
        default=DEFAULT_MAX_AGE,
        metavar="DUR",
        help=f"containers and networks stopped longer than this (default {DEFAULT_MAX_AGE})",
    )
    ap.add_argument(
        "--diag-age",
        default=DEFAULT_DIAG_AGE,
        metavar="DUR",
        help=f"_diag files older than this (default {DEFAULT_DIAG_AGE})",
    )
    ap.add_argument("--images", action="store_true", help="also prune dangling images")
    ap.add_argument(
        "-n", "--dry-run", action="store_true", help="report without removing anything"
    )
    args = ap.parse_args(argv)

    try:
        max_age = parse_duration(args.max_age)
        diag_age = parse_duration(args.diag_age)

        containers = stale_containers(max_age)
        networks = stale_networks(max_age)
        diags = stale_diag(diag_age)

        for name in containers:
            print(f"container {name}")
            if not args.dry_run:
                podman("rm", "-f", name, check=False)

        for name in networks:
            print(f"network   {name}")
            if not args.dry_run:
                podman("network", "rm", "-f", name, check=False)

        for path in diags:
            print(f"diag      {path}")
            if not args.dry_run:
                path.unlink(missing_ok=True)

        if args.images and not args.dry_run:
            podman("image", "prune", "-f", check=False)

        total = len(containers) + len(networks) + len(diags)
        print(f"{'would reap' if args.dry_run else 'reaped'} {total} object(s)")

    except CtlError as exc:
        print(f"gh-runner-prune: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
