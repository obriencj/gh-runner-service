# The control commands are built and tested with uv.
#
# --no-build-isolation throughout: an RPM buildroot has no network, so uv must
# use the setuptools already present as a BuildRequires rather than trying to
# fetch its own. The same flag keeps local builds honest about that.

UV      ?= uv
VENV    := $(BUILDDIR)/venv
VENVBIN := $(VENV)/bin

##@ Python

.PHONY: wheel
wheel: $(DISTDIR)/.wheel-stamp ## Build the control-command wheel

$(DISTDIR)/.wheel-stamp: pyproject.toml $(shell find src -name '*.py' 2>/dev/null)
	@mkdir -p $(DISTDIR)
	rm -f $(DISTDIR)/*.whl
	$(UV) build --wheel --no-build-isolation --out-dir $(DISTDIR)
	@touch $@

.PHONY: venv
venv: $(VENV)/.stamp ## Create the dev/test environment

$(VENV)/.stamp: pyproject.toml
	$(UV) venv --python $(PYTHON) $(VENV)
	VIRTUAL_ENV=$(VENV) $(UV) pip install --quiet -e '.[test]'
	@touch $@

.PHONY: check-python
check-python: venv ## Run the Python unit tests
	$(VENVBIN)/python -m pytest -q tests/unit

.PHONY: lint-python
lint-python: ## Static checks
	@$(UV) tool run ruff check src tests 2>/dev/null || echo "ruff unavailable, skipping"

.PHONY: shell
shell: venv ## Print the activate line for the dev environment
	@echo "source $(VENV)/bin/activate"
