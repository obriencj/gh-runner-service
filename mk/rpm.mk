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

.PHONY: check-spec
check-spec: ## Lint the spec
	@command -v rpmlint >/dev/null || { echo "rpmlint not installed, skipping"; exit 0; }
	rpmlint $(SPEC)

.PHONY: check-units
check-units: ## Verify the systemd and Quadlet units parse
	@rc=0; \
	for u in units/user/*.service units/user/*.timer; do \
	    if systemd-analyze verify --user "$$u" 2>&1 | grep -q .; then \
	        echo "FAIL $$u"; systemd-analyze verify --user "$$u"; rc=1; \
	    else echo "  ok  $$u"; fi; \
	done; \
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
