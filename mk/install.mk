# Staging the install tree. Called by the spec's %install, and directly
# usable on its own for inspection.
#
# RUNNER_TREE, when set, is the extracted upstream tarball to copy into
# /usr/lib/gh-runner/<runner_version>. The spec sets it from %prep; a bare
# `make install` omits it and stages only our own files.

RUNNER_TREE ?=

bindir     := $(PREFIX)/bin
datadir    := $(PREFIX)/share
libdir     := $(PREFIX)/lib
mandir     := $(datadir)/man

pkgdata    := $(datadir)/$(NAME)
pkglib     := $(libdir)/$(NAME)
userunits  := $(SYSCONFDIR)/systemd/user
pkgconf    := $(SYSCONFDIR)/$(NAME)

##@ Install

.PHONY: install
install: install-python install-container install-units install-config install-man ## Stage the full install tree into DESTDIR
	@echo "staged $(NAME) $(VERSION) into $(DESTDIR)/"

.PHONY: install-python
install-python: wheel
	$(PYTHON) -m installer --destdir="$(DESTDIR)" --prefix="$(PREFIX)" $(DISTDIR)/*.whl

# The build context: everything here is baked into the runner image and runs
# *inside* it. Nothing in this directory executes on the host.
.PHONY: install-container
install-container:
	install -d -m0755 "$(DESTDIR)$(pkgdata)/context"
	install -m0644 container/Containerfile        "$(DESTDIR)$(pkgdata)/Containerfile"
	install -m0644 container/context/packages.list "$(DESTDIR)$(pkgdata)/context/packages.list"
	install -m0755 container/context/docker        "$(DESTDIR)$(pkgdata)/context/docker"
	install -m0755 container/context/entrypoint.sh "$(DESTDIR)$(pkgdata)/context/entrypoint.sh"
	install -m0755 container/context/register.sh   "$(DESTDIR)$(pkgdata)/context/register.sh"

# Quadlet units are symlinked into /etc/containers/systemd/users/<uid>/ by
# %post, since the uid is not known until then. The maintenance timers are
# ordinary user units and land in a package-ownable path — see design §3.
.PHONY: install-units
install-units:
	install -d -m0755 "$(DESTDIR)$(pkgdata)/quadlet"
	install -m0644 units/quadlet/gh-runner.build       "$(DESTDIR)$(pkgdata)/quadlet/"
	install -m0644 units/quadlet/gh-runner@.container  "$(DESTDIR)$(pkgdata)/quadlet/"
	install -d -m0755 "$(DESTDIR)$(userunits)"
	install -m0644 units/user/*.service units/user/*.timer "$(DESTDIR)$(userunits)/"

.PHONY: install-config
install-config:
	install -d -m0755 "$(DESTDIR)$(pkgconf)/instances.d"
	install -m0644 config/gh-runner.conf       "$(DESTDIR)$(pkgconf)/gh-runner.conf"
	install -m0644 config/example.conf.sample  "$(DESTDIR)$(pkgconf)/instances.d/example.conf.sample"
	install -d -m0700 "$(DESTDIR)$(LOCALSTATEDIR)/lib/$(NAME)"

.PHONY: install-man
install-man: man
	install -d -m0755 "$(DESTDIR)$(mandir)/man5" "$(DESTDIR)$(mandir)/man8"
	install -m0644 $(BUILDDIR)/man/gh-runner-ctl.8   "$(DESTDIR)$(mandir)/man8/"
	install -m0644 $(BUILDDIR)/man/gh-runner.conf.5  "$(DESTDIR)$(mandir)/man5/"

# Only invoked from %install, where RUNNER_TREE points at the extracted
# upstream tarball. The tree is installed pristine; entrypoint.sh syncs it
# into each instance's state dir at container start (design §3).
.PHONY: install-runner
install-runner:
	@test -n "$(RUNNER_TREE)" || { echo "install-runner needs RUNNER_TREE=" >&2; exit 1; }
	install -d -m0755 "$(DESTDIR)$(pkglib)"
	cp -a "$(RUNNER_TREE)" "$(DESTDIR)$(pkglib)/$(RUNNER_VERSION)"
	echo "$(RUNNER_VERSION)" > "$(DESTDIR)$(pkglib)/$(RUNNER_VERSION)/.version"
	ln -sfn "$(RUNNER_VERSION)" "$(DESTDIR)$(pkglib)/current"

##@ Docs

.PHONY: man
man: $(BUILDDIR)/man/gh-runner-ctl.8 $(BUILDDIR)/man/gh-runner.conf.5 ## Render the man pages

$(BUILDDIR)/man/%: man/%.scd
	@mkdir -p $(BUILDDIR)/man
	$(SCDOC) < $< > $@
