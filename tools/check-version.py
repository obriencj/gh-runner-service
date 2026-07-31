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


def rpm_version(spec: Path) -> tuple[str, str]:
    """
    (base, qualifier) read from the %global lines.

    Not from `Version:` — that is `%{base_version}%{?version_qualifier}` and
    reading it without expanding macros gets the literal text.
    """

    base = qualifier = ""
    for line in spec.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "%global":
            if parts[1] == "base_version":
                base = parts[2]
            elif parts[1] == "version_qualifier":
                # %{nil} is how you null the macro without removing the line.
                # Either spelling means "this is a release".
                qualifier = "" if parts[2] == "%{nil}" else parts[2]
    if not base:
        raise SystemExit(f"no %global base_version in {spec}")
    return base, qualifier


def python_version(init: Path) -> str:
    m = re.search(r'^__version__ = "([^"]+)"', init.read_text(), re.M)
    if not m:
        raise SystemExit(f"no __version__ in {init}")
    return m.group(1)


def main() -> int:
    spec = Path(sys.argv[1] if len(sys.argv) > 1 else "gh-runner.spec")
    init = Path("src/preoccupied/gh_runner_ctl/__init__.py")

    base, qualifier = rpm_version(spec)
    have = python_version(init)

    if qualifier.startswith("^"):
        print(f"{qualifier}: ^ marks a snapshot after a release, which this "
              f"project does not use. For a pre-release use ~.", file=sys.stderr)
        return 1

    if qualifier and not qualifier.startswith("~"):
        print(f"version_qualifier {qualifier!r} does not start with ~, so it "
              f"would sort ABOVE {base} rather than below it.", file=sys.stderr)
        return 1

    if have != base:
        print("version drift:", file=sys.stderr)
        print(f"  {spec} base_version  {base}", file=sys.stderr)
        print(f"  package __version__  {have}", file=sys.stderr)
        print(f"run: make bump-version V={base}", file=sys.stderr)
        return 1

    if qualifier:
        print(f"version ok: {base}{qualifier} "
              f"(pre-release, sorts below {base}; paths use {base})")
    else:
        print(f"version ok: {base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# The end.
