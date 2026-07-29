#!/bin/bash
#
# Build the RPM inside a clean EL container.
#
# Runs with the repository bind-mounted read-only at /src and an output
# directory at /out. Nothing is written to the source tree, so this cannot
# leave build residue behind or pick up a stale artefact from a previous run.
#
# DELIBERATELY BORING. No pipes, no redirects, no background jobs, no sleeps,
# no retries, no output capture. Every command runs in the foreground and
# writes straight to the terminal, in order. If this script appears to stall,
# the last line printed is genuinely what it is doing -- there is nowhere for
# output to be hiding.
#
# The slow steps, so a pause is never a mystery:
#   - fetching the runner bundle (216MB, once; cached in /cache afterwards)
#   - each rpmbuild invocation re-runs %prep, which unpacks that bundle into a
#     666MB tree. Expect that twice: once for -br, once for -ba.

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

# ---------------------------------------------------------------- sources ---
say "assembling sources"
mkdir -p "$TOP/SOURCES" "$TOP/SPECS" "$TOP/BUILD" "$TOP/BUILDROOT"
mkdir -p "$TOP/RPMS" "$TOP/SRPMS" "$OUT"

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

# ----------------------------------------------------------- buildrequires ---
# An array, so the value keeps its space instead of splitting into
# "--define=_topdir" plus a stray path argument.
RPMOPTS=(--define "_topdir $TOP")

say "resolving static BuildRequires"
dnf -y builddep --spec "$TOP/SPECS/$SPEC_NAME"

# %generate_buildrequires means the full set is not knowable from the spec
# text alone. rpmbuild -br runs the generator, exits 11 when something it
# emitted is unmet, and leaves a .buildreqs.nosrc.rpm saying what. Repeat
# until it stops asking.
attempt=1
while [ "$attempt" -le 3 ]; do
    for stale in "$TOP"/SRPMS/*.buildreqs.nosrc.rpm; do
        if [ -f "$stale" ]; then
            rm -f "$stale"
        fi
    done

    say "resolving dynamic BuildRequires (round ${attempt})"
    echo "  rpmbuild -br runs %prep, which unpacks the 666MB runner tree."
    echo "  This takes a while and is not stuck."

    if rpmbuild "${RPMOPTS[@]}" -br "$TOP/SPECS/$SPEC_NAME"; then
        rc=0
    else
        rc=$?
    fi

    if [ "$rc" -eq 0 ]; then
        echo "  all BuildRequires satisfied"
        break
    fi

    if [ "$rc" -ne 11 ]; then
        echo "rpmbuild -br failed with status ${rc}" >&2
        exit "$rc"
    fi

    NOSRC=""
    for candidate in "$TOP"/SRPMS/*.buildreqs.nosrc.rpm; do
        if [ -f "$candidate" ]; then
            NOSRC="$candidate"
        fi
    done
    if [ -z "$NOSRC" ]; then
        echo "rpmbuild wants more BuildRequires but produced no manifest" >&2
        exit 1
    fi

    dnf -y builddep "$NOSRC"
    attempt=$((attempt + 1))
done

# -------------------------------------------------------------------- build --
say "building"
echo "  %prep runs again here; same 666MB unpack as above."
if [ -n "$RPM_TARGET" ]; then
    rpmbuild "${RPMOPTS[@]}" --target "$RPM_TARGET" -ba "$TOP/SPECS/$SPEC_NAME"
else
    rpmbuild "${RPMOPTS[@]}" -ba "$TOP/SPECS/$SPEC_NAME"
fi

# ------------------------------------------------------------------- output --
say "collecting"
for f in "$TOP"/RPMS/*/*.rpm "$TOP"/SRPMS/*.rpm; do
    if [ ! -f "$f" ]; then
        continue
    fi
    case "$f" in
        *.nosrc.rpm) continue ;;
    esac
    cp -v "$f" "$OUT/"
done

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
