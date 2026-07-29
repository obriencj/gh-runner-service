#!/bin/bash
#
# Build the RPM inside a clean EL container.
#
# Runs with the repository bind-mounted read-only at /src and an output
# directory at /out. Nothing is written to the source tree, so this cannot
# leave build residue behind or pick up a stale artefact from a previous run.
#
# Everything the package build needs is resolved here from the spec. The host
# needs nothing but podman.

set -euo pipefail

SRC=${SRC:-/src}
OUT=${OUT:-/out}
TOP=/work/rpmbuild
SPEC_NAME=${SPEC_NAME:-gh-runner.spec}

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "environment"
cat /etc/os-release | sed -n 's/^PRETTY_NAME=//p'
echo "rpmbuild: $(rpmbuild --version)"
echo "target:   $(rpm --eval '%{_target_cpu}')"

# ---------------------------------------------------------------- sources ---
say "assembling sources"
mkdir -p "$TOP"/{SOURCES,SPECS,BUILD,BUILDROOT,RPMS,SRPMS}

# The mount is owned by a different uid than the one we run as.
git config --global --add safe.directory "$SRC"

VERSION=$(awk '/^Version:/ {print $2}' "$SRC/$SPEC_NAME")
NAME=$(awk '/^Name:/ {print $2}' "$SRC/$SPEC_NAME")
RUNNER_VERSION=$(awk '/^%global[ \t]+runner_version/ {print $3}' "$SRC/$SPEC_NAME")
RUNNER_SHA256=$(awk '/^%global[ \t]+runner_sha256/  {print $3}' "$SRC/$SPEC_NAME")
RUNNER_ARCH=$(awk '/^%global[ \t]+runner_arch/    {print $3}' "$SRC/$SPEC_NAME")

RUNNER_TARBALL="actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
RUNNER_URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${RUNNER_TARBALL}"

echo "  ${NAME} ${VERSION}, runner pin ${RUNNER_VERSION}"

# Source0: this project, straight out of git so the tarball matches a commit
# rather than whatever happens to be in the working tree.
git -C "$SRC" archive --format=tar.gz \
    --prefix="${NAME}-${VERSION}/" \
    -o "$TOP/SOURCES/${NAME}-${VERSION}.tar.gz" HEAD

# Source1: the upstream release bundle, verified before it is ever unpacked.
# A cached copy under /cache survives between runs; 216MB is not worth
# re-downloading on every iteration.
if [ -f "/cache/$RUNNER_TARBALL" ]; then
    echo "  using cached $RUNNER_TARBALL"
    cp "/cache/$RUNNER_TARBALL" "$TOP/SOURCES/"
else
    echo "  fetching $RUNNER_URL"
    curl -fL --retry 3 -o "$TOP/SOURCES/$RUNNER_TARBALL" "$RUNNER_URL"
    [ -d /cache ] && cp "$TOP/SOURCES/$RUNNER_TARBALL" /cache/ || true
fi

echo "${RUNNER_SHA256}  $TOP/SOURCES/${RUNNER_TARBALL}" | sha256sum -c -

cp "$SRC/$SPEC_NAME" "$TOP/SPECS/"
if compgen -G "$SRC/patches/*.patch" >/dev/null; then
    cp "$SRC"/patches/*.patch "$TOP/SOURCES/"
fi

# ------------------------------------------------------------ buildrequires --
say "resolving BuildRequires"
RPMOPTS=(--define "_topdir $TOP")

# Static BuildRequires first.
dnf -y builddep --spec "$TOP/SPECS/$SPEC_NAME"

# Then the dynamic ones. %generate_buildrequires means the full set is not
# knowable from the spec text alone: rpmbuild -br runs the generator, exits 11
# when something it emitted is unmet, and leaves a .buildreqs.nosrc.rpm saying
# what. Repeat until it stops asking.
# Note: each rpmbuild invocation re-runs %prep, which unpacks a 216MB tarball
# into a 666MB tree. Two invocations is the normal path (-br to learn the
# dynamic requirements, then -ba to build), so expect that cost twice. Nothing
# here is backgrounded and nothing sleeps — if it looks stalled, it is doing
# that extraction.
for attempt in 1 2 3; do
    rm -f "$TOP"/SRPMS/*.buildreqs.nosrc.rpm
    echo "  round ${attempt}: rpmbuild -br (runs %prep; this is the slow part)"
    set +e
    # Streamed, not captured. A silent multi-minute phase is indistinguishable
    # from a hang, and guessing which one you are looking at is not a thing to
    # ask of whoever runs this.
    rpmbuild "${RPMOPTS[@]}" -br "$TOP/SPECS/$SPEC_NAME" 2>&1 | sed 's/^/    /'
    rc=${PIPESTATUS[0]}
    set -e
    if [ $rc -eq 0 ]; then
        echo "  all BuildRequires satisfied"
        break
    fi
    if [ $rc -ne 11 ]; then
        echo "  rpmbuild -br failed ($rc)" >&2
        exit $rc
    fi
    nosrc=$(ls "$TOP"/SRPMS/*.buildreqs.nosrc.rpm 2>/dev/null | head -1) || true
    if [ -z "${nosrc:-}" ]; then
        echo "  rpmbuild wants more BuildRequires but produced no manifest" >&2
        cat /tmp/br.log >&2
        exit 1
    fi
    echo "  round ${attempt}: installing dynamic BuildRequires"
    dnf -y builddep "$nosrc"
done

# -------------------------------------------------------------------- build --
say "building"
rpmbuild "${RPMOPTS[@]}" -ba "$TOP/SPECS/$SPEC_NAME"

# ------------------------------------------------------------------- output --
say "results"
mkdir -p "$OUT"
find "$TOP/RPMS" "$TOP/SRPMS" -name '*.rpm' ! -name '*.nosrc.rpm' -print0 \
    | xargs -0 -I{} cp -v {} "$OUT/"

for r in "$OUT"/*.rpm; do
    printf '\n\033[1m-- %s\033[0m\n' "$(basename "$r")"
    rpm -qip "$r" 2>/dev/null | sed -n '1,12p'
done

say "file list"
for r in "$OUT"/*.rpm; do
    case "$r" in *.src.rpm) continue ;; esac
    rpm -qlpv "$r"
done
