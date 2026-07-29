# RPM build. The spec is authored by hand and checked in — never generated.
# Generating it would break rpmlint, dist-git, and `rpmbuild -bs` on a plain
# checkout, and buys nothing that %global and rpmspec expansion do not.

RPMBUILD_ARGS := --define "_topdir $(RPMTOP)" \
                 --define "_sourcedir $(RPMTOP)/SOURCES" \
                 --define "_specdir $(CURDIR)" \
                 --define "_builddir $(RPMTOP)/BUILD" \
                 --define "_srcrpmdir $(DISTDIR)" \
                 --define "_rpmdir $(DISTDIR)"

TARBALL := $(NAME)-$(VERSION).tar.gz

##@ RPM

.PHONY: dist
dist: $(RPMTOP)/SOURCES/$(TARBALL) ## Create the source tarball of this project

$(RPMTOP)/SOURCES/$(TARBALL):
	@mkdir -p $(RPMTOP)/SOURCES
	git archive --format=tar.gz --prefix=$(NAME)-$(VERSION)/ \
	    -o $@ HEAD 2>/dev/null || \
	tar --exclude-vcs --exclude=$(BUILDDIR) --exclude=$(DISTDIR) \
	    --transform 's,^\.,$(NAME)-$(VERSION),' -czf $@ .

.PHONY: srpm
srpm: dist sources ## Build the source RPM
	@mkdir -p $(DISTDIR)
	rpmbuild $(RPMBUILD_ARGS) -bs $(SPEC)

.PHONY: rpm
rpm: dist sources ## Build the binary RPM
	@mkdir -p $(DISTDIR)
	rpmbuild $(RPMBUILD_ARGS) -bb $(SPEC)

# One shell, not two: each recipe line gets its own shell, so an `exit 0`
# guard on the first line does not stop the second from running.
# Catches "added a file, forgot the spec", which otherwise surfaces only as
# `File not found:` after a full container build has already run %prep and
# unpacked 644MB. Cheap here, expensive there.
.PHONY: check-shipped
check-shipped: ## Assert every shipped file is named in the spec
	@rc=0; \
	for f in units/user/* units/quadlet/* container/context/* config/*; do \
	    [ -f "$$f" ] || continue; \
	    n=$$(basename "$$f"); \
	    if grep -q -- "$$n" $(SPEC); then \
	        echo "  ok  $$n"; \
	    else \
	        echo "  MISSING from $(SPEC): $$f" >&2; rc=1; \
	    fi; \
	done; \
	exit $$rc

.PHONY: check-spec
check-spec: ## Lint the spec, and verify it parses
	@if command -v rpmspec >/dev/null; then \
	    rpmspec -P $(SPEC) >/dev/null && echo "  ok  spec parses"; \
	else \
	    echo "  --  rpmspec not present, skipped"; \
	fi
	@if command -v rpmlint >/dev/null; then \
	    rpmlint $(SPEC); \
	else \
	    echo "  --  rpmlint not present, skipped"; \
	fi

# A missing tool is "skipped", not "failed" — these checks only run on a
# systemd host, and a red FAIL on a dev laptop trains people to ignore the
# output entirely.
.PHONY: check-units
check-units: ## Verify the systemd and Quadlet units parse
	@rc=0; \
	if command -v systemd-analyze >/dev/null; then \
	    for u in units/user/*.service units/user/*.timer; do \
	        if systemd-analyze verify --user "$$u" 2>&1 | grep -q .; then \
	            echo "FAIL $$u"; systemd-analyze verify --user "$$u"; rc=1; \
	        else echo "  ok  $$u"; fi; \
	    done; \
	else \
	    echo "  --  systemd-analyze not present, unit verify skipped"; \
	fi; \
	q=/usr/libexec/podman/quadlet; \
	if [ -x "$$q" ]; then \
	    tmp=$$(mktemp -d); cp units/quadlet/* "$$tmp/"; \
	    QUADLET_UNIT_DIRS="$$tmp" "$$q" -dryrun -user >/dev/null || rc=1; \
	    rm -rf "$$tmp"; \
	    echo "  ok  quadlet -dryrun"; \
	else \
	    echo "  --  quadlet not present, skipped (see design §11)"; \
	fi; \
	exit $$rc
