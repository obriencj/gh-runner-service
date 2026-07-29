# Upstream pin management.
#
# The pin is two lines in the spec — runner_version and runner_sha256 — and
# they are the only place either value appears. Everything here reads them
# back rather than restating them.

GH_API := https://api.github.com/repos/actions/runner

##@ Upstream pin

.PHONY: show-pin
show-pin: ## Print the current upstream pin
	@echo "runner_version : $(RUNNER_VERSION)"
	@echo "runner_sha256  : $(RUNNER_SHA256)"
	@echo "source         : $(RUNNER_URL)"

.PHONY: check-upstream
check-upstream: ## Compare the pin against the latest actions/runner release
	@latest=$$(curl -fsSL $(GH_API)/releases/latest | \
	           sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p'); \
	test -n "$$latest" || { echo "could not reach GitHub" >&2; exit 2; }; \
	echo "pinned: $(RUNNER_VERSION)   latest: $$latest"; \
	if [ "$$latest" != "$(RUNNER_VERSION)" ]; then \
	    echo "behind upstream — run: make upgrade-runner V=$$latest" >&2; \
	    exit 1; \
	fi

# Moves the pin and, crucially, dry-runs the patch set against the new
# tarball. Design §4 makes every patch a standing rebase obligation; this is
# the cheapest place to discover it has come due. Failing here beats failing
# in %prep three weeks later.
.PHONY: upgrade-runner
upgrade-runner: ## Move the upstream pin (V=x.y.z) and re-test the patches
	@test -n "$(V)" || { echo "usage: make upgrade-runner V=2.329.0" >&2; exit 1; }
	@set -eu; \
	tarball="actions-runner-linux-$(RUNNER_ARCH)-$(V).tar.gz"; \
	url="https://github.com/actions/runner/releases/download/v$(V)/$$tarball"; \
	mkdir -p $(BUILDDIR)/upstream; \
	echo "fetching $$url"; \
	curl -fL --progress-bar -o "$(BUILDDIR)/upstream/$$tarball" "$$url"; \
	sha=$$(sha256sum "$(BUILDDIR)/upstream/$$tarball" | cut -d' ' -f1); \
	echo "sha256: $$sha"; \
	rm -rf $(BUILDDIR)/upstream/tree; mkdir -p $(BUILDDIR)/upstream/tree; \
	tar -xzf "$(BUILDDIR)/upstream/$$tarball" -C $(BUILDDIR)/upstream/tree; \
	echo "--- dry-running patches against $(V) ---"; \
	fail=0; \
	for p in patches/*.patch; do \
	    [ -e "$$p" ] || continue; \
	    if patch -p1 --dry-run -d $(BUILDDIR)/upstream/tree < "$$p" >/dev/null 2>&1; then \
	        echo "  ok    $$p"; \
	    else \
	        echo "  FAILS $$p" >&2; fail=1; \
	    fi; \
	done; \
	if [ "$$fail" != 0 ]; then \
	    echo "" >&2; \
	    echo "pin NOT moved. Rebase the failing patches against $(V) first." >&2; \
	    exit 1; \
	fi; \
	sed -i.bak -E 's/^(%global[ \t]+runner_version[ \t]+).*/\1$(V)/' $(SPEC); \
	sed -i.bak -E "s/^(%global[ \t]+runner_sha256[ \t]+).*/\1$$sha/" $(SPEC); \
	sed -i.bak -E 's/^Release:([ \t]+)[0-9]+/Release:\g<1>1/' $(SPEC) || \
	    sed -i.bak -E 's/^Release:([ \t]+)[0-9]+/Release:\11/' $(SPEC); \
	rm -f $(SPEC).bak; \
	echo ""; \
	echo "pin moved to $(V), Release reset to 1."; \
	echo "next: add a %changelog entry, then 'make rpm'"

.PHONY: sources
sources: ## Fetch Source0 exactly as the spec declares it, then verify
	@mkdir -p $(RPMTOP)/SOURCES
	@if [ ! -f "$(RPMTOP)/SOURCES/$(RUNNER_TARBALL)" ]; then \
	    echo "fetching $(RUNNER_URL)"; \
	    curl -fL --progress-bar -o "$(RPMTOP)/SOURCES/$(RUNNER_TARBALL)" "$(RUNNER_URL)"; \
	fi
	@$(MAKE) --no-print-directory verify-sources

.PHONY: verify-sources
verify-sources: ## Check the fetched tarball against the pinned digest
	@f="$(RPMTOP)/SOURCES/$(RUNNER_TARBALL)"; \
	test -f "$$f" || { echo "missing $$f — run 'make sources'" >&2; exit 1; }; \
	got=$$(sha256sum "$$f" | cut -d' ' -f1); \
	if [ "$$got" != "$(RUNNER_SHA256)" ]; then \
	    echo "DIGEST MISMATCH for $(RUNNER_TARBALL)" >&2; \
	    echo "  expected $(RUNNER_SHA256)" >&2; \
	    echo "  got      $$got" >&2; \
	    exit 1; \
	fi; \
	echo "verified $(RUNNER_TARBALL)"
