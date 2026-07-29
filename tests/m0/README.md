# M0 — the milestone that can invalidate the design

Run this before building anything else. It must prove the *whole* deviation,
not just the path invariant:

1. A `podman-remote` inside the runner container, talking to the host engine
   over the mounted socket.
2. The shim stripping the compiled-in `-v /var/run/docker.sock` that
   `ContainerOperationProvider` emits.
3. The identical-path mount, with one job using a `container:` block
   succeeding end to end.

If that chain does not work, design §5 and §6 are both wrong and nothing
downstream is worth building.

## Capture the argv

The shim's golden tests currently run against hand-written fixtures, which
prove it is self-consistent and nothing more. While M0 runs, wrap the shim to
log its raw argv:

    exec 3>>/var/lib/gh-runner/00/_diag/shim-argv.log
    printf '%s\0' "$@" >&3

Run a job with a `container:` block *and* a service container, then convert
each captured line into `tests/shim/argv/<name>.captured.in` with its
`.expect`. Only those prove the shim rewrites what the runner actually emits.

Watch particularly for:

- `docker version --format '{{.Server.APIVersion}}'` at startup. Podman reports
  its own version rather than a Docker API version like 1.41, and the runner
  may reject it. If it does, the shim needs to intercept `version`.
- Global flags before the verb. The parser reads the subcommand chain only
  from leading positionals and passes through unmodified if the first token is
  a flag.
- `--mount` rather than `-v`, and any socket path spelling we do not handle.
