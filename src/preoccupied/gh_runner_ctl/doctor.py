"""
gh-runner-ctl doctor.

Every check here corresponds to a failure mode from the design's risk table
that presents as something other than its cause: a silently-ignored Quadlet
key, a resource limit that is configured and reported but not enforced, a
missing socket that surfaces as an unrelated Podman error.
"""

from dataclasses import dataclass
from pathlib import Path

from . import CREDENTIALS, QUADLET_SRC, CtlError
from ._run import podman, run, service_uid, systemctl
from . import conf, secret, units


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def _try(name: str, fn) -> Check:
    try:
        ok, detail = fn()
        return Check(name, ok, detail)
    except Exception as exc:  # a broken check must not hide the others
        return Check(name, False, str(exc))


def check_account() -> tuple[bool, str]:
    uid = service_uid()
    return True, f"uid {uid}"


def check_linger() -> tuple[bool, str]:
    p = Path(f"/var/lib/systemd/linger/gh-runner")
    if p.exists():
        return True, ""
    return False, "linger is off; run: loginctl enable-linger gh-runner"


def check_subid() -> tuple[bool, str]:
    for f in ("/etc/subuid", "/etc/subgid"):
        text = Path(f).read_text() if Path(f).exists() else ""
        if not any(line.startswith("gh-runner:") for line in text.splitlines()):
            return False, f"no gh-runner entry in {f}; rootless podman will fail"
    return True, ""


def check_podman_socket() -> tuple[bool, str]:
    uid = service_uid()
    sock = Path(f"/run/user/{uid}/podman/podman.sock")
    if not sock.exists():
        return False, (
            f"{sock} missing. Every instance mounts it. Podman will create a "
            "*directory* at the mount source and the runner will fail with an "
            "unrelated error. Fix: systemctl --user enable --now podman.socket"
        )
    if not sock.is_socket():
        return False, f"{sock} exists but is not a socket — remove it and re-enable"
    return True, str(sock)


def check_podman_version() -> tuple[bool, str]:
    out = podman("version", "--format", "{{.Client.Version}}").stdout.strip()
    return True, f"host podman {out} (image pins podman-remote to match)"


def check_cgroup_delegation() -> tuple[bool, str]:
    uid = service_uid()
    p = Path(f"/sys/fs/cgroup/user.slice/user-{uid}.slice/cgroup.controllers")
    if not p.exists():
        return False, f"{p} missing; is the account lingering?"
    have = set(p.read_text().split())
    missing = {"memory", "cpu"} - have
    if missing:
        return False, (
            f"controllers not delegated: {', '.join(sorted(missing))}. "
            "MEMORY_MAX and CPU_WEIGHT will be configured, reported by "
            "systemctl show, and not enforced."
        )
    return True, " ".join(sorted(have))


def check_credential() -> tuple[bool, str]:
    if not CREDENTIALS.exists() or CREDENTIALS.stat().st_size == 0:
        return False, "no credential; run: gh-runner-ctl set-credential"
    if not secret.exists():
        return False, (
            "credential file present but the podman secret is missing; "
            "run: gh-runner-ctl sync"
        )
    return True, ""


def check_quadlet_dryrun() -> tuple[bool, str]:
    q = Path("/usr/libexec/podman/quadlet")
    if not q.exists():
        return True, "quadlet binary not found, skipped"
    proc = run([str(q), "-dryrun", "-user"], check=False)
    if proc.returncode != 0:
        return False, proc.stderr.strip()[:400]
    if "gh-runner" not in proc.stdout:
        return False, (
            "quadlet -dryrun produced no gh-runner units. Malformed keys fail "
            "silently, and a non-Quadlet file in the Quadlet directory is "
            "ignored outright."
        )
    return True, ""


def check_unit_links() -> tuple[bool, str]:
    qdir = units.quadlet_dir()
    if not qdir.is_dir():
        return False, f"{qdir} missing; run: gh-runner-ctl sync"
    want = {p.name for p in QUADLET_SRC.iterdir() if p.is_file()}
    have = {p.name for p in qdir.iterdir()}
    if want - have:
        return False, f"missing links: {', '.join(sorted(want - have))}"
    dangling = [str(p) for p in qdir.iterdir() if not p.resolve().exists()]
    if dangling:
        return False, f"dangling: {', '.join(dangling)}"
    return True, f"{len(have)} link(s)"


def check_instances() -> tuple[bool, str]:
    instances = conf.all_instances()
    if not instances:
        return True, "no instances configured"

    problems = []
    names: dict[str, str] = {}
    for inst in instances:
        if not inst.get("RUNNER_URL"):
            problems.append(f"{inst.iid}: no RUNNER_URL")
        name = inst.get("RUNNER_NAME") or ""
        if name in names:
            # Two instances sharing a name --replace each other on every job
            # and produce an unwinnable registration fight.
            problems.append(
                f"{inst.iid}: RUNNER_NAME {name!r} collides with {names[name]}"
            )
        names[name] = inst.iid
        problems.extend(conf.lint_env_file(inst.path))

    if problems:
        return False, "; ".join(problems)
    return True, f"{len(instances)} instance(s)"


def check_dropin_z() -> tuple[bool, str]:
    """:Z on the instance volume breaks sibling job containers. See §10."""
    hits = []
    for src in list(QUADLET_SRC.glob("*.container")) + list(
        units.SYSTEMD_USER_DIR.glob("gh-runner@*.service.d/*.conf")
    ):
        for line in src.read_text().splitlines():
            if line.strip().startswith("Volume=") and line.rstrip().endswith(":Z"):
                hits.append(f"{src}: {line.strip()}")
    if hits:
        return False, (
            "found :Z on a volume. It relabels with the runner's private MCS "
            "category, after which sibling job containers cannot read _work. "
            + "; ".join(hits)
        )
    return True, ""


CHECKS = [
    ("service account", check_account),
    ("linger", check_linger),
    ("subuid/subgid", check_subid),
    ("podman.socket", check_podman_socket),
    ("podman version", check_podman_version),
    ("cgroup delegation", check_cgroup_delegation),
    ("credential", check_credential),
    ("quadlet symlinks", check_unit_links),
    ("quadlet -dryrun", check_quadlet_dryrun),
    ("volume labels", check_dropin_z),
    ("instances", check_instances),
]


def run_all() -> list[Check]:
    return [_try(name, fn) for name, fn in CHECKS]


def report() -> int:
    results = run_all()
    width = max(len(c.name) for c in results)
    failed = 0
    for c in results:
        mark = "ok  " if c.ok else "FAIL"
        print(f"[{mark}] {c.name.ljust(width)}  {c.detail}".rstrip())
        if not c.ok:
            failed += 1
    print()
    print(f"{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0
