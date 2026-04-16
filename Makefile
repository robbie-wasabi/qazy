PYTHON ?= python3

UNIT_TEST_MODULES = \
	tests.test_config \
	tests.test_reporting \
	tests.test_runtimes \
	tests.test_cli_functional

RUNTIME_INTEGRATION_TEST_MODULES = tests.test_live_runtimes
EXAMPLE_INTEGRATION_TEST_MODULES = tests.test_examples

.PHONY: test-unit test-runtime-integration test-example-integration test-all

test-unit:
	$(PYTHON) -m unittest $(UNIT_TEST_MODULES)

test-runtime-integration:
	$(PYTHON) -m unittest $(RUNTIME_INTEGRATION_TEST_MODULES)

test-example-integration:
	$(PYTHON) -m unittest $(EXAMPLE_INTEGRATION_TEST_MODULES)

test-all: test-unit test-runtime-integration test-example-integration
