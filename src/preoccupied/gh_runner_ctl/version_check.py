"""
gh-runner-version-check — watch the upstream version floor.

--disableupdate means the runner will never self-update, and GitHub eventually
refuses registration from runners below a floor it moves without notice. The
failure presents as every instance simultaneously failing to register, which
is a confusing outage if nothing has been watching. This turns it into a
journal warning weeks earlier.

Reports only. It never updates anything — that is `make upgrade-runner` and a
new RPM, which is a human decision.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

from . import RUNNER_TEMPLATE

RELEASES_URL = "https://api.github.com/repos/actions/runner/releases"
DEFAULT_WARN_RELEASES = 2


def installed_version() -> str | None:
    marker = RUNNER_TEMPLATE / ".version"
    if not marker.exists():
        return None
    return marker.read_text().strip() or None


def fetch_releases(limit: int = 30) -> list[str]:
    req = urllib.request.Request(
        f"{RELEASES_URL}?per_page={limit}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "gh-runner-version-check",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    return [
        r["tag_name"].lstrip("v")
        for r in data
        if not r.get("prerelease") and not r.get("draft")
    ]


def as_tuple(version: str) -> tuple[int, ...]:
    """
    Compare numerically, not lexically: 2.9.0 must sort below 2.10.0.

    Only *leading* digits of each component count, so a suffix like
    "2.328.0-rc1" reads as (2, 328, 0) rather than picking up the 1 from the
    suffix and claiming to be newer than the release it precedes.
    """
    parts = []
    for chunk in version.lstrip("v").split("."):
        m = re.match(r"\d+", chunk)
        parts.append(int(m.group()) if m else 0)
    return tuple(parts)


def releases_behind(current: str, releases: list[str]) -> int:
    """How many published releases are newer than the installed one."""
    cur = as_tuple(current)
    return sum(1 for r in releases if as_tuple(r) > cur)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gh-runner-version-check",
        description="Warn when the pinned runner falls behind upstream.",
    )
    ap.add_argument(
        "--current",
        help="version to check (default: read /usr/lib/gh-runner/current/.version)",
    )
    ap.add_argument("--warn-releases", type=int, default=DEFAULT_WARN_RELEASES)
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)

    current = args.current or installed_version()
    if not current:
        print("version-check: no installed runner found", file=sys.stderr)
        return 2

    try:
        releases = fetch_releases()
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        # A transient network failure must not turn a daily timer into a
        # daily failed unit.
        print(f"version-check: could not reach GitHub: {exc}", file=sys.stderr)
        return 0

    if not releases:
        return 0

    behind = releases_behind(current, releases)
    latest = releases[0]

    if behind >= args.warn_releases:
        print(
            f"WARNING: runner {current} is {behind} release(s) behind "
            f"(latest {latest}). GitHub will eventually refuse registration "
            f"from runners this far back. Ship an updated gh-runner package: "
            f"make upgrade-runner V={latest}",
            file=sys.stderr,
        )
        return 1

    if not args.quiet:
        print(f"runner {current} is current enough (latest {latest}, {behind} behind)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
