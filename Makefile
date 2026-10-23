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
	$(PYTHON) tools/test_platform_env.py
	$(PYTHON) tools/test_release_gates.py
	$(PYTHON) tools/test_image_permissions.py
	$(PYTHON) tools/test_validate_contracts.py
	$(PYTHON) tools/test_sync_app_release.py
	$(PYTHON) tools/test_wifi_provisioning.py
	$(PYTHON) tools/test_host_actions.py
	$(PYTHON) tools/test_factory_reset.py
	$(PYTHON) tools/check_rauc_conf.py
	$(PYTHON) tools/test_shared_maintenance.py
	$(PYTHON) tools/test_app_compatibility.py
	$(PYTHON) tools/test_app_update.py
	$(PYTHON) tools/test_os_version.py
	$(PYTHON) tools/test_system_state.py
	$(PYTHON) tools/test_netinfo.py
	$(PYTHON) tools/test_bluetooth_pairing.py
	$(PYTHON) tools/test_improv_dbus.py
	$(PYTHON) tools/test_verify_app_release.py
