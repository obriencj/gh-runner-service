#!/bin/bash
#
# Build the SRPM and RPM inside a clean EL container.
#
# /src is the repository, read-only. /out is where packages land. /cache holds
# the upstream bundle between runs.
#
# Build dependencies come from the image (tools/Containerfile.rpmbuild); this
# installs nothing. No pipes, no output capture, no background jobs.

set -eu

SRC=${SRC:-/src}
OUT=${OUT:-/out}
CACHE=${CACHE:-/cache}
TOP=/work/rpmbuild
SPEC_NAME=${SPEC_NAME:-gh-runner.spec}
RPM_TARGET=${RPM_TARGET:-}

SPEC="$SRC/$SPEC_NAME"
NAME=$(awk '/^Name:/ {print $2}' "$SPEC")
VERSION=$(awk '/^Version:/ {print $2}' "$SPEC")
RUNNER_VERSION=$(awk '/^%global[ \t]+runner_version/ {print $3}' "$SPEC")
RUNNER_ARCH=$(awk '/^%global[ \t]+runner_arch/ {print $3}' "$SPEC")

RUNNER_TARBALL="actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
RUNNER_URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${RUNNER_TARBALL}"

mkdir -p "$TOP/SOURCES" "$TOP/SPECS" "$OUT"

# Source0: out of git, so the tarball matches a commit rather than whatever is
# in the working tree.
git config --global --add safe.directory "$SRC"
git -C "$SRC" archive --format=tar.gz \
    --prefix="${NAME}-${VERSION}/" \
    --output="$TOP/SOURCES/${NAME}-${VERSION}.tar.gz" \
    HEAD

# Source1: 216MB, so it is cached between runs. %prep verifies the digest.
if [ -f "$CACHE/$RUNNER_TARBALL" ]; then
    cp "$CACHE/$RUNNER_TARBALL" "$TOP/SOURCES/$RUNNER_TARBALL"
else
    curl -fL --output "$TOP/SOURCES/$RUNNER_TARBALL" "$RUNNER_URL"
    if [ -d "$CACHE" ]; then
        cp "$TOP/SOURCES/$RUNNER_TARBALL" "$CACHE/$RUNNER_TARBALL"
    fi
fi

cp "$SPEC" "$TOP/SPECS/$SPEC_NAME"
for p in "$SRC"/patches/*.patch; do
    if [ -f "$p" ]; then
        cp "$p" "$TOP/SOURCES/"
    fi
done

# --target satisfies ExclusiveArch: x86_64 without emulating an x86-64
# userspace. Nothing here is compiled.
RPMOPTS=(--define "_topdir $TOP")
if [ -n "$RPM_TARGET" ]; then
    RPMOPTS+=(--target "$RPM_TARGET")
fi

rpmbuild "${RPMOPTS[@]}" -ba "$TOP/SPECS/$SPEC_NAME"

# Unguarded on purpose. If a glob matches nothing, cp is handed the literal
# pattern, fails, and set -e stops here — so a build that produced no packages
# cannot exit 0 without any bookkeeping to say so.
cp "$TOP"/RPMS/*/*.rpm "$OUT/"
cp "$TOP"/SRPMS/*.rpm "$OUT/"
