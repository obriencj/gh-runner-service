# --- upstream pin -----------------------------------------------------------
# These two lines are the only place the upstream version and digest appear.
# The Makefile reads them back rather than restating them; `make upgrade-runner
# V=x.y.z` moves both and dry-runs the patch set before committing to the move.
%global runner_version  2.328.0
%global runner_sha256   0000000000000000000000000000000000000000000000000000000000000000
%global runner_arch     x64
# ----------------------------------------------------------------------------

%global service_user  gh-runner
%global service_uid   987
%global runner_tree   actions-runner-%{runner_version}

Name:           gh-runner
Version:        0.1.0
Release:        1%{?dist}
Summary:        Rootless, ephemeral GitHub Actions self-hosted runners

# Our own files are GPLv3; the bundled upstream runner is MIT.
License:        GPL-3.0-or-later AND MIT
URL:            https://github.com/obriencj/gh-runner-service

Source0:        https://github.com/actions/runner/releases/download/v%{runner_version}/actions-runner-linux-%{runner_arch}-%{runner_version}.tar.gz
Source1:        %{name}-%{version}.tar.gz

Patch0:         0001-remove-selfupdate-loop.patch

# Upstream ships a compiled .NET x86-64 payload; there is nothing to rebuild.
ExclusiveArch:  x86_64

BuildRequires:  make
BuildRequires:  python3-devel
BuildRequires:  uv
BuildRequires:  python3-setuptools
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
echo '%{runner_sha256}  %{SOURCE0}' | sha256sum -c -

%setup -q -c -T -n %{runner_tree}
tar -xzf %{SOURCE0}

# Excluded outright rather than merely unused — see design §4.
#   svc.sh                     writes a rootful system unit
#   bin/installdependencies.sh does not know EL10; deps are baked into the image
rm -f svc.sh bin/installdependencies.sh

%patch -P 0 -p1

cd %{_builddir}
%setup -q -D -T -a 1 -n %{name}-%{version}


%build
cd %{_builddir}/%{name}-%{version}
%make_build all


%install
cd %{_builddir}/%{name}-%{version}
%make_install
%make_build install-runner \
    DESTDIR=%{buildroot} \
    RUNNER_TREE=%{_builddir}/%{runner_tree}

# %ghost: the package owns the path and its mode, ships no content, and never
# touches it on upgrade. Written by `gh-runner-ctl set-credential`.
install -d -m0755 %{buildroot}%{_sysconfdir}/%{name}
touch %{buildroot}%{_sysconfdir}/%{name}/credentials


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

if [ $1 -eq 1 ]; then
cat <<'EOF'

gh-runner installed. No runners are enabled — dropping a config file and
activating it are deliberately separate steps.

    gh-runner-ctl set-credential
    gh-runner-ctl add 01 --url https://github.com/OWNER/REPO --labels alma10,podman
    gh-runner-ctl enable 01

First start builds the container image: several minutes, and it needs egress.
Run `gh-runner-ctl --help` and `gh-runner-ctl keys` for the reference.

EOF
fi
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
%{python3_sitelib}/preoccupied_gh_runner_ctl-%{version}.dist-info/

%dir %{_datadir}/%{name}
%{_datadir}/%{name}/Containerfile
%{_datadir}/%{name}/context/
%{_datadir}/%{name}/quadlet/

%{_prefix}/lib/%{name}/%{runner_version}/
%{_prefix}/lib/%{name}/current

%{_sysconfdir}/systemd/user/gh-runner-prune.service
%{_sysconfdir}/systemd/user/gh-runner-prune.timer
%{_sysconfdir}/systemd/user/gh-runner-image-refresh.service
%{_sysconfdir}/systemd/user/gh-runner-image-refresh.timer
%{_sysconfdir}/systemd/user/gh-runner-version-check.service
%{_sysconfdir}/systemd/user/gh-runner-version-check.timer

%dir %{_sysconfdir}/%{name}
%dir %{_sysconfdir}/%{name}/instances.d
%config(noreplace) %{_sysconfdir}/%{name}/gh-runner.conf
%{_sysconfdir}/%{name}/instances.d/example.conf.sample
%ghost %attr(0600, %{service_user}, %{service_user}) %{_sysconfdir}/%{name}/credentials

%attr(0700, %{service_user}, %{service_user}) %dir %{_sharedstatedir}/%{name}



%changelog
* Wed Jul 29 2026 Christopher O'Brien <obriencj@gmail.com> - 0.1.0-1
- Initial packaging skeleton against actions/runner 2.328.0
