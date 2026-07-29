# gh-runner

RPM-packaged, rootless-Podman, ephemeral GitHub Actions self-hosted runners for
AlmaLinux 10.

Standing up an Actions runner on an EL baseline is a genuinely miserable
afternoon: upstream's `installdependencies.sh` does not know EL10, `svc.sh`
writes a rootful system unit, and the runner assumes Docker. This package makes
it `dnf install`, one credential, and one config file per runner.

```bash
dnf install gh-runner
gh-runner-ctl set-credential
gh-runner-ctl add 01 --url https://github.com/OWNER/REPO --labels alma10,podman,c
gh-runner-ctl enable 01
```

The full design, including the constraints that sink naive versions of it, is
in [design/gh-runner-rpm-spec.md](design/gh-runner-rpm-spec.md). Read §5 before
changing anything about mounts.

## Not a security boundary

The runner container holds the host Podman socket, so anything inside it can
drive every container on the box. **The VM is the boundary. Do not attach these
runners to public repositories.** All instances share one uid and one Podman
store; they are one trust domain.

## Layout

| Path | What |
|---|---|
| `gh-runner.spec` | Package definition. Holds the upstream pin — the only place it appears. |
| `Makefile`, `mk/` | Everything is driven from here. `%install` calls `make install`. |
| `src/preoccupied/gh_runner_ctl/` | Host-side control commands, PEP 420 namespace package. |
| `container/` | `Containerfile` and the build context. Runs *inside* the image. |
| `units/quadlet/` | Quadlet units. Symlinked into `/etc/containers/systemd/users/<uid>/` by `%post`. |
| `units/user/` | Ordinary user timers. Quadlet ignores non-Quadlet files, so these must not live in the Quadlet directory. |
| `patches/` | Applied to the upstream tarball. Every one is a standing rebase obligation. |
| `tests/shim/` | Golden-file tests for the argv rewriting — the highest-risk code here. |

**Python on the host, POSIX shell in the image.** The host already pays for a
Python package, so `prune` and `version-check` are Python and get unit tests.
The image must not acquire a Python runtime it would only need for three
scripts, so the entrypoint, the registration helper, and the `docker` shim are
shell.

## Common tasks

```bash
make help              # every target, with the current pin
make check             # version consistency, pytest, shim goldens, unit parse, rpmlint
make rpm               # fetch Source0, verify the digest, build
make install DESTDIR=/tmp/stage   # stage the install tree without rpmbuild
make image             # build the runner image locally
```

### Moving the upstream pin

```bash
make check-upstream           # are we behind?
make upgrade-runner V=2.329.0
```

`upgrade-runner` fetches the release, records its digest, resets `Release`, and
**dry-runs the patch set against the new tarball**. It refuses to move the pin
if a patch no longer applies. That is deliberate: §4 of the design makes every
patch a standing rebase obligation, and this is the cheapest place to discover
it has come due.

Our own version is separate from the pin and moves with `make bump-version
V=x.y.z`, which updates the spec and the Python package together. `make
check-version` fails the build if they drift.

## Documentation

No man pages. The reference is the CLI itself:

```bash
gh-runner-ctl --help          # commands, files, the security caveat
gh-runner-ctl keys            # every config key, what it does, where it goes
gh-runner-ctl disable --help  # per-command detail, including the traps
gh-runner-ctl doctor          # check the things that fail silently
```

`keys` prints from the module's own routing tables, so unlike a man page it
cannot drift from what the code actually does. The shipped config files carry
the same guidance as comments. `make check-help` smoke-tests that every
command's `--help` renders.

## Build host requirements

`uv` builds the wheel and stages it into the buildroot; `make rpm` also needs
`rpm-build` and `python3-setuptools`.

Everything runs `--no-build-isolation`, because an RPM buildroot has no network
and uv must use the setuptools that `BuildRequires` already put there rather
than fetching its own. Using the same flag locally keeps the two paths honest
about each other.

**Verify `uv` is available for the target EL10 buildroot.** If it turns out not
to be packaged there, `uv build` falls back to `python3 -m build` and
`uv pip install --prefix` to `python3 -m installer --destdir`, both one-line
substitutions in `mk/python.mk` and `mk/install.mk`.

## Status

Design is settled; implementation is at the skeleton stage. Milestones are in
design §12. **M0 has not been run**, and it is the one that can invalidate the
design: it must prove `podman-remote` inside the container, over the mounted
socket, with the shim stripping the compiled-in `-v /var/run/docker.sock`.

Until M0 passes, treat `container/context/docker` as unverified. Its unit tests
prove the argv rewriting is self-consistent; only a live capture proves it
rewrites what the runner actually emits. Fixtures in `tests/shim/argv/` marked
`.captured.` come from a real runner — do not invent those.
