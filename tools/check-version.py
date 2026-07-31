#!/usr/bin/python3
"""
Assert the spec and the Python package agree on the version.

The RPM may carry a pre-release marker the Python package does not. `~` sorts
*below* the base version, so 1.1.0~dev < 1.1.0 and an installed development
build upgrades cleanly to the real thing. PEP 440 has no equivalent character
and the Python package has no need of one, so it just says 1.1.0 throughout.

Only the base is compared. `^` — a snapshot *after* a release — is rejected
rather than guessed at, since this project has no use for it.

author: Christopher O'Brien <obriencj@gmail.com>
license: GPLv3
"""

import re
import sys
from pathlib import Path


def rpm_version(spec: Path) -> str:
    for line in spec.read_text().splitlines():
        if line.startswith("Version:"):
            return line.split(None, 1)[1].strip()
    raise SystemExit(f"no Version: in {spec}")


def python_version(init: Path) -> str:
    m = re.search(r'^__version__ = "([^"]+)"', init.read_text(), re.M)
    if not m:
        raise SystemExit(f"no __version__ in {init}")
    return m.group(1)


def main() -> int:
    spec = Path(sys.argv[1] if len(sys.argv) > 1 else "gh-runner.spec")
    init = Path("src/preoccupied/gh_runner_ctl/__init__.py")

    rpm = rpm_version(spec)
    have = python_version(init)

    if "^" in rpm:
        print(f"{rpm}: ^ marks a snapshot after a release, which this project "
              f"does not use. For a pre-release use ~.", file=sys.stderr)
        return 1

    base, _, pre = rpm.partition("~")

    if have != base:
        print("version drift:", file=sys.stderr)
        print(f"  {spec} says  {rpm}", file=sys.stderr)
        print(f"  package says {have}", file=sys.stderr)
        print(f"  expected     {base}", file=sys.stderr)
        return 1

    if pre:
        print(f"version ok: {rpm} (pre-release, sorts below {base})")
    else:
        print(f"version ok: {rpm}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# The end.
