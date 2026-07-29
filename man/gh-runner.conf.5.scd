gh-runner.conf(5)

# NAME

gh-runner.conf - configuration for gh-runner instances

# SYNOPSIS

/etc/gh-runner/gh-runner.conf++
/etc/gh-runner/instances.d/_id_.conf

# DESCRIPTION

One file per runner instance under *instances.d/*, plus a global defaults
file. The global file is loaded first and the instance file wins.

The instance _id_ is the filename stem, and is used consistently as the
systemd instance (*gh-runner@*_id_*.service*), the container name
(*gh-runner-*_id_), the state directory (*/var/lib/gh-runner/*_id_*/*), and
the default runner name (_hostname_*-*_id_).

Dropping a file here does *not* start a runner. Activation is a separate step
so that configuration management and activation stay separable. See
*gh-runner-ctl*(8).

# FILE FORMAT

_KEY_*=*_value_, one per line. Lines beginning with *#* and blank lines are
ignored.

The format is *Podman's --env-file*, not systemd's *EnvironmentFile*. Quadlet
routes *EnvironmentFile=* to *podman --env-file*, and the two are similar
enough to be confused and different enough to bite:

- No quote removal. *RUNNER_NAME="build box"* produces a runner name that
  includes the quote characters literally.
- No escape processing.
- No variable expansion.
- A bare name with no *=* means "inherit from the environment" to Podman.
  Nothing sensible can inherit into a container started by systemd, so
  *gh-runner-ctl* rejects it rather than dropping it silently.

Leave values unquoted and free of leading or trailing whitespace.
*gh-runner-ctl show* warns about both.

# KEY CLASSES

Three classes of key live in one file, with three different destinations.
This is not cosmetic: a key placed in the wrong class silently does nothing.

## Runtime env

Passed into the runner container and read by its entrypoint. Take effect at
the next container start, i.e. at the next job.

*RUNNER_URL*
	Repository or organisation URL. Required.

*RUNNER_LABELS*
	Comma-separated labels. Default *alma10,podman*.

*RUNNER_NAME*
	Default _hostname_*-*_id_. Must be unique across the host; two instances
	sharing a name replace each other on every job.

*RUNNER_GROUP*
	Runner group. Optional.

*RUNNER_WIPE_WORK*
	*1* to remove *_work/_actions* and *_work/_tool* between jobs as well as
	the checkout. Default *0*, which keeps the caches — see *EPHEMERALITY*.

## Job-shaping

Passed into the container as env and read by the *docker* shim, which applies
them to each job container it creates.

*JOB_MEMORY_MAX*
	Memory ceiling for job containers, e.g. *12G*.

*JOB_CPUS*
	CPU count for job containers, e.g. *4*.

## Unit-shaping

Routed to a generated systemd drop-in at
*/etc/systemd/user/gh-runner@*_id_*.service.d/limits.conf* by
*gh-runner-ctl sync*. Require *daemon-reload* and a restart.

*MEMORY_MAX*, *MEMORY_HIGH*, *CPU_WEIGHT*, *CPU_QUOTA*, *TASKS_MAX*, *IO_WEIGHT*
	Mapped to the systemd unit properties of the corresponding name.

Quadlet fixes these at generation time, so an *EnvironmentFile* cannot reach
them. Without the generated drop-in they silently do nothing.

# THE MEMORY_MAX TRAP

*MEMORY_MAX* constrains the runner *unit*, which contains only the listener
process. Job containers are created by the host engine as siblings and land
in their own cgroup scope, inheriting nothing from the runner unit.

So *MEMORY_MAX=12G* limits the wrong process, and a compiler running in a
*container:* job still eats the host. Worse, the value looks applied:
*systemctl show* reports it faithfully.

Use *JOB_MEMORY_MAX* to bound containerised jobs and *MEMORY_MAX* to bound the
runner itself and any job that runs *without* a *container:* block. Both are
real; they cover different job shapes. Set both.

Enforcement also needs cgroup delegation of the *memory* and *cpu*
controllers. *gh-runner-ctl doctor* checks this, because the failure mode is
otherwise a limit that is configured, reported, and not enforced.

# EPHEMERALITY

The runner container is destroyed after every job, but the instance state
directory is a persistent host bind mount and cannot be otherwise: the host
engine resolves the source side of every volume the runner emits, so those
paths must exist identically on both sides.

Per job, *_work/*_repo_ and *_work/_temp* are removed; *_work/_actions* and
*_work/_tool* are kept, because rebuilding the toolcache every job is most of
the cold-start cost this package exists to avoid. Set *RUNNER_WIPE_WORK=1* for
an instance that wants maximum isolation and will pay for it.

# EXAMPLE

```
# /etc/gh-runner/instances.d/01.conf

RUNNER_URL=https://github.com/koskari-lang/koskari
RUNNER_LABELS=alma10,podman,c

JOB_MEMORY_MAX=12G
JOB_CPUS=4

MEMORY_MAX=2G
CPU_WEIGHT=100
```

# SEE ALSO

*gh-runner-ctl*(8), *podman-run*(1), *quadlet*(5), *systemd.resource-control*(5)
