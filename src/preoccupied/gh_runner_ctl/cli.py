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


def _read_token(iid: str, from_stdin: bool) -> str:
    """
    Never from argv. A PAT on a command line lands in shell history and in
    every `ps` on the box for the duration of the call.
    """

    if from_stdin:
        return sys.stdin.read()

    import getpass

    print(f"Credential for instance {iid}.")
    print("A fine-grained PAT with administration:write on the target, or an")
    print("App private key. Not a registration token — those expire in an hour")
    print("and an ephemeral runner re-registers on every job.")
    return getpass.getpass("token: ")


def cmd_add(args) -> int:
    if not conf.valid_instance_id(args.id):
        raise CtlError(f"invalid instance id: {args.id!r}")

    path = conf.instance_path(args.id)
    if path.exists() and not args.force:
        raise CtlError(f"{path} exists (use --force to overwrite)")

    # The credential first. Scaffolding a worker that cannot possibly register
    # and reporting success is how the previous version sent people to `enable`
    # only to be told about a command they had never heard of.
    if not args.no_token:
        secret.write_credential(args.id, _read_token(args.id, args.token_stdin))

    INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(
        conf.scaffold(args.id, args.url, args.labels, args.name, args.group)
    )
    print(f"wrote {path}")

    if args.no_token:
        print(f"no credential set. before enabling:")
        print(f"  gh-runner-ctl set-credential {args.id}")
    else:
        secret.sync(args.id)
        print(f"wrote {secret.credential_path(args.id)} (0600)")
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
    cred = "present" if secret.has_credential(inst.iid) else "MISSING"
    sec = "loaded" if secret.secret_exists(inst.iid) else "not loaded"
    print(f"  credential     {cred} ({inst.credential_path}), secret {sec}")
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
    if secret.remove(inst.iid):
        print(f"removed credential and secret for {inst.iid}")
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
    if not secret.has_credential(inst.iid):
        raise CtlError(
            f"no credential for {inst.iid}\n"
            f"run: gh-runner-ctl set-credential {inst.iid}"
        )
    if not secret.secret_exists(inst.iid):
        secret.sync(inst.iid)
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

    for inst in instances:
        try:
            if secret.sync(inst.iid):
                changed.append(f"secret {inst.iid}")
        except CtlError as exc:
            print(f"warning: {exc}", file=sys.stderr)

    known = {i.iid for i in instances}
    for name in secret.prune_orphan_secrets(known):
        changed.append(f"orphan secret {name}")

    # Reported, never deleted. Removing a token because a conf file went
    # missing is not a recoverable mistake.
    for name in secret.orphan_credentials(known):
        print(f"note: credential {name} has no instance", file=sys.stderr)

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
        enabled = "yes" if units.is_enabled(inst) else "no"
        if drain.is_draining(inst):
            active = "draining"
        pending = "yes" if units.render_dropin(inst) != _current_dropin(inst) else ""
        cred = "yes" if secret.has_credential(inst.iid) else "NO"
        rows.append(
            (inst.iid, enabled, active, cred, inst.get("RUNNER_URL", "-"), pending)
        )

    head = ("ID", "ENABLED", "STATE", "CRED", "URL", "DRIFT")
    widths = [max(len(r[i]) for r in (*rows, head)) for i in range(6)]
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
    # Validate the instance id even when no conf file exists yet, so a typo
    # cannot quietly create a credential nothing will ever read.
    if not conf.valid_instance_id(args.id):
        raise CtlError(f"invalid instance id: {args.id!r}")
    if not conf.instance_path(args.id).exists():
        print(f"warning: no conf file for {args.id} yet "
              f"({conf.instance_path(args.id)})", file=sys.stderr)

    secret.write_credential(args.id, _read_token(args.id, args.stdin))
    secret.sync(args.id)
    print(f"wrote {secret.credential_path(args.id)} (0600)")
    print(f"loaded podman secret {secret.secret_name(args.id)}")
    return 0


def cmd_check_credential(args) -> int:
    targets = [conf.load(args.id)] if args.id else conf.all_instances()
    if not targets:
        raise CtlError("no instances configured")

    failed = 0
    for inst in targets:
        url = inst.get("RUNNER_URL") or "-"
        if not secret.has_credential(inst.iid):
            print(f"{inst.iid}: MISSING credential ({inst.credential_path})")
            failed += 1
            continue
        try:
            secret.read_credential(inst.iid)
        except CtlError as exc:
            print(f"{inst.iid}: UNREADABLE — {exc}")
            failed += 1
            continue
        state = "ok" if secret.secret_exists(inst.iid) else "no podman secret (run sync)"
        print(f"{inst.iid}: credential present, {state}  -> {url}")
        if not secret.secret_exists(inst.iid):
            failed += 1

    # Actually minting a registration token per URL is the real check and
    # belongs here; it needs the same repo/org inference register.sh does.
    print()
    print("note: this verifies presence and readability, not that the token")
    print("      can mint for its URL. That check lands with M3.")
    return 1 if failed else 0


MAIN_EPILOG = """\
files:
  /etc/gh-runner/gh-runner.conf              global defaults
  /etc/gh-runner/instances.d/<id>.conf       per-instance; the only store
  /etc/gh-runner/credentials.d/<id>          per-worker GitHub token, 0600
  /var/lib/gh-runner/<id>/                   instance state, _work, _diag

security:
  This is not a security boundary. The runner container holds the host
  Podman socket, so anything running inside it can drive every container
  on the box. The VM is the boundary -- do not attach these runners to
  public repositories. All instances share one uid and one Podman store.

see also:
  gh-runner-ctl keys          what each config key does and where it goes
  gh-runner-ctl <cmd> --help  per-command detail
  gh-runner-ctl doctor        check the things that fail silently
"""

ADD_EPILOG = """\
Prompts for this worker's GitHub credential, writes it to
/etc/gh-runner/credentials.d/<id> at mode 0600, then writes
/etc/gh-runner/instances.d/<id>.conf and stops.

The credential is per worker, not per host. Each one registers against its own
repository or organisation under its own name, so a single shared token would
make two instances pointed at different orgs impossible -- which is the whole
reason instances exist. It is never accepted on the command line: a PAT in argv
lands in shell history and in every `ps` for the duration of the call. Use
--token-stdin for automation.

Activation is still a separate step, so configuration management and
activation stay separable.

    gh-runner-ctl enable <id>

The <id> is used consistently as the systemd instance (gh-runner@<id>.service),
the container name (gh-runner-<id>), the state directory
(/var/lib/gh-runner/<id>/), and the default runner name (<hostname>-<id>).

This is a scaffolding convenience -- useradd, not Puppet. Hand-writing the
conf file and running `sync` is fully equivalent.

Run `gh-runner-ctl keys` for what you can put in the file.
"""

DISABLE_EPILOG = """\
Draining is the default, and it is not what either systemd verb does:
`disable` alone only affects the next boot and leaves the restart loop
running, while `stop` kills the job that is currently executing.

  gh-runner-ctl disable <id>          write the drain marker. The running
                                      job finishes, the container exits,
                                      and nothing restarts it.
  gh-runner-ctl disable <id> --now    stop immediately. Any running job dies.

The marker is /var/lib/gh-runner/<id>/.drain. The container entrypoint checks
for it before registering and exits 78; the unit carries
RestartPreventExitStatus=78, so Restart=always declines and the instance
settles at a job boundary with nothing half-finished.

`list` reports such an instance as "draining", since enabled-but-not-running
on purpose is otherwise indistinguishable from a crash loop that gave up.
"""

SYNC_EPILOG = """\
Reconciles every piece of derived state to the conf files: Quadlet symlinks,
unit drop-ins, orphaned drop-ins, and one Podman secret per instance
credential. Idempotent.

Secrets for instances that no longer have a conf file are removed. Credential
*files* with no instance are only reported -- deleting a token because a conf
file went missing is not a recoverable mistake.

instances.d/ is the only store. There is no database, no cache, and no
registry file -- the drop-ins and the secret are regenerated here rather than
trusted, so a hand-edited conf becomes real on the next sync. Sync means sync.

This is the integration point for configuration management: template
instances.d/, call sync, done. One idempotent command, and no unit-name
knowledge outside the package.

Unit-shaping changes still need a restart to take effect.
"""

DOCTOR_EPILOG = """\
Checks the service account, linger, subuid/subgid, podman.socket, host Podman
version against the image client, cgroup controller delegation, the credential
and its secret, Quadlet symlinks, quadlet -dryrun, stray :Z volume options,
and per-instance config including duplicate RUNNER_NAME.

Every one of these corresponds to a failure that presents as something other
than its cause. Malformed Quadlet keys fail silently; a non-Quadlet file in
the Quadlet directory is ignored outright; a missing podman.socket surfaces as
an unrelated Podman error; an undelegated cgroup controller yields a limit
that is configured, reported by `systemctl show`, and not enforced.
"""

CRED_EPILOG = """\
Writes /etc/gh-runner/credentials.d/<id> (mode 0600) and loads it into the
Podman secret that instance's container mounts, gh-runner-token-<id>.

One credential per worker. Use this to set a token for an instance scaffolded
with `add --no-token`, or to rotate one -- the running container keeps the old
value until its next job boundary, which needs no special handling.

What to supply: a fine-grained PAT with administration:write on the target, or
a GitHub App private key. Never a classic `repo` PAT, and not a registration
token from the "add runner" page -- those expire in an hour, and an ephemeral
runner re-registers on every single job, so it mints its own from this.

Ephemeral registration means a live credential sits on the box. Anything
running a job can reach the Podman socket, so scope it to the single target.
"""


def cmd_keys(args) -> int:
    print("Config keys for /etc/gh-runner/instances.d/<id>.conf")
    print("(global defaults in /etc/gh-runner/gh-runner.conf; instance wins)")
    print()
    print("FORMAT")
    print("  KEY=value, one per line, # for comments.")
    print("  This is Podman's --env-file format, NOT systemd's EnvironmentFile.")
    print("  No quote removal, no escapes, no expansion. RUNNER_NAME=\"build box\"")
    print("  gives a name that literally contains the quote characters.")
    print("  Leave values unquoted. `show` warns when you don't.")
    print()
    print("RUNTIME ENV -- into the container, read by its entrypoint")
    print("  effect: next container start, i.e. next job")
    print("    RUNNER_URL          repository or organisation URL (required)")
    print("    RUNNER_LABELS       comma-separated labels")
    print("    RUNNER_NAME         default <hostname>-<id>; must be unique per host")
    print("    RUNNER_GROUP        runner group, optional")
    print("    RUNNER_WIPE_WORK    1 to also drop _work/_actions and _work/_tool")
    print("                        between jobs. Default 0 keeps the toolcache.")
    print()
    print("JOB-SHAPING -- read by the docker shim, applied to each job container")
    print("  effect: next job")
    for k in sorted(conf.JOB_SHAPING):
        print(f"    {k}")
    print()
    print("UNIT-SHAPING -- generated into a systemd drop-in by `sync`")
    print("  effect: needs daemon-reload plus a restart")
    for k, prop in sorted(conf.UNIT_SHAPING.items()):
        print(f"    {k:<16}  -> {prop}=")
    print()
    print("THE TRAP")
    print("  MEMORY_MAX constrains the runner UNIT, which holds only the listener")
    print("  process. Job containers are created by the host engine as siblings")
    print("  and land in their own cgroup scope, inheriting nothing from it. So")
    print("  MEMORY_MAX=12G limits the wrong process and a compiler in a")
    print("  `container:` job still eats the host -- and the value looks applied,")
    print("  because `systemctl show` reports it faithfully.")
    print()
    print("  Use JOB_MEMORY_MAX to bound containerised jobs, and MEMORY_MAX to")
    print("  bound the runner itself plus any job that runs WITHOUT a")
    print("  `container:` block. Both are real; they cover different job shapes.")
    print("  Set both. Enforcement also needs cgroup delegation -- `doctor`")
    print("  checks it, because otherwise the limit is silently not enforced.")
    print()
    print("EPHEMERALITY")
    print("  The container is destroyed after each job, but the instance state")
    print("  directory is a persistent host bind mount and cannot be otherwise:")
    print("  the host engine resolves the source side of every volume the runner")
    print("  emits, so those paths must exist identically on both sides.")
    print()
    print("  Per job, _work/<repo> and _work/_temp are removed; _work/_actions")
    print("  and _work/_tool are kept, because rebuilding the toolcache every job")
    print("  is most of the cold-start cost this package exists to avoid.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    fmt = argparse.RawDescriptionHelpFormatter

    ap = argparse.ArgumentParser(
        prog="gh-runner-ctl",
        formatter_class=fmt,
        description=(
            "Manage rootless, ephemeral GitHub Actions runners.\n\n"
            "A thin wrapper that runs systemctl --user and podman as the "
            "gh-runner service account with the right XDG_RUNTIME_DIR and "
            "DBUS_SESSION_BUS_ADDRESS. Hiding that awkwardness is the whole "
            "point; you should never have to think about it.\n\n"
            "Run as root, or as the service account itself."
        ),
        epilog=MAIN_EPILOG,
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="<command>")

    p = sub.add_parser(
        "add",
        help="scaffold a conf file",
        description="Scaffold an instance config file.",
        epilog=ADD_EPILOG,
        formatter_class=fmt,
    )
    p.add_argument("id", help="instance id; becomes the filename stem")
    p.add_argument("--url", required=True, help="repository or organisation URL")
    p.add_argument("--labels", default="alma10,podman", help="comma-separated")
    p.add_argument("--name", help="runner name (default <hostname>-<id>)")
    p.add_argument("--token-stdin", action="store_true",
                   help="read the credential from stdin instead of prompting")
    p.add_argument("--no-token", action="store_true",
                   help="scaffold without a credential; set it before enabling")
    p.add_argument("--group", help="runner group")
    p.add_argument("--force", action="store_true", help="overwrite an existing file")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser(
        "show",
        help="effective config, global + instance merged",
        description=(
            "Print the effective config with global and instance files merged, "
            "grouped by where each key actually goes, plus warnings for values "
            "Podman will accept and misinterpret.\n\n"
            "Earns its place because of the merge: 'why is this runner picking "
            "up that label' is otherwise a manual diff against gh-runner.conf."
        ),
        formatter_class=fmt,
    )
    p.add_argument("id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser(
        "edit",
        help="$EDITOR, validate on save",
        description="Open the instance conf in $EDITOR and validate on save.",
        formatter_class=fmt,
    )
    p.add_argument("id")
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser(
        "rm",
        help="disable and remove an instance",
        description=(
            "Stop the instance and remove its conf file. State under "
            "/var/lib/gh-runner/<id> is kept unless --purge is given, since it "
            "holds the _diag logs you will want if this is a removal after a "
            "failure."
        ),
        formatter_class=fmt,
    )
    p.add_argument("id")
    p.add_argument("--purge", action="store_true", help="also remove the state dir")
    p.set_defaults(func=cmd_rm)

    p = sub.add_parser(
        "enable",
        help="enable and start",
        description=(
            "Clear any drain marker, regenerate the drop-in, enable and start.\n\n"
            "Refuses when there is no credential, rather than letting the "
            "instance enter a restart loop against a missing secret -- which "
            "surfaces as an unrelated Podman error."
        ),
        formatter_class=fmt,
    )
    p.add_argument("id")
    p.add_argument("--now", action="store_true", default=True, help="start now (default)")
    p.set_defaults(func=cmd_enable)

    p = sub.add_parser(
        "disable",
        help="drain by default; --now kills the running job",
        description="Stop an instance, gracefully by default.",
        epilog=DISABLE_EPILOG,
        formatter_class=fmt,
    )
    p.add_argument("id", nargs="?")
    p.add_argument("--all", action="store_true", help="every configured instance")
    p.add_argument("--now", action="store_true", help="stop now, killing any job")
    p.set_defaults(func=cmd_disable)

    p = sub.add_parser(
        "sync",
        help="reconcile derived state to the conf files",
        description="Reconcile units, drop-ins and the Podman secret to the conf files.",
        epilog=SYNC_EPILOG,
        formatter_class=fmt,
    )
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser(
        "list",
        help="conf files vs enabled units, drift marked",
        description=(
            "One row per conf file, with its unit state and whether the "
            "generated drop-in still matches the file (DRIFT)."
        ),
        formatter_class=fmt,
    )
    p.set_defaults(func=cmd_list)

    p = sub.add_parser(
        "status",
        help="per-instance active state and restart count",
        formatter_class=fmt,
    )
    p.set_defaults(func=cmd_status)

    p = sub.add_parser(
        "doctor",
        help="check the things that fail silently",
        description="Check the host for the failures that present as something else.",
        epilog=DOCTOR_EPILOG,
        formatter_class=fmt,
    )
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser(
        "keys",
        help="what each config key does and where it goes",
        description=(
            "Print the config key reference. Generated from the module's own "
            "tables, so it cannot drift from what the code actually routes."
        ),
        formatter_class=fmt,
    )
    p.set_defaults(func=cmd_keys)

    p = sub.add_parser(
        "set-credential",
        help="write the GitHub credential",
        description="Write the GitHub credential and load it into the Podman secret.",
        epilog=CRED_EPILOG,
        formatter_class=fmt,
    )
    p.add_argument("id", help="instance the credential belongs to")
    p.add_argument("--stdin", action="store_true", help="read from stdin, not a prompt")
    p.set_defaults(func=cmd_set_credential)

    p = sub.add_parser(
        "check-credential",
        help="check each instance has a usable credential",
        formatter_class=fmt,
    )
    p.add_argument("id", nargs="?", help="one instance, or omit for all")
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


# The end.
