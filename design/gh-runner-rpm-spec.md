# `gh-runner` — Design Specification

RPM-packaged, rootless-Podman, ephemeral GitHub Actions self-hosted runners on
AlmaLinux 10.

Status: as built — 1.0.0 released; 1.1.0~dev in development
Target platform: AlmaLinux 10 (EL10), Podman 5.x, systemd 257+

The problem this exists to solve: standing up an Actions runner on an EL
baseline is a genuinely miserable afternoon — upstream's
`installdependencies.sh` does not know EL10, `svc.sh` writes a rootful unit,
and the runner assumes Docker. This package makes it `dnf install`, then one
conf file and one token per runner.

---

## 1. Goals and non-goals

### Goals

- Package upstream `actions/runner` as an RPM with all local deviations held in
  files we author, not in modifications to upstream binaries.
- Each runner executes inside a **container built from our own Containerfile**,
  based on Ubuntu, with build dependencies baked in. Workflows written against
  `ubuntu-latest` work without a `container:` block.
- Each runner is **ephemeral**: one job, then the container exits and is
  recreated from the image. The *container* filesystem is fresh per job. The
  instance state directory is deliberately not — §5 makes that impossible, and
  §5.1 defines what is wiped and what is kept.
- **N instances, configurable after install** without editing package-owned
  files or writing units by hand.
- Rootless throughout. One dedicated service account, no Docker anywhere.
- Reproducible enough that a second host is `dnf install` + drop config files.

### Non-goals

- **Not a security boundary.** The runner container holds the host Podman
  socket, so anything inside it can drive every container on the box. The VM is
  the boundary. Do not attach to public repositories.
- Not autoscaling. Instance count is static and declared.
- Not Kubernetes. If autoscaling is ever needed, that is ARC, not this.
- Not a general-purpose runner distribution. Opinionated for this lab.

---

## 2. Architecture

```
host (AlmaLinux 10, VM)
│
├── user: gh-runner  (uid fixed, linger enabled, subuid block explicit)
│   │
│   ├── quadlet: gh-runner.build ──── builds localhost/gh-runner:latest
│   │                                 from /usr/share/gh-runner/Containerfile
│   │
│   ├── quadlet: gh-runner@01.container ─┐
│   ├── quadlet: gh-runner@02.container ─┤ Ubuntu userspace
│   ├── quadlet: gh-runner@NN.container ─┘ + Runner.Listener (as root, in-container)
│   │        │
│   │        │  mounts (identical paths, see §5):
│   │        │    /var/lib/gh-runner/<id>        → /var/lib/gh-runner/<id>
│   │        │    $XDG_RUNTIME_DIR/podman.sock   → /var/run/docker.sock
│   │        │
│   │        └── runner emits `docker create …`
│   │              └── shim: drop sock mount, add job limits, label
│   │                    └── docker CLI ──────► host podman ◄── podman.socket
│   │                        (+ buildx)         (Docker-compat API)
│   │                                                      │
│   └── job containers ◄──────────────── SIBLINGS, not children
│       (created by host podman, in the same store)
│
└── user timers: gh-runner-prune       (age-based cleanup)
                 gh-runner-image-refresh (weekly rebuild)
                 gh-runner-version-check (upstream floor watch)
```

The engine the shim talks to is the **host** user's Podman, reached over the
mounted `podman.sock`. There is no engine inside the runner container — only
clients: the real Docker CLI with its `buildx` plugin, and `podman-remote`
alongside it (§6.1). Podman's socket serves a Docker-compatible API, which is
what lets the genuine Docker client drive it. `podman.socket` is therefore a
hard runtime dependency of every instance, not an implementation detail
(§8, §10).

**Key consequence:** job containers are siblings of the runner container,
created by host Podman. The runner *believes* it is nesting; it is not.

---

## 3. Package layout

### RPM-owned, read-only

```
/usr/lib/gh-runner/<version>/          pristine extracted upstream tarball
                              bin/
                              externals/
                              config.sh
                              run.sh
                              run-helper.sh
                              env.sh
/usr/lib/gh-runner/current             symlink → <version>
/usr/share/gh-runner/Containerfile
/usr/share/gh-runner/context/          build context (shim, apt manifest)
/usr/share/gh-runner/quadlet/gh-runner.build
/usr/share/gh-runner/quadlet/gh-runner@.container
/usr/lib/systemd/user/gh-runner-prune.service
/usr/lib/systemd/user/gh-runner-prune.timer
/usr/lib/systemd/user/gh-runner-image-refresh.service
/usr/lib/systemd/user/gh-runner-image-refresh.timer
/usr/lib/systemd/user/gh-runner-version-check.service
/usr/lib/systemd/user/gh-runner-version-check.timer
/usr/share/gh-runner/context/entrypoint.sh
/usr/share/gh-runner/context/register.sh
/usr/share/gh-runner/context/docker            the shim
/usr/share/gh-runner/context/packages.list     apt manifest
/usr/bin/gh-runner-ctl
/usr/bin/gh-runner-prune
/usr/bin/gh-runner-version-check
```

**No man pages.** The reference is `gh-runner-ctl --help`, per-command
`--help`, and `gh-runner-ctl keys`, which prints the config-key reference from
the module's own routing tables. That is strictly better than a man page for
this project: the tables are what `sync` and the shim actually consult, so the
documentation cannot drift from the behaviour, and there is no build-time
dependency on a doc toolchain that may not be packaged for EL10. The shipped
config files carry the same guidance as comments, where an operator is already
looking.

**Python on the host, POSIX shell in the image.** The split is by where a
thing executes, and there is no `/usr/libexec/gh-runner/`:

| Runs in | Language | Ships to |
|---|---|---|
| the runner container | POSIX shell | `/usr/share/gh-runner/context/` |
| the host | Python, `console_scripts` | `/usr/bin/` |

`entrypoint.sh`, `register.sh`, and the `docker` shim are build-context only —
they are baked into the image and never execute on the host, so a host copy
under `/usr/libexec` would be a second, divergent original.

Pruning and version-checking go the other way. Both parse JSON, do date
arithmetic, and compare versions numerically, which is shell-and-`jq` misery;
the host already pays for a Python package, so they are `gh-runner-prune` and
`gh-runner-version-check` in `preoccupied.gh_runner_ctl` and carry unit tests.
The image must not acquire a Python runtime it would only need for three
scripts, which is what keeps the boundary clean in the other direction.

**Only Quadlet unit types live in the Quadlet directory.** Quadlet processes
`.container`, `.build`, `.volume`, `.network`, `.pod`, `.image`, and `.kube`.
Anything else placed there — notably a plain `.service` or `.timer` — is
*silently ignored*, with no error and no generated unit. The maintenance timers
are ordinary user units and ship in `/usr/lib/systemd/user/` — the *vendor*
directory (`%{_userunitdir}`), which is package-ownable and needs no per-uid
symlinking.

`/etc/systemd/user/` is a different thing and is not for package content: it is
the local administrator's directory, and it is where `gh-runner-ctl sync`
writes its generated per-instance drop-ins. Shipping vendor units there would
put RPM-owned files in the administrator's namespace and make a locally
overridden unit indistinguishable from a packaged one.

### Package dependencies

```
Requires:       podman >= 5.0
Requires:       systemd >= 257
Requires:       shadow-utils
Requires:       container-selinux
Requires:       policycoreutils-python-utils     # semanage, used in %post
Requires(post): systemd, policycoreutils-python-utils
```

`policycoreutils-python-utils` is easy to forget and fails at `%post` time on a
minimal host, which is exactly where this package is meant to be installed.

### Config (`%config(noreplace)`)

```
/etc/gh-runner/gh-runner.conf              global defaults
/etc/gh-runner/instances.d/                per-instance, one file per runner
/etc/gh-runner/instances.d/example.conf.sample
/etc/gh-runner/credentials.d/              0700 root:root, one file per worker
/etc/gh-runner/credentials.d/<id>          0600, that worker's GitHub token
```

`credentials.d/` is package-owned but its contents are not: one file per
instance, named for the instance id, written by
`gh-runner-ctl set-credential <id>` or by `add`. Root-only, because `ctl` pipes
the value into `podman secret create` and the uid running job containers never
needs to read it. See §7.4.

Only `*.conf` in `instances.d/` is an instance. The shipped sample is
`.conf.sample` precisely so that installing the package activates nothing.

### Generated / state

```
/etc/containers/systemd/users/<uid>/       quadlet units, symlinked from
                                           /usr/share/gh-runner/quadlet/
/etc/systemd/user/gh-runner@<id>.service.d/limits.conf
                                           generated by `ctl sync` from the
                                           unit-shaping keys; derived state
podman secret `gh-runner-token-<id>`        derived from credentials.d/<id>
                                           by `ctl sync`; §7.4
/var/lib/gh-runner/                         home dir + podman graphroot
/var/lib/gh-runner/<id>/                    per-instance runner root
                        bin/  externals/    synced from /usr/lib at start
                        config.sh  run.sh   synced likewise — see below
                        run-helper.sh  env.sh
                        .version            sync marker
                        .drain              drain flag, see §7.3
                        _work/  _diag/
                        .runner  .credentials
```

**Why the runner root lives in `/var` and not `/usr`:** the runner writes
`.runner`, `.credentials`, and `_diag/` into its own root directory. That is
incompatible with RPM ownership. `/usr/lib/gh-runner/<version>` is the pristine
template; `entrypoint.sh` syncs it into the instance state dir when the
`.version` marker differs. The sync is one-time per version per instance, not
per job.

**The sync covers the wrappers, not just `bin/` and `externals/`.** `config.sh`
and `run.sh` resolve their own directory and write `.runner`, `.credentials`,
and `_diag/` relative to it. Run from the read-only `/usr/lib` mount they would
attempt to write into it and fail. The whole extracted tree is synced; the
`/usr/lib` copy is a template that is only ever read.

---

## 4. `%prep` — patch model

Upstream ships a compiled .NET payload plus shell wrappers. Only the wrappers
are patchable; everything meaningful in container orchestration is inside
`Runner.Worker` assemblies.

```
Source0: gh-runner-%{version}.tar.gz                          this project
Source1: actions-runner-linux-x64-%{runner_version}.tar.gz    upstream release
```

**Verify, do not vendor.** `%prep` checks the SHA256 against the value in the
spec and extracts. No repacking.

The two `%global` lines carrying `runner_version` and `runner_sha256` are the
only place either value appears; the Makefile reads them back rather than
restating them. `make upgrade-runner V=x.y.z` moves both, and dry-runs the
patch set against the new tarball before committing to the move.

**The package build uses the distro's own Python macros** — `%pyproject_wheel`
in `%build`, `%pyproject_install` in `%install` — so an RPM builds from a stock
buildroot with nothing but `pyproject-rpm-macros`. `%install` then places the
non-Python files with plain `install` commands. It does not delegate to a
`make install` target: the spec is the single authority on the install layout,
and a parallel make target would be a second source of truth that nothing
verifies. `uv` is a local development convenience only and has no part in the
package build.

**Nothing is excluded.** An earlier version of this section removed the
service templates, `runsvc.sh`, and the macOS equivalents as "rootful service
machinery that must never run here". `config.sh` then failed outright:

```
Could not find file '/var/lib/gh-runner/01/bin/systemd.svc.sh.template'
```

It reads those templates unconditionally, even under `--unattended
--ephemeral`. Deleting files from a vendored tree whose internal loading order
we do not control buys nothing — we simply never invoke `svc.sh` — and costs a
build-and-deploy cycle to discover. Same reasoning as §4.1 on shipping all four
node runtimes: the deviation has to earn its place, and this one did not.

`bin/installdependencies.sh` stays for the same reason. It does not know EL10
and must never be run, but the image bakes its dependencies in and nothing
invokes it.

**The sync marker is version-release, not the upstream version.**
`entrypoint.sh` re-syncs the tree into an instance's state directory when
`/usr/lib/gh-runner/current/.version` differs from the instance's copy. A
marker of `runner_version` alone means a package that changes *what* it ships
for a given upstream version never triggers a re-sync — which is precisely the
trap that un-excluding those files walked into: the corrected RPM installs
cleanly and every existing instance keeps running the old tree. The marker is
therefore `<runner_version>-<version>-<release>`, so any rebuild re-syncs.

### 4.1 What the bundle actually weighs

216MB compressed, **644MB unpacked**, and the runner itself is the small part:

| Path | Size | Used by |
|---|---:|---|
| `externals/node24` | 187 MB | actions declaring `using: node24` |
| `externals/node20` | 153 MB | actions declaring `using: node20` |
| `externals/node24_alpine` | 126 MB | node24 actions in a musl job container |
| `externals/node20_alpine` | 100 MB | node20 actions in a musl job container |
| `bin/` | 79 MB | `Runner.Listener`, `Worker`, `PluginHost`, .NET runtime |

Four bundled Node runtimes are 88% of the package. **We ship all of them
anyway.** The `_alpine` pair is 226MB that only matters when a job runs in a
musl-based container, and dropping them is tempting — but the failure mode is
an action dying *inside a job container* with an unhelpful missing-interpreter
error, on some future workflow nobody remembers this decision for. Every file
removed here is also a deviation to re-justify on each upgrade, against a
`%prep` that already has to be re-checked (see above).

The size is real and shows up in three places: package size, per-host disk, and
`%prep` time during a build. The last of those is why the builder image bakes
its dependencies (§12) — it removed the `rpmbuild -br` round, and with it a
second unpack of this tree.

**Patches:**

| Patch | Target | Purpose |
|---|---|---|
| `0001-remove-selfupdate-loop.patch` | `run.sh` | Drop the `returnCode == 2` restart branch. With `--disableupdate` it is dead code; removing it makes an unexpected update attempt fail loudly rather than loop. |

**Deliberately not patched:**

- Root guard in `config.sh` / `run.sh` — handled by `ENV RUNNER_ALLOW_RUNASROOT=1`
  in the Containerfile. One line, no rebase cost.
- The hardcoded `-v /var/run/docker.sock` mount — lives in
  `ContainerOperationProvider`, compiled. Handled by the PATH shim in the image
  (§6). This is the deviation that motivates the whole design.

Every patch is a rebase obligation against each upstream release. Keep the set
minimal.

---

## 5. The identical-path invariant

**This is the constraint that sinks naive versions of this design. Treat it as
a hard rule.**

Host Podman resolves the *source* side of every `-v` the runner emits. The
runner emits paths as it sees them inside its own container. Therefore:

> Every path the runner references must exist at the **identical absolute path**
> both inside the runner container and on the host.

Affected paths, all under the instance state dir:

```
/var/lib/gh-runner/<id>/_work
/var/lib/gh-runner/<id>/_work/_temp
/var/lib/gh-runner/<id>/_work/_actions
/var/lib/gh-runner/<id>/_work/_tool
/var/lib/gh-runner/<id>/externals
```

Hence the mount is `/var/lib/gh-runner/<id>:/var/lib/gh-runner/<id>` — not a
convenience, a requirement. Baking the runner tree into the image instead is
the classic failure mode.

### 5.1 Consequence: the workspace outlives the container

The invariant forces the instance state directory to be a persistent host bind
mount. So "ephemeral" applies to the container, not to `_work`. Every job sees
whatever the previous job left in `_work`, `_work/_actions`, and
`_work/_tool`.

This must not be quietly relied on as a security property; it is a performance
property. Re-downloading the toolcache and every action on each job is most of
the cold-job latency this design exists to avoid, so the persistence is kept
and scoped explicitly:

| Path | Per-job treatment | Why |
|---|---|---|
| `_work/<repo>` | **removed** by `entrypoint.sh` before `run.sh` | Job checkouts must not leak across jobs. |
| `_work/_temp` | **removed** | Scratch. Also where credentials get staged. |
| `_work/_actions` | kept | Action checkouts, content-addressed by ref. |
| `_work/_tool` | kept | Toolcache. Expensive to rebuild, safe to share. |
| `_diag` | kept, pruned by age (§8) | Needed to debug the previous failure. |

`RUNNER_WIPE_WORK=1` in an instance conf drops `_actions` and `_tool` as well,
for an instance that wants maximum isolation and will pay for it. Default is
`0`.

The trust statement is therefore: **the container is fresh per job, the
workspace is fresh per repository, and the caches are shared within an
instance.** All instances share one uid and one Podman store regardless (§11),
so this adds no exposure that the architecture did not already have.

---

## 6. Container image

`/usr/share/gh-runner/Containerfile`, built once by the `.build` quadlet.

```dockerfile
# The runner image. Built once by the gh-runner.build quadlet and refreshed
# weekly by gh-runner-image-refresh.timer.
#
# Base is pinned by TAG, not digest. Digest-pinning is the better instinct in
# general, but it would make Pull=newer a no-op and silently defeat the
# refresh timer — the image would look fresh forever while its packages aged.
FROM docker.io/library/ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

# Docker's own archive, for docker-ce-cli and docker-buildx-plugin. Ubuntu's
# docker.io is daemon-plus-CLI with no clean CLI-only split, and we want the
# client alone -- there is still no engine in this container.
#
# This is the only non-Ubuntu source here, and it is a deliberate one: buildx
# is a Docker CLI plugin, so actions like docker/build-push-action cannot work
# without it. Podman's `buildx` is a stub that rejects `create --name`.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && install -m0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
         -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && . /etc/os-release \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc]" \
            "https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
       > /etc/apt/sources.list.d/docker.list \
    && rm -rf /var/lib/apt/lists/*

# Everything the runner or a workflow could need, baked in. See
# context/packages.list — the manifest is the thing you edit.
COPY context/packages.list /tmp/packages.list
RUN apt-get update \
    && sed -e 's/#.*//' -e '/^\s*$/d' /tmp/packages.list \
       | xargs apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* /tmp/packages.list

# Upstream's config.sh and run.sh refuse to run as root. In this container
# root is mapped to the unprivileged host service account, so the guard is
# measuring the wrong thing. One env var, no patch, no rebase cost.
ENV RUNNER_ALLOW_RUNASROOT=1

# There is no engine here — only clients, both pointed at the *host* engine
# over the socket the quadlet mounts at /var/run/docker.sock. Podman's socket
# serves a Docker-compatible API alongside its own, so the real Docker CLI
# works against it; that is what makes buildx possible.
#
# Two variables because they are two clients: the Docker CLI reads DOCKER_HOST,
# podman-remote reads CONTAINER_HOST. podman-remote is kept as a second way to
# interrogate the engine when something is confusing.
ENV DOCKER_HOST=unix:///var/run/docker.sock
ENV CONTAINER_HOST=unix:///var/run/docker.sock

# The shim. The runner still emits `-v /var/run/docker.sock:...` from compiled
# code we cannot patch, but the `docker` it resolves is now ours.
COPY context/docker /usr/local/bin/docker
COPY context/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY context/register.sh /usr/local/bin/register.sh
RUN chmod 0755 /usr/local/bin/docker \
                /usr/local/bin/entrypoint.sh \
                /usr/local/bin/register.sh

LABEL net.preoccupied.gh-runner.role=image

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

**The shim moves into the image.** The runner still emits
`-v /var/run/docker.sock:/var/run/docker.sock`, but the `docker` it resolves is
now ours, inside the container, version-controlled in the package. The host
keeps no global symlink and no mount namespace hack. This is strictly better
than either host-side workaround.

### 6.1 The client is the real Docker CLI

The shim hands off to `/usr/bin/docker`, not to `podman-remote`. Podman's
socket serves a **Docker-compatible API** alongside its own, so the genuine
client works against it — the Docker CLI negotiates down (`API version 1.44`)
and talks to Podman 5.8.2 without complaint.

This exists because `buildx` is a Docker CLI *plugin*, and podman's `buildx` is
a stub that rejects `create --name` — so `docker/build-push-action` fails at its
first call. With the real client, `buildx create --driver docker-container`
bootstraps a genuine buildkitd container through the compat API and builds
work.

Two consequences worth holding onto:

**buildx never passes through the shim.** It speaks the API directly rather
than shelling out, so the buildkitd container it spawns carries no `role=job`
label and no `JOB_MEMORY_MAX`. `gh-runner-prune` matches those by name
(`buildx_buildkit_*`) instead — the reap-by-exclusion pattern §8 otherwise
argues against, accepted here because there is no label to select on and
buildkit's own GC allows around 20GiB per builder.

**BuildKit's cache is not Podman's.** `cache-from: type=gha` cannot work
(no Actions cache backend), and the host's persistent image store does *not*
substitute for it: buildkit keeps its cache inside the builder's state volume.
With `docker/build-push-action` naming a fresh `builder-<uuid>` per job, that
cache is created and destroyed every time. A fixed builder name, or
`type=local` pointed under `_work/_tool` (which persists per §5.1), restores it.

**There is no engine inside the runner container** — only clients. The shim
execs the Docker CLI, and `podman-remote` is kept as a second way to interrogate
the engine; both connect via
`DOCKER_HOST` and `CONTAINER_HOST` respectively, both pointed at the socket the
Quadlet mounts to `/var/run/docker.sock`. Installing a full `podman` or a
`dockerd` would give the runner a second, empty container store, and every job
would silently re-pull its images into a store the host never prunes. Clients
only, one store, one engine.

The Docker CLI comes from Docker's own archive, since Ubuntu ships no clean
CLI-only package — the sole non-Ubuntu source in this image, and a deliberate
one.

Ubuntu 24.04 does package `podman-remote` separately, and its client talks to
the host's Podman 5.8.2 without complaint — the version-skew concern that
originally motivated pinning it turned out not to bite, so the package list
carries no version constraint. The manifest is `context/packages.list`, one
package per line, which is the file to edit when a workflow needs a tool.

Shim behaviour, in order:

1. Drop the `docker.sock` `-v` pair. A build-time flag switches this to
   *rewrite* the mount instead, for workflows that genuinely need socket access
   inside the job container. Default is drop.
2. On `create` / `run`, inject `--label net.preoccupied.gh-runner.role=job` and
   `--label net.preoccupied.gh-runner.id=$RUNNER_ID`, so §8's pruner can
   identify what it owns rather than reaping by exclusion.
3. On `create` / `run`, inject `--memory=$JOB_MEMORY_MAX` and
   `--cpus=$JOB_CPUS` when those are set (§7). This is the only point at which
   a job container's resources can be constrained; see §7.2.
4. `exec /usr/bin/docker` with the remainder — by absolute path. The shim
   *is* `/usr/local/bin/docker` and `/usr/local/bin` precedes `/usr/bin`, so
   resolving through `PATH` would re-invoke it forever, and that failure
   presents as a hang rather than as anything naming the shim.

`openssl` is present because GitHub App authentication (§11) requires signing a
JWT, and `curl` plus `jq` cannot do that alone. It is a few hundred kilobytes
and removes the need to rebuild the image at the moment the PAT is retired.

Note `--no-install-recommends` plus explicit `build-essential`: on Ubuntu,
`libc6-dev` is a *Recommends* of `gcc`, not a Depends, so a slimmed image
otherwise yields a `gcc` with no `stdlib.h`.

---

## 7. Instance configuration — the multi-runner proposal

### Decision: directory of files, one per instance

`/etc/gh-runner/instances.d/<id>.conf`

The instance ID is the filename stem and is used consistently as:

- systemd template instance — `gh-runner@<id>.service`
- container name — `gh-runner-<id>`
- state directory — `/var/lib/gh-runner/<id>/`
- default runner name — `<hostname>-<id>`

**File format: `KEY=value`, one per line, `#` comments.** No parser to write,
no format to document beyond `gh-runner-ctl keys`, and Quadlet consumes it via
`EnvironmentFile=` in `[Container]`.

The syntax is **Podman's `--env-file`, not systemd's `EnvironmentFile`** — that
is where Quadlet routes the key. The two are similar enough to be confused and
different enough to bite: Podman does not perform systemd's quote removal or
escape processing, so `RUNNER_NAME="build box"` yields a name that includes the
quote characters. Keep values unquoted and free of leading or trailing
whitespace. `gh-runner-ctl show` reproduces Podman's parsing rules, not
systemd's, and `ctl edit` rejects a file that relies on the difference.

```ini
# /etc/gh-runner/instances.d/01.conf

# --- runtime env: consumed by entrypoint.sh at each container start
RUNNER_URL=https://github.com/koskari-lang/koskari
RUNNER_LABELS=alma10,podman,c
#RUNNER_NAME=                       # default: <hostname>-<id>
#RUNNER_GROUP=default
#RUNNER_WIPE_WORK=0                 # see §5.1

# --- job-shaping: consumed by the shim, applied to job containers
JOB_MEMORY_MAX=12G
JOB_CPUS=4

# --- unit-shaping: routed to a systemd drop-in, see below
MEMORY_MAX=2G
CPU_WEIGHT=100
```

Global defaults in `/etc/gh-runner/gh-runner.conf`, loaded first; per-instance
file wins.

### 7.1 Three classes of key, one file

One operator-facing file, but the keys have three different destinations and
this must not be papered over:

| Class | Keys | Destination | Takes effect |
|---|---|---|---|
| **Runtime env** | `RUNNER_URL`, `RUNNER_NAME`, `RUNNER_LABELS`, `RUNNER_GROUP`, `RUNNER_WIPE_WORK` | `EnvironmentFile=` in the `.container`; read by `entrypoint.sh` | Next container start |
| **Job-shaping** | `JOB_MEMORY_MAX`, `JOB_CPUS` | same `EnvironmentFile=`; read by the **shim**, applied to each job container | Next job |
| **Unit-shaping** | `MEMORY_MAX`, `CPU_WEIGHT`, extra volumes | `/etc/systemd/user/gh-runner@<id>.service.d/limits.conf`, generated | `daemon-reload` + restart |

Quadlet fixes unit-shaping values at generation time, so an `EnvironmentFile`
cannot reach them. Without the generated drop-in, `MEMORY_MAX` in a conf file
silently does nothing — the specific trap this split exists to avoid.

### 7.2 Why job-shaping is a separate class

`MEMORY_MAX` constrains the **runner unit**, and the runner unit contains only
`Runner.Listener` — a few hundred megabytes of .NET that never does any work.
Job containers are siblings created by host Podman (§2); they land in their own
cgroup scope under `user.slice` and inherit nothing from the runner unit.

So `MEMORY_MAX=12G` on a runner instance limits the wrong process, and the
compiler that eats the host still eats the host. This is the same silent-no-op
failure as an ungenerated drop-in, one level further down, and it is worse
because the value *looks* applied — `systemctl show` reports it faithfully.

`JOB_MEMORY_MAX` and `JOB_CPUS` are therefore distinct keys with a distinct
mechanism: they ride in as ordinary container env and the shim turns them into
`--memory` / `--cpus` on every `docker create` and `docker run` it forwards
(§6). That is the only place in the architecture with the job container's
command line in hand.

Two consequences worth stating:

- A job that runs **no** container — a plain `runs-on` job with no
  `container:` block and no service containers — executes inside the runner
  container itself, and is bounded by `MEMORY_MAX`, not `JOB_MEMORY_MAX`. Both
  keys are real; they cover different job shapes. Set both. `MEMORY_MAX`
  defaults to `2G` on the assumption of containerised jobs and wants raising on
  an instance that runs bare ones.
- Enforcement needs cgroup delegation for the `memory` and `cpu` controllers on
  `user@<uid>.service`. Modern systemd delegates these by default;
  `gh-runner-ctl doctor` checks `/sys/fs/cgroup/user.slice/user-<uid>.slice/
  cgroup.controllers` and says so plainly if they are missing, because the
  failure mode is otherwise a limit that is configured, reported, and not
  enforced.

The drop-in targets the **generated service**, not the `.container` file:
`/etc/systemd/user/gh-runner@<id>.service.d/`. systemd applies drop-ins from
its own unit path to generator-produced units, which gives unambiguous
per-instance scoping. Quadlet's handling of instance-specific `.container.d`
drop-ins for a *template* unit is the sort of thing that works until it
doesn't, and a silently-unapplied limit is the failure this whole section
exists to prevent.

`gh-runner-ctl sync` regenerates the drop-ins from the conf files rather than
assuming they are current, so a hand-edited conf becomes real on the next
`sync`. Sync means sync.

### 7.3 Reconfiguration is nearly free

Ephemeral runners re-register on every container start, so changing
`RUNNER_LABELS` takes effect when the current job finishes and the next
container comes up. No `config.sh remove`, no re-registration dance — the usual
tax on a persistent runner does not apply.

`ctl` therefore defaults to **graceful**: mark the instance and let the natural
job-boundary exit pick up the change. `--now` forces an immediate restart and
kills any running job. Unit-shaping changes always require `daemon-reload` plus
a real restart, which is the second reason to keep the three classes visibly
distinct.

**The drain mechanism.** "Graceful" needs a concrete implementation, because
neither systemd verb does what is wanted here: `systemctl --user disable` only
affects the next boot and leaves the restart loop running, while `stop` kills
the in-flight job — precisely the distinction `--now` is meant to express.

```
ctl disable <id>          touch /var/lib/gh-runner/<id>/.drain
                          (current job finishes, container exits, no restart)
ctl disable <id> --now    rm nothing; systemctl --user stop, job dies
ctl enable  <id>          rm .drain; systemctl --user enable --now
```

`entrypoint.sh` checks for `.drain` **before** registering (§9 step 1) and
exits `78` if present. The unit carries `RestartPreventExitStatus=78`, so
`Restart=always` declines to restart and the instance settles into
`inactive (dead)` at a job boundary with nothing half-finished. Any container
already running a job is untouched and drains on its own.

`ctl list` reports a drained-but-enabled instance as `draining`, since
"enabled, not running, on purpose" is otherwise indistinguishable from a crash
loop that has given up.

### Alternatives considered and rejected

| Option | Rejected because |
|---|---|
| Single conf with `RUNNER_COUNT=4` | No per-instance identity. Cannot give instance 3 different labels or point it at a different repo. Cannot enable/disable one. |
| systemd generator reading a config | Correct mechanism, genuinely miserable to debug — failures are silent and the generator runs before most of the system is up. Not worth it for a static count. |
| Units written directly by Ansible | Works, but puts the package's own layout knowledge in the role. Package should own its units. |

### Activation

Dropping a file does **not** start a runner — deliberate, so that config
management and activation stay separable.

```bash
gh-runner-ctl add <id> --url [--labels --name --group]    prompt for this
                                           worker's token, then scaffold its
                                           conf file. --token for inline,
                                           --token-stdin for automation,
                                           --no-token to defer.
gh-runner-ctl show <id>                    effective config, merged and grouped
gh-runner-ctl edit <id>                    $EDITOR, validate on save
gh-runner-ctl rm <id> [--purge]            disable, drop credential and secret,
                                           remove conf; --purge takes the state

gh-runner-ctl enable <id> [--now]          link into gh-runner.target.wants
gh-runner-ctl disable <id> [--now]         drain by default, --now kills the job
gh-runner-ctl start|stop|restart [<id>]    no id acts on gh-runner.target
gh-runner-ctl sync                         reconcile every derived artifact
gh-runner-ctl list                         conf files vs enabled units, drift
gh-runner-ctl status                       restarts against jobs completed
gh-runner-ctl keys                         the config key reference
gh-runner-ctl doctor                       quadlet -dryrun, linger, subuid,
                                           podman.socket, cgroup delegation,
                                           credentials, labels

gh-runner-ctl set-credential <id> [--token|--stdin]
                                           write credentials.d/<id>, 0600, then
                                           load it into gh-runner-token-<id>
gh-runner-ctl check-credential [<id>]      report per-instance credential and
                                           secret state
```

`sync` is the converge operation and owns more than the drop-ins: Quadlet
symlinks, unit drop-ins, per-instance Podman secrets, the instance state
directories, `podman.socket`, and enabling the maintenance timers. Several of
those were originally left to `%post`, which runs them with `|| :` — so when
they failed, they failed silently and the instance was stranded with no
indication why. A converge step that must actually hold is worth more than a
scriptlet that tries once.

`gh-runner-ctl` is a thin wrapper that runs `systemctl --user` as the service
account with the correct `XDG_RUNTIME_DIR` and `DBUS_SESSION_BUS_ADDRESS` —
i.e. it encapsulates the `machinectl`/`sudo -u` awkwardness so an operator never
has to think about it.

`show` earns its place because of the global-plus-instance merge: "why is this
runner picking up that label" is otherwise a manual diff against
`gh-runner.conf`.

### 7.4 The credential is per worker

**A single host-wide token cannot work, and an earlier draft of this section
said it could.** It described `/etc/gh-runner/credentials` as a virtue —
"the credential has one path" — while §7's rejected-alternatives table
dismissed `RUNNER_COUNT=4` precisely because it "cannot point instance 3 at a
different repo." Pointing an instance at a different repo is exactly what
needs a different token. The two positions were incompatible and the singleton
lost.

Every other attribute of a worker is per instance: URL, labels, name, group,
limits, state directory, unit. The credential is what *authorises* it, so it is
per instance too:

```
/etc/gh-runner/credentials.d/<id>  ──[ set-credential | sync ]──►  podman secret
        0600, root-only                                     gh-runner-token-<id>
```

The Quadlet mounts it with `Secret=gh-runner-token-%i`, so each container sees
only its own token at `/run/secrets/gh-token` and `register.sh` needs no
changes.

**The service account never reads these files.** `ctl` runs as root and pipes
the value straight into `podman secret create`, so `credentials.d/` is mode
`0700 root:root`. A token readable by the uid that runs job containers would be
a token readable by any job.

**`add` takes the credential.** This is the part that was structurally missing,
not merely inconvenient: `add` is the command whose entire purpose is "set up a
new worker", and it had no way to express the one thing without which the
worker cannot register. It prompted for nothing, wrote a conf file mentioning no
credential, and printed `next: gh-runner-ctl enable` — which then failed,
naming a global command the operator had never been told about. `add` now
prompts first, and `--no-token` is the explicit way to defer.

**Never from argv.** No `--token VALUE`. A PAT on a command line is in shell
history and in every `ps` on the box for the duration of the call.
`--token-stdin` covers automation.

`sync` recreates a secret whenever its file differs, so the files stay the
source of truth and the secrets are derived state, consistent with the
`instances.d/` invariant below. `podman secret create` cannot update in place,
so `sync` removes and recreates; a container already running keeps the old value
until its next start, which is the usual job-boundary behaviour and needs no
special handling. That also makes rotation a normal operation.

Asymmetric cleanup, deliberately: `sync` **removes** secrets for instances that
no longer have a conf file, but only **reports** credential files with no
instance. A regenerable secret is safe to delete; a token is not recoverable,
and a conf file can go missing for reasons that are not "this worker is gone".

`ctl doctor` reports per-instance credential state, and `ctl enable` refuses on
a missing one rather than letting the instance enter a restart loop against a
Podman error that never mentions credentials.

What to supply is a fine-grained PAT with `administration: write` on the
target, or a GitHub App key — **not** a registration token from the "add
runner" page. Those expire in an hour, and an ephemeral runner re-registers on
every single job, so the box needs a credential that can mint them
indefinitely. That is also why the PAT blast radius in §11 is unavoidable
rather than lazy.

### Invariant: `instances.d/` is the only store

`ctl` has **no secondary state** — no database, no cache, no registry file. It
reads and writes the same files a human or a config-management tool would, and
`list`/`show` derive everything from the directory plus a systemd query. The
generated unit drop-ins and the Podman secret are derived state, regenerated by
`sync`, never a second source of truth.

Consequence: hand-editing a conf file and running `sync` is fully equivalent to
going through `ctl`. `add` is a scaffolding convenience — `useradd`, not
Puppet — so nothing is obliged to use it.

`sync` is the integration point if this is ever driven from Ansible: template
`instances.d/`, call `sync`, done. One idempotent command, no unit-name
knowledge outside the package.

---

## 8. Units

Two kinds, and the distinction is load-bearing (§3): the runner image and the
runner instances are **Quadlet** units in
`/etc/containers/systemd/users/<uid>/`; the three maintenance timers are
**ordinary user** units in `/usr/lib/systemd/user/`.

### `gh-runner.build`

```ini
[Unit]
Description=Build GitHub Actions runner image

[Build]
ImageTag=localhost/gh-runner:latest
File=/usr/share/gh-runner/Containerfile
SetWorkingDirectory=/usr/share/gh-runner
Pull=newer
```

### `gh-runner@.container`

```ini
[Unit]
Description=GitHub Actions Runner (%i)
# Not optional. The mount source below is %t/podman/podman.sock; if the socket
# unit has not run, that path does not exist and Podman creates a *directory*
# there, after which the shim's CONTAINER_HOST points at a directory and every
# docker call inside the runner fails with a message about the wrong thing.
Requires=podman.socket
After=podman.socket

# Stop and restart propagate from the aggregate target. A target does not stop
# the units it Wants, only those declaring themselves part of it, so without
# this `systemctl stop gh-runner.target` would leave every runner running.
PartOf=gh-runner.target
After=gh-runner.target

[Container]
Image=gh-runner.build
ContainerName=gh-runner-%i

# Podman --env-file syntax, not systemd EnvironmentFile syntax. No quoting,
# no escapes, no expansion. See gh-runner.conf(5).
EnvironmentFile=/etc/gh-runner/gh-runner.conf
EnvironmentFile=/etc/gh-runner/instances.d/%i.conf

Environment=RUNNER_ID=%i
Environment=RUNNER_ROOT=/var/lib/gh-runner/%i

# The identical-path invariant (design §5): host Podman resolves the source
# side of every -v the runner emits, and the runner emits paths as it sees
# them inside its own container. Same path both sides is a requirement, not a
# convenience. No :Z here — it would relabel with this container's private
# MCS category and lock the sibling job containers out of _work.
Volume=/var/lib/gh-runner/%i:/var/lib/gh-runner/%i
Volume=/usr/lib/gh-runner/current:/usr/lib/gh-runner/current:ro
Volume=%t/podman/podman.sock:/var/run/docker.sock

# SELinux checks a unix socket twice, and only the first check is about the
# socket. Both denials were hit here, in order:
#
#   1. the file.  container_t could not stat the socket at all:
#        ls: cannot access '/var/run/docker.sock': Permission denied
#      `:z` fixes that by relabelling it container_file_t:s0.
#
#   2. connectto. Having reached the file, connect() still failed:
#        dial unix /var/run/docker.sock: connect: permission denied
#      unix_stream_socket connectto is checked against the *listening
#      process's* label — podman itself — not the socket file's. Relabelling
#      the socket cannot reach it. There is nothing to relabel.
#
# Hence label confinement off rather than a label fix. It costs nothing that
# is not already given away: this container holds the host Podman socket, so it
# can create a container mounting / and do anything on the box. §1 says plainly
# that it is not a security boundary and the VM is; SELinux confining a process
# that can drive podman is theatre.
#
# Job containers are unaffected — created by the host engine as siblings, still
# fully confined — which is why §10's _work fcontext rules still matter.
SecurityLabelDisable=true

Secret=gh-runner-token-%i,type=mount,target=/run/secrets/gh-token

# Without this the container's output dies with it, since Quadlet passes --rm.
# Diagnosing a crash-looping runner then means reproducing the podman run by
# hand, which is not a thing to ask of anyone.
LogDriver=journald

Label=net.preoccupied.gh-runner.role=runner
Label=net.preoccupied.gh-runner.id=%i

# No PodmanArgs=--rm. Quadlet already emits --replace --rm in the generated
# ExecStart, and --replace is load-bearing: an unclean exit leaves a named
# container behind, and without it the next start collides on the name.

[Service]
Restart=always
RestartSec=5

# The drain mechanism (design §7.3). entrypoint.sh exits 78 when it finds
# /var/lib/gh-runner/%i/.drain, which lets an instance stop at a job boundary
# instead of having a running job killed.
RestartPreventExitStatus=78

# A persistent failure — bad credential, GitHub unreachable — would otherwise
# trip the default start limit and leave the unit failed, needing a manual
# reset-failed. A runner that stays down after the outage that caused it has
# ended is worse than one that keeps retrying visibly.
StartLimitIntervalSec=0

# No [Install]. WantedBy= in a template instantiates nothing without a
# DefaultInstance, so this section only ever looked like it was doing
# something. `gh-runner-ctl enable <id>` writes the per-instance
# gh-runner.target.wants symlink instead.
```

`Image=gh-runner.build` is the **literal filename**, not the tag — that is what
makes Quadlet generate the ordering dependency on `gh-runner-build.service`.

`Requires=podman.socket` is not optional. The socket mount source is
`%t/podman/podman.sock`; if the socket unit has not run, that path does not
exist and Podman creates a *directory* there, after which the shim's
`CONTAINER_HOST` points at a directory and every `docker` call inside the
runner fails with a message about the wrong thing. `%post` enables the socket
(§10) and this ordering keeps it true across reboots.

No `PodmanArgs=--rm`. Quadlet already emits `--replace --rm` in the generated
`ExecStart` for a `.container`, and `--replace` is load-bearing here: an
unclean exit leaves a named container behind, and without replace the next
start collides on the name and wedges the instance. Restating `--rm` by hand
obscures that it — and the `--replace` we actually depend on — come from
Quadlet.

`StartLimitIntervalSec=0` disables start-rate limiting. `RestartSec=5` keeps
the loop well under the default burst in normal operation, but a persistent
failure (bad credential, GitHub unreachable) would otherwise trip the limit and
leave the unit `failed` needing a manual `reset-failed` — a runner that stays
down after the outage that caused it has ended. The instance should keep
retrying and be visible in `ctl status` as retrying.

`RestartPreventExitStatus=78` implements the §7.3 drain.

### `gh-runner.target`

The aggregate handle. Instances are linked into `gh-runner.target.wants/` by
`ctl enable`, which gives *start* propagation, and declare
`PartOf=gh-runner.target`, which gives *stop* and restart — a target does not
stop the units it merely `Wants`, so both are needed and neither alone
suffices.

The maintenance timers below are part of it as well. Stopping gh-runner should
leave the whole thing inert, not quietly reaping containers on a schedule. They
too carry both `WantedBy=` and `PartOf=`, since `PartOf` propagates stop but
never start.

`gh-runner-build.service` is the one exception: it holds no running process, so
stopping it accomplishes nothing, and cycling it would re-run the image build
on the next start for no reason.

### `gh-runner-prune.timer`

Ordinary user units in `/usr/lib/systemd/user/`, not Quadlet files (§3).

Every 30 minutes, calling `gh-runner-prune`. It reaps containers, networks, and
volumes carrying `net.preoccupied.gh-runner.role=job` — the label the shim
stamps on creation (§6) — that have been stopped longer than
`PRUNE_MAX_AGE` (default `2h`), plus dangling images and `_diag` files older
than `DIAG_MAX_AGE` (default `14d`).

**Age-based, not idle-gated.** An earlier form of this no-op'd whenever any
`Runner.Worker` process existed. On a host with several instances and steady
traffic there is essentially always one, so the pruner would never run —
failing exactly under the load that makes it necessary. Age is per-object and
composes with concurrent jobs: a container stopped two hours ago is garbage
regardless of what else is running.

**Positive selection, not exclusion.** Reaping "everything that is not
`role=runner`" would also reap anything else the service account happens to
own, and would race a job container between `create` and `start`. The pruner
touches only objects it can prove the shim created, and the age floor covers
the creation race.

### `gh-runner-image-refresh.timer`

Weekly, plus `Persistent=true`. Runs `systemctl --user restart
gh-runner-build.service` with `Pull=newer` in effect, so the base image and the
baked apt packages are refreshed.

Without this the build unit's `RemainAfterExit=yes` means the image is built
exactly once, at install. The ephemeral loop then makes everything *look* fresh
— new container every job — while the toolchain inside it silently ages toward
the first "works on my machine" that is really "the runner's `gcc` is two years
old". Rebuilding is cheap and the failure mode is expensive.

The refresh does not disturb running jobs: existing containers keep the image
they started with, and instances pick up the new one at their next job
boundary.

### `gh-runner-version-check.timer`

Daily. `gh-runner-version-check` compares `/usr/lib/gh-runner/current/.version`
against the latest `actions/runner` release tag and logs at warning level when
the gap exceeds `VERSION_WARN_RELEASES` (default 2).

`--disableupdate` means the runner will never self-update, and GitHub
eventually refuses registration from runners below a floor it moves without
notice. That converts "ship a new RPM" from an occasional chore into a standing
obligation, and the failure presents as every instance simultaneously failing
to register — a confusing outage if nothing has been watching the version. This
timer makes it a warning in the journal weeks earlier. It only reports; it
never updates anything.

---

## 9. Entrypoint flow

Per container start, i.e. per job:

1. **Drain check.** If `$RUNNER_ROOT/.drain` exists, log and `exit 78`.
   `RestartPreventExitStatus=78` stops the loop here (§7.3). This is first so
   that a drained instance never mints a token it will not use.
2. Source instance env (already in environment via `EnvironmentFile`).
3. Sync `/usr/lib/gh-runner/current` → `$RUNNER_ROOT` if the `.version` marker
   differs — the full tree, wrappers included (§3). Idempotent; no-op on the
   common path.
4. **Workspace hygiene** (§5.1): remove `_work/<repo>` and `_work/_temp`;
   keep `_work/_actions` and `_work/_tool` unless `RUNNER_WIPE_WORK=1`.
5. **Preflight.** Verify `/var/run/docker.sock` is a socket and
   `podman-remote info` succeeds. Fail loudly here rather than sixty seconds
   later inside a job step, where it surfaces as an inscrutable `docker: command
   failed` in a workflow log the operator may not even be able to see.
6. Read PAT/App key from `/run/secrets/gh-token`; `POST` to the
   registration-token endpoint (repo or org, inferred from `RUNNER_URL`).
7. **Clear the local registration.** Remove `.runner`, `.credentials` and
   `.credentials_rsaparams`. See below — this is the common path, not crash
   recovery.
8. `config.sh --unattended --ephemeral --disableupdate --replace \
   --url $RUNNER_URL --token <minted> --name $RUNNER_NAME \
   --labels $RUNNER_LABELS --work $RUNNER_ROOT/_work`
9. `exec run.sh`
10. Runner takes one job, exits. Quadlet's `--rm` destroys the container.
   `Restart=always` starts a fresh one.

**Both halves of "already registered" need handling, and `--replace` is only
one of them.** It settles the *server* side: GitHub still lists a runner under
this name, and without it the next start fails on the collision and wedges the
instance. It does nothing about *local* state, and `config.sh` refuses outright
when it finds one:

```
Cannot configure the runner because it is already configured.
```

Since the state directory persists across container lifetimes by design (§5)
and an ephemeral runner reconfigures on every start, a stale `.runner` is the
common path rather than crash recovery. Step 7 removes `.runner`,
`.credentials` and `.credentials_rsaparams` before configuring. `config.sh
remove` is the sanctioned route and is wrong here — it wants a removal token
minted over the network to undo a registration about to be replaced anyway.

`RUNNER_NAME` defaults to `<hostname>-<id>`, which is unique per host and per
instance. Two instances sharing a name would `--replace` each other on every
job and produce an unwinnable registration fight, so `ctl doctor` checks for
duplicates across `instances.d/`.

---

## 10. Scriptlets

**`%pre`** — allocate a fixed uid/gid (needed for
`/etc/containers/systemd/users/<uid>/`), create the account with an explicit,
non-overlapping subuid/subgid block. Home is `/var/lib/gh-runner`.

**`%post`**
- `loginctl enable-linger gh-runner`
- Wait for the user manager. `enable-linger` returns before
  `user@<uid>.service` is up, so the `daemon-reload` below is a race on a fast
  install. Poll for `/run/user/<uid>/bus`, a few seconds, then proceed
  best-effort — `ctl sync` is the operation that must converge, `%post` is
  merely a head start.
- SELinux contexts, both trees:
  ```
  semanage fcontext -a -t container_file_t \
      '/var/lib/gh-runner/[^/]+(/.*)?'
  semanage fcontext -a -t container_ro_file_t \
      '/usr/lib/gh-runner(/.*)?'
  restorecon -R /var/lib/gh-runner /usr/lib/gh-runner
  ```
- symlink the **Quadlet** units into `/etc/containers/systemd/users/<uid>/`.
  The maintenance timers are already installed in `/usr/lib/systemd/user/` and
  need no symlink (§3).
- `systemctl --user -M gh-runner@ enable --now podman.socket`
- `systemctl --user -M gh-runner@ daemon-reload`
- Do **not** auto-enable instances. Print a pointer to `gh-runner-ctl`.

**Why the fcontext rules are shaped this way.** The earlier form labelled only
`_work` and `externals`, but the mount is the whole instance directory, so
`.runner`, `.credentials`, `_diag/`, and the synced `bin/` were left unlabelled
and denied. `/usr/lib/gh-runner` needs its own rule because it is `usr_t` by
default and a container cannot read it.

**The runner container runs with SELinux label confinement disabled**
(`SecurityLabelDisable=true`), and relabelling is not an alternative. A unix
socket is checked twice: the file, then `unix_stream_socket connectto` against
the *listening process's* label. `:z` on the mount satisfies the first —
`Permission denied` on `stat` becomes a readable socket — and cannot touch the
second, because the peer is podman itself and there is no object to relabel.
`connect()` still fails.

This costs nothing that is not already given away: the runner holds the host
Podman socket, so it can create a container mounting `/` regardless. §1 states
that it is not a security boundary and the VM is. Job containers are
unaffected — siblings created by the host engine, still fully confined — which
is why the `_work` fcontext rules below still matter.

**Do not "fix" the state mount with `:Z` on the volume.** It is the obvious move and it
breaks the design: `:Z` relabels with the runner container's private MCS
category, after which the *sibling* job containers — which have their own
categories — cannot read `_work`, and every containerised job fails on a
permission error pointing at a directory that visibly exists and visibly has
the right owner. The shared labels above are correct precisely because job
containers must reach the same paths (§5). `ctl doctor` flags a `:Z` that has
found its way into a drop-in.

**`%preun`** — on removal (not upgrade): `gh-runner-ctl disable --all --now`,
best-effort deregistration (needs network + credential; log and continue on
failure).

**`%postun`** — on removal:
- `semanage fcontext -d` for both rules
- remove the Quadlet symlinks from `/etc/containers/systemd/users/<uid>/` and
  the generated `*.container.d/` drop-ins — all derived state, safe to delete,
  and dangling symlinks left behind make the next install's `doctor` output
  actively misleading
- `systemctl --user -M gh-runner@ daemon-reload` while the account still exists
- disable linger
- leave `/var/lib/gh-runner`, `/etc/gh-runner`, and the service account in
  place for inspection. The credential file goes with them; it is `0600` and
  removing it silently on an upgrade-gone-wrong is worse than leaving it.

---

## 11. Known risks

Accepted, with the mitigation named. Nothing here is an open question.

| Risk | Notes |
|---|---|
| **PAT blast radius** | Ephemeral registration needs a live credential on the box, readable by anything running a job. Scope to `administration: write` on the single repo, or use a GitHub App. Never a classic `repo` PAT. The image carries `openssl` (§6) so the App path needs no rebuild. |
| **`--disableupdate` + version floor** | GitHub rejects runners that fall too far behind, and does so all at once across every instance. Mitigated by `gh-runner-version-check.timer` (§8), which only warns — shipping the new RPM remains a standing human obligation. |
| **Image staleness** | `RemainAfterExit=yes` means the build runs once. The ephemeral loop makes everything *look* fresh while baked-in apt packages age. Mitigated by `gh-runner-image-refresh.timer` (§8). |
| **First-start latency** | Cold start builds the image: minutes, needs egress. Will look hung. `ctl enable` prints the expected wait and `ctl status` distinguishes *building* from *starting*. |
| **No inter-instance isolation** | All instances share one Podman store and one uid. Instance 2 can inspect instance 3's job containers. Acceptable only because all instances are one trust domain. Put this in the README. |
| **Workspace is not ephemeral** | §5.1. The container is fresh per job; `_actions` and `_tool` are not. A compromised job can poison the toolcache for later jobs *on the same instance*. Inside one trust domain this is the same exposure as the shared Podman store; it is listed because the word "ephemeral" invites a stronger reading. |
| **Storage growth** | Shared image cache is the upside of a persistent host; unbounded growth is the downside. `gh-runner-prune.timer` covers job containers, dangling images, and `_diag`. It does **not** bound `_work/_tool`, which grows with the variety of toolchain versions the workflows ask for. Watch `podman system df` and the instance dirs. |
| **Quadlet debuggability** | Malformed keys fail silently, as do non-Quadlet files in the Quadlet directory (§3). `/usr/libexec/podman/quadlet -dryrun -user` is wired into `gh-runner-ctl doctor`. |
| **podman-remote version skew** | The in-image client and the host engine are separate packages on separate release cadences, and a mismatch would surface as odd API errors mid-job. In practice Ubuntu's `podman-remote` talks to Podman 5.8.2 without complaint, so the package list carries no pin. `ctl doctor` reports both versions. |
| **SELinux confinement of the runner** | The runner container runs with `SecurityLabelDisable=true`, because `connectto` on the engine's socket cannot be reached by relabelling (§10). It gives up nothing the socket had not already given away, but it does mean the runner is unconfined and job containers are not — a distinction worth remembering when reading an AVC. |

---

## 12. As built

Everything in this document is implemented and running. Both job shapes have
been exercised on an AlmaLinux 10 host: jobs with a `container:` block, which
drive the shim into creating sibling containers through the host engine, and
plain `runs-on` jobs, which execute inside the runner container itself.

The three things that could have invalidated the design, and did not:

| Question | Outcome |
|---|---|
| Can a client inside the runner reach the host engine over the mounted socket? | Yes. `podman-remote` from Ubuntu's archive, against Podman 5.8.2, no version skew. |
| Does the shim rewrite what `ContainerOperationProvider` actually emits? | Yes. Containerised jobs run; the socket mount is stripped and job containers are labelled. |
| Does the identical-path invariant hold for sibling containers? | Yes. `_work` paths resolve identically on both sides. |

What the bring-up cost, and what it says about the design: almost every failure
was a silent one. Quadlet ignoring non-Quadlet files, `%post` swallowing errors
with `|| :`, `systemctl enable` refusing generated units, SELinux denying a
`connectto` after the file check had already passed, `config.sh` refusing a
stale `.runner`, an excluded file that turned out to be read unconditionally.
None announced itself; each was found by reading state rather than logs. That
is why `doctor` exists, why the entrypoint preflights before it mints a token,
and why `status` reports jobs alongside restarts.

Ongoing obligations, all automated but none self-correcting:

- `gh-runner-version-check.timer` warns as the pinned runner falls behind. It
  only reports; shipping a new package is `make upgrade-runner` and a human
  decision (§11).
- `gh-runner-image-refresh.timer` rebuilds weekly, so the baked toolchain does
  not age behind an ephemeral loop that makes everything look fresh.
- `gh-runner-prune.timer` reaps job leftovers by age.

The remaining known gap is `register.sh`'s GitHub App path: `openssl` is in the
image for the JWT exchange, but only PAT authentication is implemented.
