# --- upstream pin -----------------------------------------------------------
# These two lines are the only place the upstream version and digest appear.
# The Makefile reads them back rather than restating them; `make upgrade-runner
# V=x.y.z` moves both and dry-runs the patch set before committing to the move.
%global runner_version  2.336.0
%global runner_sha256   04cf0be1aff4c3ec3554466c39124ca250e3effd8873bb7e8d68535aa9505d5d
%global runner_arch     x64
# ----------------------------------------------------------------------------

%global service_user  gh-runner
%global service_uid   987
%global runner_tree   actions-runner-%{runner_version}

# --- the bundled runner tree is a payload, not something we build ------------
#
# /usr/lib/gh-runner/<version> is a vendored, prebuilt .NET tree. RPM's
# automatic machinery must keep its hands off it entirely:
#
# __requires_exclude_from   Without this, the dependency generator scans the
#                           bundled native libraries and emits Requires on
#                           libicu.so.74, libssl.so.3 and friends — the Ubuntu
#                           sonames that exist only inside the runner *image*.
#                           None of them can be satisfied on AlmaLinux, so the
#                           package would build cleanly and then be flatly
#                           uninstallable.
# __provides_exclude_from   Likewise, we must not advertise the bundled .NET
#                           sonames to the rest of the system.
# debug_package / brp_strip Stripping a prebuilt vendor tree corrupts what we
#                           were asked to ship pristine, and there are no
#                           sources for a debuginfo package to point at.
# mangle_shebangs           Upstream's scripts and the bundled node builds
#                           carry their own interpreters; rewriting them is
#                           exactly the kind of local deviation §1 forbids.
# brp_check_rpaths          The .NET binaries carry $ORIGIN RPATHs, which
#                           check-rpaths treats as an error and aborts the
#                           build over. It is the right check for something we
#                           compiled and the wrong one for a vendor payload we
#                           are forbidden from modifying.
%global __requires_exclude_from ^%{_prefix}/lib/%{name}/.*$
%global __provides_exclude_from ^%{_prefix}/lib/%{name}/.*$
%global __brp_mangle_shebangs_exclude_from ^%{_prefix}/lib/%{name}/.*$
%global __brp_check_rpaths %{nil}
%global __brp_strip %{nil}
%global __brp_strip_static_archive %{nil}
%global __brp_strip_comment_note %{nil}

# Disabling debuginfo takes all four. `debug_package %%{nil}` alone only
# suppresses the -debuginfo *subpackage*; find-debuginfo still runs and still
# rewrites the binaries it finds. The extraction step is gated separately, and
# here it would be chewing on Runner.Listener, Runner.Worker,
# Runner.PluginHost and createdump — prebuilt vendor binaries with no matching
# sources, which we are meant to ship byte-for-byte.
%global debug_package %{nil}
%global __debug_package %{nil}
%global __debug_install_post %{nil}
%global _enable_debug_packages 0
%undefine _debugsource_packages
%undefine _debuginfo_subpackages

# No /usr/lib/.build-id links either. With debuginfo off they correlate with
# nothing, and generating them put 43 entries plus the shared .build-id
# directory itself into the package. It also warns on every build: the three
# .NET apphosts (Runner.Listener, Runner.Worker, Runner.PluginHost) are
# identical launcher stubs and legitimately share a build-id, which rpm cannot
# express as two symlinks of the same name.
%global _build_id_links none

# The payload is ~666MB of already-compressed binaries, where maximum
# compression costs minutes and saves very little. Trade a slightly larger
# package for a build that finishes.
%global _binary_payload w3.zstdio

Name:           gh-runner
Version:        0.1.0
Release:        14%{?dist}
Summary:        Rootless, ephemeral GitHub Actions self-hosted runners

# Our own files are GPLv3; the bundled upstream runner is MIT.
License:        GPL-3.0-or-later AND MIT
URL:            https://github.com/obriencj/gh-runner-service

# Source0 is this project. Source1 is the upstream runner release bundle,
# verified against runner_sha256 in %%prep and extracted, never repacked.
Source0:        %{name}-%{version}.tar.gz
Source1:        https://github.com/actions/runner/releases/download/v%{runner_version}/actions-runner-linux-%{runner_arch}-%{runner_version}.tar.gz

# Applied to the extracted runner tree, not to this project.
Patch0:         0001-remove-selfupdate-loop.patch

# Upstream ships a compiled .NET x86-64 payload; there is nothing to rebuild.
ExclusiveArch:  x86_64

# The distro's own Python packaging macros do the wheel build and install.
# uv is the local development tool (see the Makefile); it deliberately has no
# part in the package build, which uses only what a stock buildroot provides.
BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  systemd-rpm-macros

Requires:       podman >= 5.0
Requires:       systemd >= 257
Requires:       shadow-utils
Requires:       container-selinux
Requires:       policycoreutils-python-utils
Requires:       python3

Requires(pre):  shadow-utils
Requires(post): systemd
Requires(post): policycoreutils-python-utils
Requires(post): /usr/bin/loginctl

%description
RPM-packaged, rootless-Podman, ephemeral GitHub Actions self-hosted runners.

Each runner executes inside a container built from a Containerfile shipped by
this package, takes exactly one job, and is destroyed. Job containers are
created as siblings by the host's rootless Podman via a docker-to-podman shim
baked into the image.

This is not a security boundary. The runner container holds the host Podman
socket, so anything running inside it can drive every container on the box.
The VM is the boundary. Do not attach these runners to public repositories.


%prep
# Verify before extracting. We ship no repacked tarball and no vendored tree.
echo '%{runner_sha256}  %{SOURCE1}' | sha256sum -c -

%setup -q -n %{name}-%{version}

# The upstream runner unpacks into a subdirectory of our own source tree. It
# is a payload we install pristine, not something we build.
mkdir -p %{runner_tree}
tar -xzf %{SOURCE1} -C %{runner_tree}

# Excluded outright rather than merely unused — see design §4. Upstream no
# longer ships a top-level svc.sh; the rootful-service machinery is now the
# templates plus runsvc.sh under bin/, and those are what have to go.
rm -f %{runner_tree}/bin/installdependencies.sh
rm -f %{runner_tree}/bin/runsvc.sh
rm -f %{runner_tree}/bin/systemd.svc.sh.template
rm -f %{runner_tree}/bin/actions.runner.service.template
rm -f %{runner_tree}/bin/RunnerService.js
# macOS service machinery, irrelevant on this platform.
rm -f %{runner_tree}/bin/darwin.svc.sh.template
rm -f %{runner_tree}/bin/actions.runner.plist.template
rm -f %{runner_tree}/bin/macos-run-invoker.js

%patch -P 0 -p1 -d %{runner_tree}


%generate_buildrequires
%pyproject_buildrequires


%build
%pyproject_wheel


%install
%pyproject_install

# --- everything that is not the Python package -----------------------------
# Installed explicitly here rather than delegated to a make target: the spec
# is the authority on the install layout, and a parallel `make install` would
# be a second source of truth that nothing verifies.

# Build context. Baked into the runner image; every file here executes inside
# the container and never on the host.
install -d -m0755 %{buildroot}%{_datadir}/%{name}/context
install -m0644 container/Containerfile \
    %{buildroot}%{_datadir}/%{name}/Containerfile
install -m0644 container/context/packages.list \
    %{buildroot}%{_datadir}/%{name}/context/
install -m0755 container/context/docker \
    container/context/entrypoint.sh \
    container/context/register.sh \
    %{buildroot}%{_datadir}/%{name}/context/

# Quadlet units. Symlinked into /etc/containers/systemd/users/<uid>/ by %%post,
# since the uid is not known until then.
install -d -m0755 %{buildroot}%{_datadir}/%{name}/quadlet
install -m0644 units/quadlet/gh-runner.build \
    units/quadlet/gh-runner@.container \
    %{buildroot}%{_datadir}/%{name}/quadlet/

# Maintenance timers. Ordinary user units, and vendor-owned, so they belong in
# %%{_userunitdir} (/usr/lib/systemd/user) — /etc/systemd/user is the local
# administrator's directory and is where `gh-runner-ctl sync` writes its
# generated drop-ins. Note these are NOT Quadlet types: a .service or .timer
# placed in the Quadlet directory is silently ignored.
install -d -m0755 %{buildroot}%{_userunitdir}
install -m0644 units/user/*.service units/user/*.timer units/user/*.target \
    %{buildroot}%{_userunitdir}/

# Configuration.
install -d -m0755 %{buildroot}%{_sysconfdir}/%{name}/instances.d
install -m0644 config/gh-runner.conf \
    %{buildroot}%{_sysconfdir}/%{name}/gh-runner.conf
install -m0644 config/example.conf.sample \
    %{buildroot}%{_sysconfdir}/%{name}/instances.d/example.conf.sample

# One credential per instance, named for the instance id. Root-only: ctl reads
# these and pipes them into `podman secret create`, so the uid that runs the
# containers never needs access to the file itself.
install -d -m0700 %{buildroot}%{_sysconfdir}/%{name}/credentials.d

# Instance state root. Home directory of the service account.
install -d -m0700 %{buildroot}%{_sharedstatedir}/%{name}

# The pristine upstream tree. entrypoint.sh syncs it into each instance's
# state directory when the .version marker differs — the runner writes into
# its own root, which is incompatible with RPM ownership.
install -d -m0755 %{buildroot}%{_prefix}/lib/%{name}
cp -a %{runner_tree} %{buildroot}%{_prefix}/lib/%{name}/%{runner_version}
echo '%{runner_version}' > \
    %{buildroot}%{_prefix}/lib/%{name}/%{runner_version}/.version
ln -sfn %{runner_version} %{buildroot}%{_prefix}/lib/%{name}/current


%pre
getent group %{service_user} >/dev/null || \
    groupadd -r -g %{service_uid} %{service_user} 2>/dev/null || \
    groupadd -r %{service_user}

getent passwd %{service_user} >/dev/null || \
    useradd -r -u %{service_uid} -g %{service_user} \
        -d %{_sharedstatedir}/%{name} -s /sbin/nologin \
        -c "GitHub Actions runner service account" %{service_user} 2>/dev/null || \
    useradd -r -g %{service_user} \
        -d %{_sharedstatedir}/%{name} -s /sbin/nologin \
        -c "GitHub Actions runner service account" %{service_user}

# An explicit, non-overlapping subid block. Rootless Podman is unusable
# without one, and the default allocation is not guaranteed to exist on a
# host where the account was created by something else.
if ! grep -q "^%{service_user}:" %{_sysconfdir}/subuid 2>/dev/null; then
    usermod --add-subuids 500000-565535 %{service_user} || :
fi
if ! grep -q "^%{service_user}:" %{_sysconfdir}/subgid 2>/dev/null; then
    usermod --add-subgids 500000-565535 %{service_user} || :
fi
exit 0


%post
uid=$(id -u %{service_user})
qdir=%{_sysconfdir}/containers/systemd/users/${uid}

loginctl enable-linger %{service_user} >/dev/null 2>&1 || :

# enable-linger returns before user@${uid}.service is up, so the daemon-reload
# below is a race on a fast install. Give it a moment, then proceed
# best-effort — `gh-runner-ctl sync` is the operation that must converge.
for _ in $(seq 1 20); do
    [ -S /run/user/${uid}/bus ] && break
    sleep 0.5
done

# Both trees. The instance dir is bind-mounted whole, so labelling only
# _work and externals leaves .runner, .credentials, _diag and the synced
# bin/ unlabelled and denied. /usr/lib/gh-runner is usr_t by default and a
# container cannot read it.
#
# Do NOT "fix" a denial here with :Z on the volume. It relabels with the
# runner container's private MCS category, after which sibling job containers
# cannot read _work — see design §10.
semanage fcontext -a -t container_file_t \
    '%{_sharedstatedir}/%{name}/[^/]+(/.*)?' 2>/dev/null || :
semanage fcontext -a -t container_ro_file_t \
    '%{_prefix}/lib/%{name}(/.*)?' 2>/dev/null || :
restorecon -R %{_sharedstatedir}/%{name} %{_prefix}/lib/%{name} 2>/dev/null || :

# Only Quadlet types belong in the Quadlet directory; the maintenance timers
# are ordinary user units already installed under /etc/systemd/user.
mkdir -p "${qdir}"
for f in %{_datadir}/%{name}/quadlet/*; do
    ln -sfn "$f" "${qdir}/$(basename "$f")"
done

runuser -u %{service_user} -- \
    env XDG_RUNTIME_DIR=/run/user/${uid} \
        DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/${uid}/bus \
    systemctl --user enable --now podman.socket >/dev/null 2>&1 || :
runuser -u %{service_user} -- \
    env XDG_RUNTIME_DIR=/run/user/${uid} \
        DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/${uid}/bus \
    systemctl --user daemon-reload >/dev/null 2>&1 || :

exit 0


%preun
if [ $1 -eq 0 ]; then
    %{_bindir}/gh-runner-ctl disable --all --now >/dev/null 2>&1 || :
fi
exit 0


%postun
if [ $1 -eq 0 ]; then
    uid=$(id -u %{service_user} 2>/dev/null) || uid=""

    semanage fcontext -d '%{_sharedstatedir}/%{name}/[^/]+(/.*)?' 2>/dev/null || :
    semanage fcontext -d '%{_prefix}/lib/%{name}(/.*)?' 2>/dev/null || :

    if [ -n "${uid}" ]; then
        # Derived state only. Dangling symlinks left here make the next
        # install's `doctor` output actively misleading.
        rm -rf %{_sysconfdir}/containers/systemd/users/${uid}
        rm -rf %{_sysconfdir}/systemd/user/gh-runner@*.service.d
        rm -rf %{_sysconfdir}/systemd/user/gh-runner.target.wants

        runuser -u %{service_user} -- \
            env XDG_RUNTIME_DIR=/run/user/${uid} \
                DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/${uid}/bus \
            systemctl --user daemon-reload >/dev/null 2>&1 || :

        loginctl disable-linger %{service_user} >/dev/null 2>&1 || :
    fi

    # /var/lib/gh-runner, /etc/gh-runner and the account are left in place
    # for inspection. The credential goes with them: it is 0600, and removing
    # it silently on an upgrade-gone-wrong is worse than leaving it.
fi
exit 0


%files
%license LICENSE
%doc README.md design/gh-runner-rpm-spec.md

%{_bindir}/gh-runner-ctl
%{_bindir}/gh-runner-prune
%{_bindir}/gh-runner-version-check

# Shared with other packages in the namespace — do not ship an __init__.py.
%dir %{python3_sitelib}/preoccupied
%{python3_sitelib}/preoccupied/gh_runner_ctl/
%{python3_sitelib}/preoccupied.gh_runner_ctl-%{version}.dist-info/

%dir %{_datadir}/%{name}
%{_datadir}/%{name}/Containerfile
%{_datadir}/%{name}/context/
%{_datadir}/%{name}/quadlet/

%{_prefix}/lib/%{name}/%{runner_version}/
%{_prefix}/lib/%{name}/current

%{_userunitdir}/gh-runner.target
%{_userunitdir}/gh-runner-prune.service
%{_userunitdir}/gh-runner-prune.timer
%{_userunitdir}/gh-runner-image-refresh.service
%{_userunitdir}/gh-runner-image-refresh.timer
%{_userunitdir}/gh-runner-version-check.service
%{_userunitdir}/gh-runner-version-check.timer

%dir %{_sysconfdir}/%{name}
%dir %{_sysconfdir}/%{name}/instances.d
%config(noreplace) %{_sysconfdir}/%{name}/gh-runner.conf
%{_sysconfdir}/%{name}/instances.d/example.conf.sample
%dir %attr(0700, root, root) %{_sysconfdir}/%{name}/credentials.d

%attr(0700, %{service_user}, %{service_user}) %dir %{_sharedstatedir}/%{name}



%changelog
* Wed Jul 29 2026 Christopher O'Brien <obriencj@gmail.com> - 0.1.0-1
- Initial packaging skeleton against actions/runner 2.328.0
