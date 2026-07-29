##@ Python

.PHONY: wheel
wheel: $(DISTDIR)/.wheel-stamp ## Build the control-command wheel

$(DISTDIR)/.wheel-stamp: pyproject.toml $(shell find src -name '*.py' 2>/dev/null)
	@mkdir -p $(DISTDIR)
	rm -f $(DISTDIR)/*.whl
	$(PYTHON) -m build --wheel --no-isolation --outdir $(DISTDIR)
	@touch $@

.PHONY: check-python
check-python: ## Run the Python unit tests
	$(PYTHON) -m pytest -q tests/unit

.PHONY: lint-python
lint-python: ## Static checks
	@command -v ruff >/dev/null && ruff check src tests || echo "ruff not installed, skipping"

.PHONY: develop
develop: ## Editable install into the current environment
	$(PYTHON) -m pip install -e .
