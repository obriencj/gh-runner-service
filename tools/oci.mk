# Building the RPM in a container.
#
# The package targets EL10, which is not what anyone develops on. Rather than
# maintain a "works on my machine" build, the RPM is produced inside a clean
# base image — which is also the only way to find out whether the spec's
# BuildRequires are actually satisfiable there.
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

##@ RPM in a container

.PHONY: builder-image
builder-image: ## Build the EL build environment image
	$(PODMAN) build \
	    $(_platform_arg) \
	    --build-arg BASE=$(RPM_BASE) \
	    -t $(RPM_BUILDER) \
	    -f tools/Containerfile.rpmbuild \
	    tools

.PHONY: rpm-container
rpm-container: builder-image ## Build the RPM in a clean EL container
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
shell-container: builder-image ## Interactive shell in the build environment
	$(PODMAN) run --rm -it \
	    $(_platform_arg) \
	    -e RPM_TARGET=$(RPM_TARGET) \
	    -v $(CURDIR):/src:ro,z \
	    -v $(RPM_OUT):/out:z \
	    -v $(RPM_CACHE):/cache:z \
	    $(RPM_BUILDER) /bin/bash

.PHONY: clean-container
clean-container: ## Remove the builder image and cached sources
	-$(PODMAN) rmi $(RPM_BUILDER)
	rm -rf $(RPM_CACHE)
