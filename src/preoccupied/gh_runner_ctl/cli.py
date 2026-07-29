"""
gh-runner-ctl — the operator-facing surface.

A thin wrapper that runs systemctl --user and podman as the service account
with the right XDG_RUNTIME_DIR and DBUS_SESSION_BUS_ADDRESS. That awkwardness
is the whole reason this command exists; an operator should never have to
think about it.

instances.d/ is the only store. `add` is a scaffolding convenience —
useradd, not Puppet — so hand-editing a conf and running `sync` is fully
equivalent to going through this tool.
"""

import argparse
import os
import subprocess
import sys

from . import INSTANCES_DIR, __version__, CtlError
from . import conf, doctor, drain, secret, units


def _print_kv(title: str, values: dict[str, str], note: str = "") -> None:
    if not values:
        return
    print(f"  {title}{'  — ' + note if note else ''}")
    for k, v in values.items():
        print(f"    {k}={v}")


def cmd_add(args) -> int:
    if not conf.valid_instance_id(args.id):
        raise CtlError(f"invalid instance id: {args.id!r}")
    path = conf.instance_path(args.id)
    if path.exists() and not args.force:
        raise CtlError(f"{path} exists (use --force to overwrite)")
    INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(
        conf.scaffold(args.id, args.url, args.labels, args.name, args.group)
    )
    print(f"wrote {path}")
    print(f"next: gh-runner-ctl enable {args.id}")
    return 0


def cmd_show(args) -> int:
    inst = conf.load(args.id)
    groups = inst.classify()
    print(f"{inst.iid}  ({inst.path})")
    print(f"  unit           {inst.unit}")
    print(f"  container      {inst.container_name}")
    print(f"  state dir      {inst.state_dir}")
    print(f"  draining       {'yes' if drain.is_draining(inst) else 'no'}")
    print()
    _print_kv("runtime env", groups["runtime"], "next container start")
    _print_kv("job-shaping", groups["job"], "next job, applied by the shim")
    _print_kv("unit-shaping", groups["unit"], "needs sync + daemon-reload")
    _print_kv("unrecognised", groups["unknown"], "passed through to the container")

    for warning in conf.lint_env_file(inst.path):
        print(f"\nwarning: {warning}")
    return 0


def cmd_edit(args) -> int:
    inst_path = conf.instance_path(args.id)
    if not inst_path.exists():
        raise CtlError(f"no such instance: {args.id}")
    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([editor, str(inst_path)], check=False)
    conf.parse_env_file(inst_path)  # validate on save
    for warning in conf.lint_env_file(inst_path):
        print(f"warning: {warning}")
    print("valid. run `gh-runner-ctl sync` to apply unit-shaping changes.")
    return 0


def cmd_rm(args) -> int:
    inst = conf.load(args.id)
    units.disable(inst, now=True)
    inst.path.unlink(missing_ok=True)
    units.prune_orphan_dropins({i.iid for i in conf.all_instances()})
    units.reload()
    if args.purge:
        import shutil

        shutil.rmtree(inst.state_dir, ignore_errors=True)
        print(f"removed {inst.path} and {inst.state_dir}")
    else:
        print(f"removed {inst.path}; state kept at {inst.state_dir}")
        print("use --purge to remove the state directory too")
    return 0


def cmd_enable(args) -> int:
    inst = conf.load(args.id)
    if not inst.get("RUNNER_URL"):
        raise CtlError(f"{inst.path} has no RUNNER_URL")
    # Refuse rather than let the instance enter a restart loop against a
    # missing secret, which surfaces as an unrelated podman error.
    if not secret.exists():
        raise CtlError(
            "no podman secret; run `gh-runner-ctl set-credential` then `sync`"
        )
    drain.clear(inst)
    units.sync_dropin(inst)
    units.reload()
    units.enable(inst, now=args.now)
    print(f"enabled {inst.unit}")
    if args.now:
        print("first start builds the container image: several minutes, needs egress")
    return 0


def cmd_disable(args) -> int:
    targets = conf.all_instances() if args.all else [conf.load(args.id)]
    for inst in targets:
        if args.now:
            units.disable(inst, now=True)
            print(f"{inst.iid}: stopped (any running job was killed)")
        else:
            drain.mark(inst)
            units.disable(inst, now=False)
            print(f"{inst.iid}: draining — will stop at the next job boundary")
    return 0


def cmd_sync(args) -> int:
    changed = []

    if units.sync_quadlet_links():
        changed.append("quadlet symlinks")

    instances = conf.all_instances()
    for inst in instances:
        if units.sync_dropin(inst):
            changed.append(f"drop-in {inst.iid}")

    if units.prune_orphan_dropins({i.iid for i in instances}):
        changed.append("orphan drop-ins")

    try:
        if secret.sync():
            changed.append("podman secret")
    except CtlError as exc:
        print(f"warning: {exc}", file=sys.stderr)

    if changed:
        units.reload()
        print("synced: " + ", ".join(changed))
        print("unit-shaping changes need a restart to take effect")
    else:
        print("already in sync")
    return 0


def cmd_list(args) -> int:
    instances = conf.all_instances()
    if not instances:
        print("no instances configured; see `gh-runner-ctl add --help`")
        return 0

    rows = []
    for inst in instances:
        st = units.state(inst)
        active = st.get("ActiveState", "?")
        enabled = st.get("UnitFileState", "?")
        if drain.is_draining(inst):
            active = "draining"
        pending = "yes" if units.render_dropin(inst) != _current_dropin(inst) else ""
        rows.append(
            (inst.iid, enabled, active, inst.get("RUNNER_URL", "-"), pending)
        )

    head = ("ID", "ENABLED", "STATE", "URL", "DRIFT")
    widths = [max(len(r[i]) for r in (*rows, head)) for i in range(5)]
    print("  ".join(h.ljust(w) for h, w in zip(head, widths)))
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))
    return 0


def _current_dropin(inst) -> str | None:
    p = units.dropin_dir(inst) / units.DROPIN_NAME
    return p.read_text() if p.exists() else None


def cmd_status(args) -> int:
    for inst in conf.all_instances():
        st = units.state(inst)
        print(
            f"{inst.iid}: {st.get('ActiveState', '?')}/{st.get('SubState', '?')} "
            f"restarts={st.get('NRestarts', '0')}"
            + ("  [draining]" if drain.is_draining(inst) else "")
        )
    return 0


def cmd_doctor(args) -> int:
    return doctor.report()


def cmd_set_credential(args) -> int:
    if args.stdin:
        value = sys.stdin.read()
    else:
        import getpass

        value = getpass.getpass("GitHub PAT or App private key path: ")
    secret.write_credential(value)
    secret.sync()
    print(f"credential written and loaded into podman secret")
    return 0


def cmd_check_credential(args) -> int:
    from urllib.error import HTTPError

    urls = {i.get("RUNNER_URL") for i in conf.all_instances()} - {None}
    if not urls:
        raise CtlError("no instances with a RUNNER_URL to check against")
    secret.read_credential()
    print("credential readable; per-URL minting check is a TODO for M3")
    for u in sorted(urls):
        print(f"  would mint against {u}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="gh-runner-ctl")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="scaffold a conf file")
    p.add_argument("id")
    p.add_argument("--url", required=True)
    p.add_argument("--labels", default="alma10,podman")
    p.add_argument("--name")
    p.add_argument("--group")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("show", help="effective config, global + instance merged")
    p.add_argument("id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("edit", help="$EDITOR, validate on save")
    p.add_argument("id")
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("rm", help="disable and remove an instance")
    p.add_argument("id")
    p.add_argument("--purge", action="store_true", help="also remove the state dir")
    p.set_defaults(func=cmd_rm)

    p = sub.add_parser("enable", help="enable and start")
    p.add_argument("id")
    p.add_argument("--now", action="store_true", default=True)
    p.set_defaults(func=cmd_enable)

    p = sub.add_parser("disable", help="drain by default; --now kills the job")
    p.add_argument("id", nargs="?")
    p.add_argument("--all", action="store_true")
    p.add_argument("--now", action="store_true")
    p.set_defaults(func=cmd_disable)

    p = sub.add_parser("sync", help="reconcile derived state to the conf files")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("list", help="conf files vs enabled units, drift marked")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("doctor", help="check the things that fail silently")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("set-credential", help="write the GitHub credential")
    p.add_argument("--stdin", action="store_true", help="read from stdin")
    p.set_defaults(func=cmd_set_credential)

    p = sub.add_parser("check-credential")
    p.set_defaults(func=cmd_check_credential)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CtlError as exc:
        print(f"gh-runner-ctl: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
