PYTHON ?= python3

.PHONY: contracts render test

## Validate the contract registry and the rendered document (CI entry point).
contracts:
	$(PYTHON) tools/validate_contracts.py
	$(PYTHON) tools/render_contracts.py --check

## Regenerate contracts/EPILYKOS-OS-CONTRACTS.md from the YAML registry.
render:
	$(PYTHON) tools/render_contracts.py

## Self-test the validator against deliberately broken fixtures.
test:
	$(PYTHON) tools/test_validate_contracts.py
