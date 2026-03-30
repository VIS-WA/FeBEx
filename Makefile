NETWORKS_DIR = networks
TASKS_DIR    = tasks
BUILD_DIR    = build
BUILD_P4_DIR = $(BUILD_DIR)/p4
TEMP_DIR     = temp
TEMP_P4_SWITCH_DIR     = $(TEMP_DIR)/p4_switch
TEMP_P4RT_CONTROLLER_DIR = $(TEMP_DIR)/p4rt_controller

P4_COMPILER        = p4c-bm2-ss
PYTHON_INTERPRETER = /opt/p4/p4dev-python-venv/bin/python3

# ── FeBEx variables ───────────────────────────────────────────────────

FEBEX_P4_DIR            = $(TASKS_DIR)/febex/p4
FEBEX_P4_COMPILER_IN    = $(FEBEX_P4_DIR)/febex.p4
FEBEX_P4_COMPILER_OUT   = febex
FEBEX_CONTROLLER_SCRIPT = $(TASKS_DIR)/febex/p4rt_controller/controller.py
FEBEX_CONTROLLER_TEMP_DIR = $(TEMP_P4RT_CONTROLLER_DIR)/s1
FEBEX_CONTROLLER_LOG    = $(FEBEX_CONTROLLER_TEMP_DIR)/s1_controller-stdout.log
FEBEX_CONTROLLER_PID    = $(FEBEX_CONTROLLER_TEMP_DIR)/s1_controller.pid

FEBEX_P4_ARGS  = --p4v 16
FEBEX_P4_ARGS += --p4runtime-files $(BUILD_P4_DIR)/$(FEBEX_P4_COMPILER_OUT).p4info.txtpb
FEBEX_P4_ARGS += -o $(BUILD_P4_DIR)/$(FEBEX_P4_COMPILER_OUT).json

CLI_NETWORK_FILE = mininet/networks.py

.PHONY: all build-init build-clean run-init run-stop run-clean clean \
        build-febex build-febex-size run-febex run-tests-febex \
        run-experiments run-experiments-quick evaluate visualize \
        generate-submission

all:

# ═══════════════════════════════════════════════════════════════════════
#  FeBEx targets
# ═══════════════════════════════════════════════════════════════════════

build-febex:
	$(MAKE) build-clean
	$(MAKE) build-init
	$(P4_COMPILER) $(FEBEX_P4_ARGS) $(FEBEX_P4_COMPILER_IN)

# Recompile with a custom dedup table size: make build-febex-size DEDUP_SIZE=4096
build-febex-size:
	$(MAKE) build-clean
	$(MAKE) build-init
	$(P4_COMPILER) $(FEBEX_P4_ARGS) -DDEDUP_TABLE_SIZE=$(DEDUP_SIZE) $(FEBEX_P4_COMPILER_IN)

run-febex:
	$(MAKE) run-clean
	$(MAKE) build-febex
	$(MAKE) run-init
	mkdir -p $(FEBEX_CONTROLLER_TEMP_DIR)
	$(PYTHON_INTERPRETER) $(FEBEX_CONTROLLER_SCRIPT) \
		--gateways 2 --tenants 2 --epoch-interval 5 \
		> $(FEBEX_CONTROLLER_LOG) 2>&1 & echo $$! > $(FEBEX_CONTROLLER_PID)
	sudo $(PYTHON_INTERPRETER) $(NETWORKS_DIR)/febex/$(CLI_NETWORK_FILE)
	$(MAKE) run-stop

run-tests-febex:
	$(MAKE) run-clean
	$(MAKE) build-febex
	$(MAKE) run-init
	sudo $(PYTHON_INTERPRETER) $(TASKS_DIR)/febex/test_febex.py
	$(MAKE) run-stop

# ═══════════════════════════════════════════════════════════════════════
#  Experiment & evaluation targets
# ═══════════════════════════════════════════════════════════════════════

# Generate a coverage matrix from a YAML config:
#   make generate-coverage CONFIG=tasks/febex/configs/medium_city.yaml OUTPUT=coverage.json
generate-coverage:
	$(PYTHON_INTERPRETER) $(TASKS_DIR)/febex/generate_coverage.py \
		--config $(CONFIG) --output $(OUTPUT)

# Run all 7 experiments (E1-E7):
run-experiments:
	$(MAKE) build-febex
	sudo $(PYTHON_INTERPRETER) $(TASKS_DIR)/febex/run_all.py

# Quick mode (fewer sweep points, for testing):
run-experiments-quick:
	$(MAKE) build-febex
	sudo $(PYTHON_INTERPRETER) $(TASKS_DIR)/febex/run_all.py --quick

# Run specific experiments:  make run-experiment-e1
run-experiment-%:
	$(MAKE) build-febex
	sudo $(PYTHON_INTERPRETER) $(TASKS_DIR)/febex/run_all.py --experiments $(shell echo $* | tr a-z A-Z)

# Evaluate results and generate plots:
evaluate:
	$(PYTHON_INTERPRETER) $(TASKS_DIR)/febex/evaluate.py

# Visualize a coverage matrix:
#   make visualize COVERAGE=coverage.json
visualize:
	$(PYTHON_INTERPRETER) $(TASKS_DIR)/febex/visualize_network.py --coverage $(COVERAGE)

# ═══════════════════════════════════════════════════════════════════════
#  Common / infrastructure targets
# ═══════════════════════════════════════════════════════════════════════

build-init:
	mkdir -p $(BUILD_P4_DIR)

build-clean:
	rm -rf $(BUILD_DIR)

run-init:
	mkdir -p $(TEMP_P4RT_CONTROLLER_DIR)
	mkdir -p $(TEMP_P4_SWITCH_DIR)

run-stop:
	sudo mn -c
	for pid_file in $$(find temp -type f -name "*.pid"); do sudo kill -9 $$(cat $$pid_file | sed "s/^[[:space:]]*//;s/[[:space:]]*$$//") || true; sudo rm -rf $$pid_file; done

run-clean:
	$(MAKE) run-stop
	sudo rm -rf $(TEMP_DIR)

clean:
	$(MAKE) run-clean
	$(MAKE) build-clean

generate-submission:
	@read -p "Student ID: " student_id; \
	echo "*** Generating submission file"; \
	zip -r $$student_id.zip tasks;
