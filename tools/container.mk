IMAGE ?= localhost/gh-runner:latest

##@ Container image

.PHONY: image
image: ## Build the runner image locally (the .build quadlet does this on a host)
	$(PODMAN) build --pull=newer -t $(IMAGE) -f container/Containerfile container

.PHONY: image-packages
image-packages: ## Show what the image bakes in
	@grep -v '^\s*#' container/context/packages.list | grep -v '^\s*$$' | sort

# The shim's argv rewriting is the highest-risk code in the project and is a
# pure function of argv, so it is tested as one. The fixtures under
# tests/shim/argv are hand-written, and containerised jobs have since run
# against them unchanged -- but they are still not a capture from a live
# runner. Anything new the runner turns out to emit belongs here as a fixture
# before it is handled.

.PHONY: check-shim
check-shim: ## Golden-file tests for the docker->podman-remote shim
	@tests/shim/run.sh
