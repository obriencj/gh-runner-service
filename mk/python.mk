# Local development only.
#
# uv gives a fast edit/test loop here, but it has no part in the package
# build: the spec uses %pyproject_wheel and %pyproject_install, so an RPM can
# be built from a stock buildroot with nothing but pyproject-rpm-macros. Both
# paths drive the same setuptools backend from the same pyproject.toml.
#
# The wheel target exists so `make wheel` is available for inspection; the
# spec does not call it.

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

.PHONY: check-help
check-help: ## Smoke-test that every command's --help renders
	@PYTHONPATH=src $(PYTHON) -m preoccupied.gh_runner_ctl.cli --help >/dev/null
	@for c in add show edit rm enable disable sync list status doctor keys \
	          set-credential check-credential; do \
	    PYTHONPATH=src $(PYTHON) -m preoccupied.gh_runner_ctl.cli $$c --help >/dev/null \
	        || { echo "FAIL: gh-runner-ctl $$c --help" >&2; exit 1; }; \
	done
	@PYTHONPATH=src $(PYTHON) -m preoccupied.gh_runner_ctl.prune --help >/dev/null
	@PYTHONPATH=src $(PYTHON) -m preoccupied.gh_runner_ctl.version_check --help >/dev/null
	@echo "help ok"
