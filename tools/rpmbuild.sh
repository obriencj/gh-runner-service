#!/bin/bash
#
# Build the RPM inside a clean EL container.
#
# Runs with the repository bind-mounted read-only at /src and an output
# directory at /out. Nothing is written to the source tree, so this cannot
# leave build residue behind or pick up a stale artefact from a previous run.
#
# DELIBERATELY BORING. No pipes, no redirects, no background jobs, no sleeps,
# no retries, no output capture, and nothing is installed here. Every command
# runs in the foreground and writes straight to the terminal, in order. If
# this script appears to stall, the last line printed is genuinely what it is
# doing -- there is nowhere for output to be hiding.
#
# Build dependencies live in tools/Containerfile.rpmbuild and are baked into
# the image. This script only assembles sources and calls rpmbuild once.
#
# The two slow steps, so a pause is never a mystery:
#   - fetching the runner bundle (216MB, once; cached in /cache afterwards)
#   - %prep, which unpacks that bundle into a 666MB tree

set -eu

SRC=${SRC:-/src}
OUT=${OUT:-/out}
CACHE=${CACHE:-/cache}
TOP=/work/rpmbuild
SPEC_NAME=${SPEC_NAME:-gh-runner.spec}
RPM_TARGET=${RPM_TARGET:-}

say() {
    printf '\n== %s\n' "$*"
}

say "environment"
. /etc/os-release
echo "  base:     ${PRETTY_NAME}"
echo "  rpmbuild: $(rpmbuild --version)"
echo "  native:   $(rpm --eval '%{_target_cpu}')"
if [ -n "$RPM_TARGET" ]; then
    echo "  target:   ${RPM_TARGET} (cross-tagged; nothing here is compiled)"
fi

# ----------------------------------------------------------------- output ---
# Proven writable, and proven to be the bind mount, before anything expensive
# happens. If /out is not actually mounted, everything still "succeeds" and
# then vanishes with the container — which is a very annoying way to spend an
# afternoon.
say "checking the output mount"
mkdir -p "$OUT"
if grep -q " ${OUT} " /proc/self/mounts; then
    echo "  ${OUT} is a mount point"
else
    echo "  WARNING: ${OUT} is NOT a mount point."
    echo "  Anything written here dies with the container. Check the -v flag."
fi
echo "  writing ${OUT}/.rpmbuild-marker"
echo "written by tools/rpmbuild.sh" > "$OUT/.rpmbuild-marker"
echo "  mount entries mentioning ${OUT}:"
grep " ${OUT} " /proc/self/mounts || echo "    (none)"

# ---------------------------------------------------------------- sources ---
say "assembling sources"
mkdir -p "$TOP/SOURCES" "$TOP/SPECS" "$TOP/BUILD" "$TOP/BUILDROOT"
mkdir -p "$TOP/RPMS" "$TOP/SRPMS"

# The mount is owned by a different uid than the one we run as.
git config --global --add safe.directory "$SRC"

SPEC="$SRC/$SPEC_NAME"
NAME=$(awk '/^Name:/ {print $2}' "$SPEC")
VERSION=$(awk '/^Version:/ {print $2}' "$SPEC")
RUNNER_VERSION=$(awk '/^%global[ \t]+runner_version/ {print $3}' "$SPEC")
RUNNER_SHA256=$(awk '/^%global[ \t]+runner_sha256/ {print $3}' "$SPEC")
RUNNER_ARCH=$(awk '/^%global[ \t]+runner_arch/ {print $3}' "$SPEC")

RUNNER_TARBALL="actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
RUNNER_URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${RUNNER_TARBALL}"

echo "  package:  ${NAME} ${VERSION}"
echo "  pin:      runner ${RUNNER_VERSION}"

# Source0: this project, out of git so the tarball matches a commit rather
# than whatever happens to be in the working tree.
echo "  archiving HEAD"
git -C "$SRC" archive --format=tar.gz \
    --prefix="${NAME}-${VERSION}/" \
    --output="$TOP/SOURCES/${NAME}-${VERSION}.tar.gz" \
    HEAD

# Source1: the upstream release bundle.
if [ -f "$CACHE/$RUNNER_TARBALL" ]; then
    echo "  using cached ${RUNNER_TARBALL}"
    cp "$CACHE/$RUNNER_TARBALL" "$TOP/SOURCES/$RUNNER_TARBALL"
else
    echo "  fetching ${RUNNER_URL}"
    echo "  (216MB, and the only step here that touches the network)"
    curl -fL --output "$TOP/SOURCES/$RUNNER_TARBALL" "$RUNNER_URL"
    if [ -d "$CACHE" ]; then
        cp "$TOP/SOURCES/$RUNNER_TARBALL" "$CACHE/$RUNNER_TARBALL"
    fi
fi

# Verified before it is ever unpacked. Compared in the shell rather than piped
# to `sha256sum -c` so the failure prints both values.
echo "  verifying ${RUNNER_TARBALL}"
SUM_LINE=$(sha256sum "$TOP/SOURCES/$RUNNER_TARBALL")
SUM=${SUM_LINE%% *}
if [ "$SUM" != "$RUNNER_SHA256" ]; then
    echo "DIGEST MISMATCH" >&2
    echo "  expected ${RUNNER_SHA256}" >&2
    echo "  got      ${SUM}" >&2
    exit 1
fi
echo "  digest ok"

cp "$SPEC" "$TOP/SPECS/$SPEC_NAME"
for p in "$SRC"/patches/*.patch; do
    if [ -f "$p" ]; then
        echo "  patch $(basename "$p")"
        cp "$p" "$TOP/SOURCES/"
    fi
done

# -------------------------------------------------------------------- build --
# An array, so the value keeps its space instead of splitting into
# "--define=_topdir" plus a stray path argument.
RPMOPTS=(--define "_topdir $TOP")

say "building"
echo "  %prep unpacks the 216MB bundle into a 666MB tree. This is the slow"
echo "  part, it runs exactly once, and it is not stuck."
echo
echo "  Build dependencies come from the image, not from here -- if rpmbuild"
echo "  reports 'Failed build dependencies', add the package to"
echo "  tools/Containerfile.rpmbuild and re-run 'make builder-image'."

if [ -n "$RPM_TARGET" ]; then
    rpmbuild "${RPMOPTS[@]}" --target "$RPM_TARGET" -ba "$TOP/SPECS/$SPEC_NAME"
else
    rpmbuild "${RPMOPTS[@]}" -ba "$TOP/SPECS/$SPEC_NAME"
fi

# ---------------------------------------------------------------- collect ---
say "where rpmbuild writes"
echo "  _topdir:    $(rpm "${RPMOPTS[@]}" --eval '%{_topdir}')"
echo "  _rpmdir:    $(rpm "${RPMOPTS[@]}" --eval '%{_rpmdir}')"
echo "  _srcrpmdir: $(rpm "${RPMOPTS[@]}" --eval '%{_srcrpmdir}')"
echo "  _target_cpu: $(rpm "${RPMOPTS[@]}" --eval '%{_target_cpu}')"

say "what rpmbuild produced"
echo "  under ${TOP}/RPMS:"
ls -lR "$TOP/RPMS"
echo "  under ${TOP}/SRPMS:"
ls -lR "$TOP/SRPMS"

say "collecting into ${OUT}"
collected=0
for f in "$TOP"/RPMS/*/*.rpm "$TOP"/SRPMS/*.rpm; do
    if [ ! -f "$f" ]; then
        continue
    fi
    case "$f" in
        *.nosrc.rpm) continue ;;
    esac
    cp -v "$f" "$OUT/"
    collected=$((collected + 1))
done

# A build that produces nothing must not exit 0. The previous version skipped
# silently when the globs matched nothing, so "runs to completion, output looks
# sane, no artefacts anywhere" was a passing run.
if [ "$collected" -eq 0 ]; then
    echo
    echo "NO RPMS COLLECTED." >&2
    echo "rpmbuild reported success but left nothing at ${TOP}/RPMS or" >&2
    echo "${TOP}/SRPMS. The listings above show what is actually there." >&2
    exit 1
fi
echo "  collected ${collected} package(s)"

for r in "$OUT"/*.rpm; do
    if [ ! -f "$r" ]; then
        continue
    fi
    case "$r" in
        *.src.rpm) continue ;;
    esac

    say "requires: $(basename "$r")"
    # The check that matters. Any libicu.so / libssl.so soname appearing here
    # means __requires_exclude_from is not matching the vendored tree, and the
    # package would install nowhere despite having built cleanly.
    rpm -qpR "$r"

    say "contents: $(basename "$r")"
    rpm -qlpv "$r"
done

say "done"
ls -lh "$OUT"
