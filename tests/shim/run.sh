#!/bin/bash
#
# Golden-file tests for the docker->podman-remote shim.
#
# The shim's argv rewriting is a pure function of argv, so it is tested as
# one: each tests/shim/argv/<name>.in holds one command line, and the
# matching .expect holds what the shim should exec.
#
# Fixtures ending in .captured.in came off a live runner during M0. Do not
# invent those — capture them. The hand-written ones cover the branches;
# only the captured ones prove we handle what the runner actually emits.

set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
shim="${here}/../../container/context/docker"
argvdir="${here}/argv"

# A stub that prints its argv instead of running anything.
stub="$(mktemp)"
cat > "$stub" <<'EOF'
#!/bin/bash
printf '%s\n' "$@"
EOF
chmod +x "$stub"
trap 'rm -f "$stub"' EXIT

pass=0
fail=0

for in_file in "$argvdir"/*.in; do
    [ -e "$in_file" ] || continue
    name="$(basename "$in_file" .in)"
    expect_file="${argvdir}/${name}.expect"

    if [ ! -e "$expect_file" ]; then
        echo "  MISS ${name}: no .expect file"
        fail=$((fail + 1))
        continue
    fi

    # Per-fixture environment. A <name>.env sits beside the .in and is
    # sourced only for that case, so a fixture that exercises JOB_MEMORY_MAX
    # cannot silently change the expected output of every other fixture.
    env_file="${argvdir}/${name}.env"

    # shellcheck disable=SC2046
    got="$(
        unset JOB_MEMORY_MAX JOB_CPUS GH_RUNNER_SHIM_DROP_SOCKET
        RUNNER_ID=01
        [ -e "$env_file" ] && . "$env_file"
        export RUNNER_ID JOB_MEMORY_MAX JOB_CPUS GH_RUNNER_SHIM_DROP_SOCKET
        GH_RUNNER_PODMAN="$stub" bash "$shim" $(cat "$in_file") 2>&1
    )"

    if [ "$got" == "$(cat "$expect_file")" ]; then
        echo "  ok   ${name}"
        pass=$((pass + 1))
    else
        echo "  FAIL ${name}"
        diff -u "$expect_file" <(printf '%s\n' "$got") | sed 's/^/       /'
        fail=$((fail + 1))
    fi
done

echo
echo "shim: ${pass} passed, ${fail} failed"
[ "$fail" -eq 0 ]
