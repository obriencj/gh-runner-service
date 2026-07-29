"""
Instance configuration.

instances.d/ is the only store (design §7). Nothing here caches, and nothing
writes a second copy of anything an operator could have written by hand.

The parser deliberately implements *Podman's* --env-file rules, not systemd's
EnvironmentFile rules. Quadlet routes EnvironmentFile= to `podman --env-file`,
and the two formats are similar enough to be confused and different enough to
bite: Podman does no quote removal, so RUNNER_NAME="build box" produces a name
containing literal quote characters. `show` must report what the container
will actually see, which means matching Podman, warts and all.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import GLOBAL_CONF, INSTANCES_DIR, STATE_ROOT, CtlError

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Keys routed to a systemd drop-in rather than into the container.
#: Quadlet fixes these at generation time, so an EnvironmentFile cannot reach
#: them; without the generated drop-in they silently do nothing.
UNIT_SHAPING: dict[str, str] = {
    "MEMORY_MAX": "MemoryMax",
    "MEMORY_HIGH": "MemoryHigh",
    "CPU_WEIGHT": "CPUWeight",
    "CPU_QUOTA": "CPUQuota",
    "TASKS_MAX": "TasksMax",
    "IO_WEIGHT": "IOWeight",
}

#: Keys read by the shim inside the container and applied to *job* containers.
#: Separate from UNIT_SHAPING because job containers are siblings in their own
#: cgroup scope and inherit nothing from the runner unit — design §7.2.
JOB_SHAPING = frozenset({"JOB_MEMORY_MAX", "JOB_CPUS"})

RUNTIME_KEYS = frozenset(
    {
        "RUNNER_URL",
        "RUNNER_NAME",
        "RUNNER_LABELS",
        "RUNNER_GROUP",
        "RUNNER_WIPE_WORK",
    }
)

_INSTANCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def valid_instance_id(iid: str) -> bool:
    """
    The id is a filename stem, a systemd instance, and a container name
    """

    return bool(_INSTANCE_ID_RE.match(iid))


def parse_env_file(path: Path) -> dict[str, str]:
    """
    Parse one file using Podman's --env-file rules
    """

    values: dict[str, str] = {}
    if not path.exists():
        return values

    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            # Podman treats a bare name as "inherit from the environment".
            # Nothing sensible can inherit into a container started by
            # systemd, so refuse rather than silently dropping it.
            raise CtlError(f"{path}:{lineno}: no '=' in {line!r}")
        key, _, value = line.partition("=")
        key = key.strip()
        if not _KEY_RE.match(key):
            raise CtlError(f"{path}:{lineno}: not a valid key: {key!r}")
        values[key] = value.strip()
    return values


def lint_env_file(path: Path) -> list[str]:
    """
    Warnings for things Podman will accept and misinterpret
    """

    warnings: list[str] = []
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            warnings.append(
                f"{path}:{lineno}: {key.strip()} is quoted. Podman does not strip "
                f"quotes, so the value will include them literally. Drop them."
            )
        if value != raw.partition("=")[2].strip("\n"):
            warnings.append(
                f"{path}:{lineno}: {key.strip()} has leading or trailing "
                f"whitespace in its value."
            )
    return warnings


@dataclass
class Instance:
    """
    One runner, derived entirely from its conf file plus the defaults
    """

    iid: str
    path: Path
    values: dict[str, str] = field(default_factory=dict)

    @property
    def state_dir(self) -> Path:
        return STATE_ROOT / self.iid

    @property
    def drain_marker(self) -> Path:
        return self.state_dir / ".drain"

    @property
    def unit(self) -> str:
        return f"gh-runner@{self.iid}.service"

    @property
    def container_name(self) -> str:
        return f"gh-runner-{self.iid}"

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.values.get(key, default)

    def unit_shaping(self) -> dict[str, str]:
        return {
            UNIT_SHAPING[k]: v for k, v in self.values.items() if k in UNIT_SHAPING
        }

    def classify(self) -> dict[str, dict[str, str]]:
        """
        Split the merged values by where each key actually goes
        """

        out = {"runtime": {}, "job": {}, "unit": {}, "unknown": {}}
        for key, value in sorted(self.values.items()):
            if key in UNIT_SHAPING:
                out["unit"][key] = value
            elif key in JOB_SHAPING:
                out["job"][key] = value
            elif key in RUNTIME_KEYS:
                out["runtime"][key] = value
            else:
                out["unknown"][key] = value
        return out


def instance_path(iid: str) -> Path:
    return INSTANCES_DIR / f"{iid}.conf"


def load(iid: str) -> Instance:
    """
    Global defaults first, instance file wins
    """

    if not valid_instance_id(iid):
        raise CtlError(f"invalid instance id: {iid!r}")
    path = instance_path(iid)
    if not path.exists():
        raise CtlError(f"no such instance: {iid} (expected {path})")

    values = parse_env_file(GLOBAL_CONF)
    values.update(parse_env_file(path))
    values.setdefault("RUNNER_NAME", _default_name(iid))
    return Instance(iid=iid, path=path, values=values)


def _default_name(iid: str) -> str:
    import socket

    return f"{socket.gethostname().split('.')[0]}-{iid}"


def all_instances() -> list[Instance]:
    """
    Every *.conf in instances.d. The sample is .conf.sample and is skipped
    """

    if not INSTANCES_DIR.is_dir():
        return []
    return [load(p.stem) for p in sorted(INSTANCES_DIR.glob("*.conf"))]


SCAFFOLD = """\
# /etc/gh-runner/instances.d/{iid}.conf
#
# Format is Podman --env-file, not systemd EnvironmentFile: no quoting, no
# escapes, no variable expansion. See gh-runner.conf(5).

# --- runtime env: read by entrypoint.sh at each container start
RUNNER_URL={url}
RUNNER_LABELS={labels}
{name_line}
{group_line}

# --- job-shaping: read by the shim, applied to each job container.
#     These are what bound a containerised workflow. MEMORY_MAX below does
#     NOT — job containers are siblings in their own cgroup scope (§7.2).
#JOB_MEMORY_MAX=12G
#JOB_CPUS=4

# --- unit-shaping: routed to a systemd drop-in by `gh-runner-ctl sync`.
#     Bounds the runner process itself, and any job that runs without a
#     container: block.
#MEMORY_MAX=2G
#CPU_WEIGHT=100
"""


def scaffold(
        iid: str,
        url: str,
        labels: str,
        name: str | None = None,
        group: str | None = None) -> str:
    """
    Generate a scaffold conf file for a new instance
    """

    return SCAFFOLD.format(
        iid=iid,
        url=url,
        labels=labels,
        name_line=(f"RUNNER_NAME={name}" if name else "#RUNNER_NAME=  # default: <hostname>-<id>"),
        group_line=(f"RUNNER_GROUP={group}" if group else "#RUNNER_GROUP=default"),
    )


# The end.
