# Patches

Applied to the extracted upstream tarball in `%prep`. Every patch here is a
standing rebase obligation against each upstream release — `make
upgrade-runner` dry-runs the whole set and refuses to move the pin if any of
them no longer applies.

Keep the set minimal. Upstream ships a compiled .NET payload plus shell
wrappers; only the wrappers are patchable, and everything meaningful in
container orchestration is inside `Runner.Worker` assemblies.

## Deliberately not patched

- **Root guard in `config.sh` / `run.sh`** — handled by
  `ENV RUNNER_ALLOW_RUNASROOT=1` in the Containerfile. One line, no rebase cost.
- **The hardcoded `-v /var/run/docker.sock` mount** — lives in
  `ContainerOperationProvider`, compiled. Handled by the PATH shim in the
  image. This is the deviation that motivates the whole design.
