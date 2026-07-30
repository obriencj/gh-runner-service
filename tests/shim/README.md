# Shim fixtures

`run.sh` drives `container/context/docker` through the cases in `argv/`, with a
stub standing in for `podman-remote` so each case is a pure argv-in/argv-out
comparison. It is the highest-risk code in the project: everything about
containerised jobs depends on it rewriting what the runner emits, and the
runner emits it from compiled code we cannot read.

Every fixture here is **hand-written**. Containerised jobs have run against
them unchanged, which proves the shim handles what this workload emits — not
that the fixtures reproduce it. Those are different claims and the difference
matters when something new breaks.

So: when the runner turns out to emit a form that is not represented here, add
it as a fixture *before* changing the shim. To see what it actually emits, wrap
the shim to record its arguments:

    exec 3>>/var/lib/gh-runner/01/_diag/shim-argv.log
    printf '%s\0' "$@" >&3

Run a job with a `container:` block and a service container, then turn each
captured line into `argv/<name>.captured.in` with its `.expect`. A `.env` file
beside a fixture sets per-case environment — `JOB_MEMORY_MAX`, `RUNNER_ID`,
`GH_RUNNER_SHIM_DROP_SOCKET` — so one case exercising job limits cannot change
what every other case expects.

## What is covered

- the compiled-in `-v /var/run/docker.sock` mount is stripped, in both
  `-v x:y` and `--volume=x:y` forms
- other volumes pass through untouched
- job containers and networks are labelled for the pruner
- `JOB_MEMORY_MAX` / `JOB_CPUS` become `--memory` / `--cpus`
- `network create` injects after the subcommand, not after a flag's value
- a flag's value is never mistaken for a verb
- everything else passes through unmodified
