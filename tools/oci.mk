# Building the RPM in a container.
#
# The package targets EL10, which is not what anyone develops on. Rather than
# maintain a "works on my machine" build, the RPM is produced inside a clean
# base image — which is also the only way to find out whether the spec's
# BuildRequires are actually satisfiable there — the builder image installs
# them from a stock EL repo set, so `make builder-image` failing is itself the
# answer to "can this be built on the target".
#
#     make rpm-container                  # almalinux:10, the default target
#     make rpm-container RPM_BASE=...     # any EL/Fedora base
#
# The source tree is mounted read-only. Nothing is written back to it, so a
# container build cannot leave residue or pick up a stale artefact.

RPM_BASE     ?= docker.io/library/almalinux:10
RPM_TAG      := $(subst :,-,$(subst /,-,$(notdir $(RPM_BASE))))
RPM_BUILDER  ?= localhost/gh-runner-builder:$(RPM_TAG)
RPM_OUT      := $(CURDIR)/$(DISTDIR)/$(RPM_TAG)
RPM_CACHE    := $(CURDIR)/$(BUILDDIR)/cache

# Native by default, cross-targeted.
#
# The spec is ExclusiveArch: x86_64 because the runner payload is a prebuilt
# x86-64 tree — but nothing here is *compiled*. The wheel is noarch and the
# payload is copied verbatim, so `rpmbuild --target x86_64` satisfies
# ExclusiveArch and tags the package correctly while the container itself runs
# at native speed.
#
# Emulating a full x86-64 userspace to copy 666MB and compress it is minutes of
# qemu and enough memory pressure to get the build OOM-killed on a default
# 2GiB podman machine. It buys only one thing: proof that BuildRequires resolve
# on x86-64 specifically. That is worth checking occasionally, not every build:
#
#     make rpm-container RPM_PLATFORM=linux/amd64
#
RPM_TARGET   ?= x86_64
RPM_PLATFORM ?=
_platform_arg = $(if $(RPM_PLATFORM),--platform $(RPM_PLATFORM),)

# The builder image is tracked by a stamp file that depends on the
# Containerfile alone.
#
# Not a phony prerequisite: that shells out to `podman build` on every single
# invocation, and any cache miss re-runs the whole dnf layer. Not "no
# prerequisite" either — then editing the Containerfile leaves you running a
# stale image and debugging a build failure that is really just a missing
# package. The stamp gets both: rebuild exactly when the Containerfile
# changes, and never otherwise.
#
# Force one with `make -B builder-image`, or delete the stamp.
BUILDER_STAMP := $(BUILDDIR)/builder-$(RPM_TAG).stamp

##@ RPM in a container

$(BUILDER_STAMP): tools/Containerfile.rpmbuild
	$(PODMAN) build \
	    $(_platform_arg) \
	    --build-arg BASE=$(RPM_BASE) \
	    -t $(RPM_BUILDER) \
	    -f tools/Containerfile.rpmbuild \
	    tools
	@mkdir -p $(dir $@)
	@touch $@

.PHONY: builder-image
builder-image: $(BUILDER_STAMP) ## Build the EL build environment image if stale

.PHONY: rpm-container
rpm-container: $(BUILDER_STAMP) ## Build the RPM in a clean EL container
	@mkdir -p $(RPM_OUT) $(RPM_CACHE)
	$(PODMAN) run --rm \
	    $(_platform_arg) \
	    -e RPM_TARGET=$(RPM_TARGET) \
	    -v $(CURDIR):/src:ro,z \
	    -v $(RPM_OUT):/out:z \
	    -v $(RPM_CACHE):/cache:z \
	    $(RPM_BUILDER) \
	    /src/tools/rpmbuild.sh
	@echo
	@echo "RPMs in $(RPM_OUT)/"

.PHONY: shell-container
shell-container: $(BUILDER_STAMP) ## Interactive shell in the build environment
	$(PODMAN) run --rm -it \
	    $(_platform_arg) \
	    -e RPM_TARGET=$(RPM_TARGET) \
	    -v $(CURDIR):/src:ro,z \
	    -v $(RPM_OUT):/out:z \
	    -v $(RPM_CACHE):/cache:z \
	    $(RPM_BUILDER) /bin/bash

.PHONY: clean-container
clean-container: ## Remove the builder image, its stamp, and cached sources
	-$(PODMAN) rmi $(RPM_BUILDER)
	rm -f $(BUILDER_STAMP)
	rm -rf $(RPM_CACHE)
