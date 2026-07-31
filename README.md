# gh-runner

RPM-packaged, rootless-Podman, ephemeral GitHub Actions self-hosted runners for
AlmaLinux 10.

Standing up an Actions runner on an EL baseline is a genuinely miserable
afternoon: upstream's `installdependencies.sh` does not know EL10, `svc.sh`
writes a rootful system unit, and the runner assumes Docker. This package makes
it `dnf install`, then one config file and one token per runner.

Each runner takes exactly one job, then its container is destroyed and rebuilt
from the image. Job containers are created by the *host* Podman as siblings of
the runner — so workflows with a `container:` block work without the runner
needing an engine of its own.

The image carries the real Docker CLI and `buildx`, pointed at the host's
Podman socket through its Docker-compatible API. That means `docker` actions
work as written, including `docker/build-push-action`, which needs buildx and
which podman's own `buildx` stub cannot serve.

---

## Not a security layer

**This is a concurrency and runner-sharing tool. It is not an isolation
boundary, and nothing here should be read as one.**

The runner container holds the host Podman socket, which is what makes sibling
job containers possible. Anything running inside it can therefore drive every
container on the box — including creating one that mounts `/`. All instances
share a single uid and a single Podman store, so instance 2 can inspect
instance 3's job containers, and a job can poison the shared toolcache for
later jobs on the same instance.

None of this is specific to this package. It is the standing property of *any*
self-hosted runner: a workflow runs code from the repository with the runner
account's privileges, and anyone who can open a pull request or push a branch
can influence what that code is. GitHub's own guidance against attaching
self-hosted runners to public repositories applies here unchanged.

The boundary is the VM. Treat every runner on a host as being in one trust
domain with every other, and give that host nothing you would not give the
repositories it serves.

---

## Install and add a runner

```bash
dnf install gh-runner
```

Adding a runner takes one command, which prompts for that worker's GitHub
credential and writes its config file:

```bash
gh-runner-ctl add 01 --url https://github.com/OWNER/REPO --labels alma10,podman,c
gh-runner-ctl enable 01
```

Dropping a config file does not start a runner — activation is a separate step,
so configuration management and activation stay separable.

### The credential

Each runner has its own, in `/etc/gh-runner/credentials.d/<id>` at mode 0600.
They are per worker rather than per host, so instances can point at different
repositories or organisations.

It is **not** the registration token from the repo's "New self-hosted runner"
page. Those are single-use and expire in an hour, and an ephemeral runner
re-registers on *every job* — so it needs a credential that can keep minting
registration tokens indefinitely.

Create a **fine-grained PAT** under your account: *Settings → Developer
settings → Personal access tokens → Fine-grained tokens*. Then:

| Runner scope | `RUNNER_URL` | Permission needed |
|---|---|---|
| repository | `https://github.com/OWNER/REPO` | Repository permissions → **Administration**: Read and write |
| organisation | `https://github.com/ORG` | Organization permissions → **Self-hosted runners**: Read and write |

Those are different permissions; one does not imply the other. Set the token's
*Resource owner* to the organisation if the repo belongs to one, and note that
organisations must opt in to fine-grained tokens before they work at all.

```bash
gh-runner-ctl add 01 --url ... --token github_pat_xxx   # or --token-stdin
gh-runner-ctl set-credential 01                         # set or rotate later
gh-runner-ctl check-credential
```

`--token` is accepted for convenience but is visible in shell history and in
`ps` while the command runs; `--token-stdin` avoids both.

## Everyday use

```bash
gh-runner-ctl list                 # instances, credential state, drift
gh-runner-ctl status               # health: restarts against jobs completed
gh-runner-ctl show 01              # effective config, merged and grouped
gh-runner-ctl doctor               # the things that fail silently

gh-runner-ctl start|stop|restart   # no id acts on every enabled instance
gh-runner-ctl disable 01           # drain: finish the current job, then stop
gh-runner-ctl disable 01 --now     # stop now, killing the running job
gh-runner-ctl sync                 # reconcile everything derived from config
gh-runner-ctl rm 01 [--purge]
```

`status` compares restarts against jobs completed, because an ephemeral runner
restarts after every job it finishes — so a restart count alone cannot tell a
healthy loop from a crash loop:

```
ID  NAME            TARGET                STATE    RESTARTS  JOBS  LAST
01  gh-runner-01-a  koskari-lang/koskari  running  7         7     ok
02  gh-runner-01-b  koskari-lang/koskari  running  6         6     ok
```

The whole set is also addressable through systemd directly:

```bash
systemctl --user start gh-runner.target
systemctl --user stop gh-runner.target
```

Stopping the target stops the runners *and* the maintenance timers — the
service becomes inert rather than quietly reaping containers on a schedule.

## Configuration

One file per instance in `/etc/gh-runner/instances.d/<id>.conf`. The format is
Podman's `--env-file`, **not** systemd's `EnvironmentFile`: no quoting, no
escapes, no expansion. `gh-runner-ctl keys` prints the full reference from the
code's own routing tables, so it cannot drift.

Keys fall into three classes by where they actually go:

| Class | Examples | Takes effect |
|---|---|---|
| runtime env | `RUNNER_URL`, `RUNNER_LABELS`, `RUNNER_NAME` | next job |
| job-shaping | `JOB_MEMORY_MAX`, `JOB_CPUS` | next job, applied by the shim |
| unit-shaping | `MEMORY_MAX`, `CPU_WEIGHT` | `sync` + restart |

The distinction matters: `MEMORY_MAX` bounds the runner unit, and job
containers are siblings in their own cgroup scope that inherit nothing from it.
Use `JOB_MEMORY_MAX` to bound a containerised job. Both are real; they cover
different job shapes.

## Documentation

No man pages. The CLI is the reference:

```bash
gh-runner-ctl --help
gh-runner-ctl keys              # every config key, what it does, where it goes
gh-runner-ctl <command> --help  # per-command detail, including the traps
```

The full design, including the constraints that sink naive versions of it, is
in [design/gh-runner-rpm-spec.md](design/gh-runner-rpm-spec.md). Read §5 before
changing anything about mounts.

---

## Building

| Path | What |
|---|---|
| `gh-runner.spec` | Package definition and the authority on the install layout. Holds the upstream runner pin — the only place it appears. |
| `Makefile`, `tools/` | Local build machinery. Installs nothing, and is not a build dependency. |
| `src/preoccupied/gh_runner_ctl/` | Host-side control commands, PEP 420 namespace package. |
| `container/` | The runner image: `Containerfile` and build context. Runs inside the runner container. |
| `units/quadlet/` | Quadlet units, symlinked into `/etc/containers/systemd/users/<uid>/` by `%post`. |
| `units/user/` | The target and maintenance timers → `/usr/lib/systemd/user/`. |
| `patches/` | Applied to the upstream tarball. Each one is a standing rebase obligation. |
| `tests/shim/` | Golden-file tests for the shim's argv rewriting. |

**Python on the host, POSIX shell in the image.** The host already pays for a
Python package, so `prune` and `version-check` are Python and carry unit tests.
The image must not acquire a Python runtime for three scripts, so the
entrypoint, the registration helper, and the `docker` shim are shell.

```bash
make help                  # every target, with the current pin
make check                 # the full local suite
make rpm-container         # build the RPM in a clean almalinux:10 container
```

The RPM is built inside a container because it targets EL10, which is not what
anyone develops on — and because that is the only way to find out whether the
spec's `BuildRequires` are satisfiable there. Output lands in
`dist/<base-image>/`.

The package build itself uses the distro's own macros — `%pyproject_wheel` and
`%pyproject_install` — so a buildroot needs nothing beyond
`pyproject-rpm-macros` and `systemd-rpm-macros`. `uv` is a local development
convenience only and has no part in it.

### Moving the upstream runner pin

```bash
make check-upstream            # are we behind?
make upgrade-runner V=2.337.0
```

`upgrade-runner` fetches the release, records its digest, resets `Release`, and
dry-runs the patch set against the new tarball — refusing to move the pin if a
patch no longer applies. Every patch is a standing rebase obligation, and this
is the cheapest place to discover one has come due.

### Our own version

The spec splits the version from its pre-release qualifier, because they have
different jobs:

```
%global base_version      1.1.0
%global version_qualifier ~dev      # absent for a release
Version:                  %{base_version}%{?version_qualifier}
```

`~` sorts *below* the base, so a development build upgrades cleanly to
`1.1.0-1`. It must never reach a path, though — not the source tarball, not the
`%setup` directory, and not the wheel's `dist-info`, which carries the Python
package's version and can have no qualifier at all since `~` is not valid PEP
440. So everything path-shaped uses `%{base_version}`.

```bash
make bump-version V=1.2.0    # base version, spec and Python together
make qualifier Q=~rc1        # set the qualifier
make qualifier Q=            # clear it: this is a release
```
