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

# The runner payload is a prebuilt x86-64 .NET tree, so the spec is
# ExclusiveArch: x86_64 and the build has to happen on that arch. On an
# aarch64 workstation this runs emulated, which is slower but honest — the
# alternative, rpmbuild --target, would produce a package nothing had actually
# resolved dependencies for.
RPM_PLATFORM ?= linux/amd64

##@ RPM in a container

.PHONY: builder-image
builder-image: ## Build the EL build environment image
	$(PODMAN) build \
	    --platform $(RPM_PLATFORM) \
	    --build-arg BASE=$(RPM_BASE) \
	    -t $(RPM_BUILDER) \
	    -f tools/Containerfile.rpmbuild \
	    tools

.PHONY: rpm-container
rpm-container: builder-image ## Build the RPM in a clean EL container
	@mkdir -p $(RPM_OUT) $(RPM_CACHE)
	$(PODMAN) run --rm \
	    --platform $(RPM_PLATFORM) \
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
	    --platform $(RPM_PLATFORM) \
	    -v $(CURDIR):/src:ro,z \
	    -v $(RPM_OUT):/out:z \
	    -v $(RPM_CACHE):/cache:z \
	    $(RPM_BUILDER) /bin/bash

.PHONY: clean-container
clean-container: ## Remove the builder image and cached sources
	-$(PODMAN) rmi $(RPM_BUILDER)
	rm -rf $(RPM_CACHE)
