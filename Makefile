# gh-runner — development and packaging entry point.
#
# This drives development: tests, the local wheel, the container image, pin
# management, and the RPM build itself.
#
# It does NOT install anything. The spec's %install is explicit and uses
# %pyproject_install plus plain `install` commands, so the spec is the single
# authority on the install layout. To inspect what ships:
#
#     make rpm && rpm -qlpv dist/*/*.rpm

NAME          := gh-runner
SPEC          := $(NAME).spec

PYTHON        ?= python3
PODMAN        ?= podman
UV            ?= uv

DESTDIR       ?=
PREFIX        ?= /usr
SYSCONFDIR    ?= /etc
LOCALSTATEDIR ?= /var

BUILDDIR      := _build
DISTDIR       := dist
RPMTOP        := $(CURDIR)/$(BUILDDIR)/rpm

# Pin values live in the spec and nowhere else. Read them back out rather
# than restating them — a second declaration is a drift bug that only shows
# up as a checksum failure at build time.
RUNNER_VERSION := $(shell awk '/^%global[ \t]+runner_version/ {print $$3}' $(SPEC))
RUNNER_SHA256  := $(shell awk '/^%global[ \t]+runner_sha256/  {print $$3}' $(SPEC))
RUNNER_ARCH    := $(shell awk '/^%global[ \t]+runner_arch/    {print $$3}' $(SPEC))
VERSION        := $(shell awk '/^Version:/ {print $$2}' $(SPEC))

RUNNER_TARBALL := actions-runner-linux-$(RUNNER_ARCH)-$(RUNNER_VERSION).tar.gz
RUNNER_URL     := https://github.com/actions/runner/releases/download/v$(RUNNER_VERSION)/$(RUNNER_TARBALL)

export NAME SPEC PYTHON PODMAN UV DESTDIR PREFIX SYSCONFDIR LOCALSTATEDIR
export BUILDDIR DISTDIR RPMTOP VERSION
export RUNNER_VERSION RUNNER_SHA256 RUNNER_ARCH RUNNER_TARBALL RUNNER_URL

.DEFAULT_GOAL := help

include mk/python.mk
include mk/container.mk
include mk/upstream.mk
include mk/rpm.mk
include mk/oci.mk

##@ General

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\n\033[1m%s\033[0m\n", "gh-runner $(VERSION) (runner pin $(RUNNER_VERSION))"} \
	     /^[a-zA-Z_0-9-]+:.*?##/ { printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2 } \
	     /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) }' $(MAKEFILE_LIST)
	@echo

.PHONY: all
all: wheel ## Build the local wheel (the spec does not use this)

.PHONY: check
check: check-version check-help check-python check-shim check-units check-spec ## Run the full test suite

.PHONY: clean
clean: ## Remove build output
	rm -rf $(BUILDDIR) $(DISTDIR) *.egg-info src/*.egg-info
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +

##@ Consistency

.PHONY: check-version
check-version: ## Assert the spec and the Python package agree on the version
	@pkg=$$($(PYTHON) -c "import sys; sys.path.insert(0, 'src'); \
	        import preoccupied.gh_runner_ctl as m; print(m.__version__)"); \
	if [ "$$pkg" != "$(VERSION)" ]; then \
	    echo "version drift: $(SPEC) says $(VERSION), package says $$pkg" >&2; \
	    echo "run: make bump-version V=<x.y.z>" >&2; \
	    exit 1; \
	fi; \
	echo "version ok: $(VERSION)"

.PHONY: bump-version
bump-version: ## Set our package version in both places (V=x.y.z)
	@test -n "$(V)" || { echo "usage: make bump-version V=x.y.z" >&2; exit 1; }
	sed -i.bak -E 's/^Version:([ \t]+).*/Version:\1$(V)/' $(SPEC) && rm -f $(SPEC).bak
	sed -i.bak -E 's/^__version__ = ".*"/__version__ = "$(V)"/' \
	    src/preoccupied/gh_runner_ctl/__init__.py && \
	    rm -f src/preoccupied/gh_runner_ctl/__init__.py.bak
	@echo "bumped to $(V); add a %changelog entry before building"
