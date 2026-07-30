#!/bin/bash
#
# Runner container entrypoint. Runs once per container start, i.e. once per
# job. Design §9.

set -euo pipefail

DRAIN_EXIT=78

: "${RUNNER_ID:?RUNNER_ID not set — check EnvironmentFile in the quadlet}"
: "${RUNNER_ROOT:=/var/lib/gh-runner/${RUNNER_ID}}"
: "${RUNNER_URL:?RUNNER_URL not set — check /etc/gh-runner/instances.d/${RUNNER_ID}.conf}"
: "${RUNNER_LABELS:=alma10,podman}"
: "${RUNNER_NAME:=$(hostname)-${RUNNER_ID}}"
: "${RUNNER_WIPE_WORK:=0}"

TEMPLATE=/usr/lib/gh-runner/current
SOCK=/var/run/docker.sock

log() { printf '[entrypoint %s] %s\n' "$RUNNER_ID" "$*" >&2; }

# 1. Drain check. First, so a drained instance never mints a token it will
#    not use. RestartPreventExitStatus=78 stops the restart loop here.
if [[ -f "${RUNNER_ROOT}/.drain" ]]; then
    log "drain marker present; stopping at job boundary"
    exit "$DRAIN_EXIT"
fi

# 2. (env already in the environment via EnvironmentFile=)

# 3. Sync the pristine template into the instance state dir when the version
#    marker differs. The *whole* tree, wrappers included: config.sh and run.sh
#    resolve their own directory and write .runner, .credentials and _diag
#    relative to it, so running them from the read-only /usr/lib mount would
#    fail. One-time per version per instance, not per job.
mkdir -p "$RUNNER_ROOT"
want_version=$(cat "${TEMPLATE}/.version")
have_version=$(cat "${RUNNER_ROOT}/.version" 2>/dev/null || echo "")

if [[ "$want_version" != "$have_version" ]]; then
    log "syncing runner ${have_version:-<none>} -> ${want_version}"
    cp -a "${TEMPLATE}/." "${RUNNER_ROOT}/"
    echo "$want_version" > "${RUNNER_ROOT}/.version"
fi

# 4. Workspace hygiene. The instance state dir is a persistent host bind
#    mount — it has to be, for the identical-path invariant (§5) — so "fresh
#    per job" applies to the container, not to _work. Job checkouts and
#    scratch go; the toolcache and action checkouts stay, because rebuilding
#    them every job is most of the cold-start cost this design exists to
#    avoid. See §5.1.
WORK="${RUNNER_ROOT}/_work"
mkdir -p "$WORK"

if [[ "$RUNNER_WIPE_WORK" == "1" ]]; then
    log "RUNNER_WIPE_WORK=1: removing the entire work tree"
    rm -rf "${WORK:?}"/*
else
    rm -rf "${WORK:?}/_temp"
    for d in "$WORK"/*; do
        [[ -d "$d" ]] || continue
        case "$(basename "$d")" in
            _actions|_tool) continue ;;
            *) rm -rf "$d" ;;
        esac
    done
fi
mkdir -p "${WORK}/_temp" "${WORK}/_actions" "${WORK}/_tool"

# 5. Preflight. Fail here, loudly, rather than sixty seconds later inside a
#    job step where it surfaces as an inscrutable `docker: command failed` in
#    a workflow log the operator may not be able to see.
#    Show what is actually there rather than asserting a cause. `[[ ! -S ]]`
#    is equally false for a missing path, a directory, and a socket whose
#    stat() was denied by SELinux — an earlier version blamed podman.socket
#    unconditionally and sent the operator after the wrong thing twice.
if [[ ! -S "$SOCK" ]]; then
    log "FATAL: ${SOCK} is not a usable socket."
    log "what is actually at that path, from inside the container:"
    ls -ldZ "$SOCK" >&2 2>/dev/null || ls -ld "$SOCK" >&2 2>/dev/null \
        || log "  (nothing — the path does not exist or cannot be stat'ed)"
    stat -c '  type=%F mode=%A owner=%U:%G' "$SOCK" >&2 2>/dev/null || true
    log "the mount source on the host is \$XDG_RUNTIME_DIR/podman/podman.sock;"
    log "the three things that produce this:"
    log "  missing    - podman.socket not running; podman then creates a"
    log "               DIRECTORY at the source, which shows as type=directory"
    log "  directory  - that leftover; remove it on the host, restart the socket"
    log "  denied     - socket exists on the host but SELinux denies stat here;"
    log "               check for AVCs and see design §10 on labelling"
    exit 1
fi

if [[ ! -w "$SOCK" ]]; then
    log "FATAL: ${SOCK} is a socket but not writable by this container."
    ls -ldZ "$SOCK" >&2 2>/dev/null || ls -ld "$SOCK" >&2 2>/dev/null || true
    log "Usually SELinux. Check the host for an AVC denial naming this socket."
    exit 1
fi

if ! podman-remote info >/dev/null 2>&1; then
    log "FATAL: cannot reach the host podman engine over ${SOCK}"
    podman-remote info >&2 || true
    exit 1
fi
log "engine ok: $(podman-remote version --format '{{.Server.Version}}' 2>/dev/null || echo unknown)"

# 6. Mint a registration token. Ephemeral runners re-register every start.
TOKEN=$(/usr/local/bin/register.sh)

# 7. Clear the local registration.
#
#    An ephemeral runner re-registers on every start, so a .runner written by
#    the previous container is always stale. config.sh refuses rather than
#    overwriting it:
#
#      Cannot configure the runner because it is already configured. To
#      reconfigure the runner, run './config.sh remove' first.
#
#    --replace does not help here: it settles a *server-side* collision, where
#    GitHub still lists a runner under this name, and never touches local
#    state. Both halves are needed, and the state directory persists across
#    container lifetimes by design (§5), so this is the common path rather
#    than crash recovery.
#
#    `config.sh remove` is the sanctioned route and is wrong for us: it needs
#    a removal token minted over the network to undo a registration that is
#    about to be replaced anyway.
rm -f "$RUNNER_ROOT"/.runner \
      "$RUNNER_ROOT"/.credentials \
      "$RUNNER_ROOT"/.credentials_rsaparams

# 8. Configure. --replace settles the server side: an unclean exit leaves a
#    registration behind, and without it the next start fails on a name
#    collision and wedges the instance.
cd "$RUNNER_ROOT"
./config.sh \
    --unattended \
    --ephemeral \
    --disableupdate \
    --replace \
    --url "$RUNNER_URL" \
    --token "$TOKEN" \
    --name "$RUNNER_NAME" \
    --labels "$RUNNER_LABELS" \
    ${RUNNER_GROUP:+--runnergroup "$RUNNER_GROUP"} \
    --work "$WORK"

# 9. One job, then exit. --rm destroys the container; Restart=always brings up
#    a fresh one.
log "registered as ${RUNNER_NAME}; waiting for a job"
exec ./run.sh
