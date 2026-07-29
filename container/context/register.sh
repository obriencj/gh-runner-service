#!/bin/bash
#
# Mint a short-lived registration token from the long-lived credential.
# Writes the token to stdout; everything else goes to stderr.
#
# The credential is mounted as a podman secret at /run/secrets/gh-token and
# never appears in the environment, in argv, or in a workflow log.

set -euo pipefail

SECRET="${GH_TOKEN_FILE:-/run/secrets/gh-token}"
API="${GITHUB_API_URL:-https://api.github.com}"

log() { printf '[register] %s\n' "$*" >&2; }

[[ -r "$SECRET" ]] || {
    log "FATAL: no credential at ${SECRET}"
    log "On the host: gh-runner-ctl set-credential && gh-runner-ctl sync"
    exit 1
}

CRED=$(tr -d '\n' < "$SECRET")

# Infer the registration endpoint from RUNNER_URL. A repo URL has two path
# components, an org URL has one.
path="${RUNNER_URL#*://}"
path="${path#*/}"
path="${path%/}"

case "$path" in
    */*/*)
        log "FATAL: RUNNER_URL has too many path components: ${RUNNER_URL}"
        exit 1
        ;;
    */*)
        endpoint="${API}/repos/${path}/actions/runners/registration-token"
        ;;
    ?*)
        endpoint="${API}/orgs/${path}/actions/runners/registration-token"
        ;;
    *)
        log "FATAL: cannot infer owner from RUNNER_URL: ${RUNNER_URL}"
        exit 1
        ;;
esac

log "minting against ${endpoint}"

response=$(curl -fsSL -X POST \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -H "Authorization: Bearer ${CRED}" \
    "$endpoint") || {
    log "FATAL: registration-token request failed."
    log "Check the credential scope: it needs administration:write on the"
    log "target, not a classic repo PAT."
    exit 1
}

token=$(printf '%s' "$response" | jq -r '.token // empty')
[[ -n "$token" ]] || {
    log "FATAL: no token in the response"
    exit 1
}

printf '%s' "$token"

# TODO (M3): GitHub App support. openssl is in the image for exactly this —
# detect a PEM in $SECRET, sign a JWT, exchange it for an installation token,
# then mint as above.
