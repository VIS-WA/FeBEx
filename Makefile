NETWORKS_DIR = networks

TASKS_DIR = tasks

SRC_MININET_DIR = mininet
SRC_P4_DIR = p4
SRC_CONTROLLER_DIR = p4rt_controller

BUILD_DIR = build
BUILD_P4_DIR = $(BUILD_DIR)/p4

TEMP_DIR = temp
TEMP_P4_SWITCH_DIR = $(TEMP_DIR)/p4_switch
TEMP_P4RT_CONTROLLER_DIR = $(TEMP_DIR)/p4rt_controller

TESTS_DIR = tests
TESTS_CHECKS_DIR = checks

P4_COMPILER = p4c-bm2-ss

CLI_NETWORK_FILE = $(SRC_MININET_DIR)/topology_cli.py

CHECKS_NETWORK_FILE = $(SRC_MININET_DIR)/topology_checks.py
CHECKS_CASES_FILE = $(TESTS_CHECKS_DIR)/traffic_checker.py

PYTHON_INTERPRETER = /opt/p4/p4dev-python-venv/bin/python3

IXP_SWITCH_P4_COMPILER_IN_FILE = $(SRC_P4_DIR)/ixp_switch.p4
IXP_SWITCH_P4_COMPILER_OUT_NAME = ixp_switch
IXP_SWITCH_P4_COMPILER_ARGS += --p4v 16
IXP_SWITCH_P4_COMPILER_ARGS += --p4runtime-files $(BUILD_P4_DIR)/$(IXP_SWITCH_P4_COMPILER_OUT_NAME).p4info.txtpb
IXP_SWITCH_P4_COMPILER_ARGS += -o $(BUILD_P4_DIR)/$(IXP_SWITCH_P4_COMPILER_OUT_NAME).json

IXP1S1_CONTROLLER_SCRIPT_FILE = $(SRC_CONTROLLER_DIR)/ixp1s1_controller.py
IXP1S1_CONTROLLER_TEMP_DIR = $(TEMP_P4RT_CONTROLLER_DIR)/ixp1s1
IXP1S1_CONTROLLER_LOG_FILE = $(IXP1S1_CONTROLLER_TEMP_DIR)/ixp1s1_controller-stdout.log
IXP1S1_CONTROLLER_PID_FILE = $(IXP1S1_CONTROLLER_TEMP_DIR)/ixp1s1_controller.pid
IXP2S1_CONTROLLER_SCRIPT_FILE = $(SRC_CONTROLLER_DIR)/ixp2s1_controller.py
IXP2S1_CONTROLLER_TEMP_DIR = $(TEMP_P4RT_CONTROLLER_DIR)/ixp2s1
IXP2S1_CONTROLLER_LOG_FILE = $(IXP2S1_CONTROLLER_TEMP_DIR)/ixp2s1_controller-stdout.log
IXP2S1_CONTROLLER_PID_FILE = $(IXP2S1_CONTROLLER_TEMP_DIR)/ixp2s1_controller.pid

.PHONY: \
	build-init \
	build-task-1 \
	build-task-2 \
	build-clean \
	run-init \
	run-task-1 \
	run-checks-1 \
	run-task-2 \
	run-checks-2 \
	run-stop \
	run-clean \
	clean \
	generate-submission \
	build-febex \
	build-febex-size \
	run-febex \
	run-tests-febex

all:

# ── FeBEx (this project) ───────────────────────────────────────────────

FEBEX_P4_DIR           = $(TASKS_DIR)/febex/p4
FEBEX_P4_COMPILER_IN   = $(FEBEX_P4_DIR)/febex.p4
FEBEX_P4_COMPILER_OUT  = febex
FEBEX_CONTROLLER_SCRIPT = $(TASKS_DIR)/febex/p4rt_controller/controller.py
FEBEX_CONTROLLER_TEMP_DIR = $(TEMP_P4RT_CONTROLLER_DIR)/s1
FEBEX_CONTROLLER_LOG   = $(FEBEX_CONTROLLER_TEMP_DIR)/s1_controller-stdout.log
FEBEX_CONTROLLER_PID   = $(FEBEX_CONTROLLER_TEMP_DIR)/s1_controller.pid

FEBEX_P4_ARGS  = --p4v 16
FEBEX_P4_ARGS += --p4runtime-files $(BUILD_P4_DIR)/$(FEBEX_P4_COMPILER_OUT).p4info.txtpb
FEBEX_P4_ARGS += -o $(BUILD_P4_DIR)/$(FEBEX_P4_COMPILER_OUT).json

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

.PHONY: build-febex build-febex-size run-febex run-tests-febex

# ── Original tasks ─────────────────────────────────────────────────────

build-init:
	mkdir -p $(BUILD_P4_DIR)

build-task-1:
	$(MAKE) build-clean
	$(MAKE) build-init
	$(P4_COMPILER) $(IXP_SWITCH_P4_COMPILER_ARGS) $(TASKS_DIR)/1/$(IXP_SWITCH_P4_COMPILER_IN_FILE)

build-task-2:
	$(MAKE) build-clean
	$(MAKE) build-init
	$(P4_COMPILER) $(IXP_SWITCH_P4_COMPILER_ARGS) $(TASKS_DIR)/2/$(IXP_SWITCH_P4_COMPILER_IN_FILE)

build-clean:
	rm -rf $(BUILD_DIR)

run-init:
	mkdir -p $(TEMP_P4RT_CONTROLLER_DIR)
	mkdir -p $(TEMP_P4_SWITCH_DIR)

run-task-1:
	$(MAKE) run-clean
	$(MAKE) build-task-1
	$(MAKE) run-init
	mkdir -p $(IXP1S1_CONTROLLER_TEMP_DIR)
	mkdir -p $(IXP2S1_CONTROLLER_TEMP_DIR)
	$(PYTHON_INTERPRETER) $(TASKS_DIR)/1/$(IXP1S1_CONTROLLER_SCRIPT_FILE) > $(IXP1S1_CONTROLLER_LOG_FILE) 2>&1 & echo $$! > $(IXP1S1_CONTROLLER_PID_FILE)
	$(PYTHON_INTERPRETER) $(TASKS_DIR)/1/$(IXP2S1_CONTROLLER_SCRIPT_FILE) > $(IXP2S1_CONTROLLER_LOG_FILE) 2>&1 & echo $$! > $(IXP2S1_CONTROLLER_PID_FILE)
	sudo $(PYTHON_INTERPRETER) $(NETWORKS_DIR)/1/$(CLI_NETWORK_FILE)
	$(MAKE) run-stop

run-checks-1:
ifeq ("$(wildcard $(TESTS_DIR)/1/$(TESTS_CHECKS_DIR))", "")
	$(error The requested checks are unavailable)
endif
	$(MAKE) run-clean
	$(MAKE) build-task-1
	$(MAKE) run-init
	mkdir -p $(IXP1S1_CONTROLLER_TEMP_DIR)
	mkdir -p $(IXP2S1_CONTROLLER_TEMP_DIR)
	$(PYTHON_INTERPRETER) $(TASKS_DIR)/1/$(IXP1S1_CONTROLLER_SCRIPT_FILE) > $(IXP1S1_CONTROLLER_LOG_FILE) 2>&1 & echo $$! > $(IXP1S1_CONTROLLER_PID_FILE)
	$(PYTHON_INTERPRETER) $(TASKS_DIR)/1/$(IXP2S1_CONTROLLER_SCRIPT_FILE) > $(IXP2S1_CONTROLLER_LOG_FILE) 2>&1 & echo $$! > $(IXP2S1_CONTROLLER_PID_FILE)
	sudo $(PYTHON_INTERPRETER) $(NETWORKS_DIR)/1/$(CHECKS_NETWORK_FILE)
	$(MAKE) run-stop
	$(PYTHON_INTERPRETER) $(TESTS_DIR)/1/$(CHECKS_CASES_FILE)

run-task-2:
	$(MAKE) run-clean
	$(MAKE) build-task-2
	$(MAKE) run-init
	mkdir -p $(IXP1S1_CONTROLLER_TEMP_DIR)
	mkdir -p $(IXP2S1_CONTROLLER_TEMP_DIR)
	$(PYTHON_INTERPRETER) $(TASKS_DIR)/2/$(IXP1S1_CONTROLLER_SCRIPT_FILE) > $(IXP1S1_CONTROLLER_LOG_FILE) 2>&1 & echo $$! > $(IXP1S1_CONTROLLER_PID_FILE)
	$(PYTHON_INTERPRETER) $(TASKS_DIR)/2/$(IXP2S1_CONTROLLER_SCRIPT_FILE) > $(IXP2S1_CONTROLLER_LOG_FILE) 2>&1 & echo $$! > $(IXP2S1_CONTROLLER_PID_FILE)
	sudo $(PYTHON_INTERPRETER) $(NETWORKS_DIR)/2/$(CLI_NETWORK_FILE)
	$(MAKE) run-stop

run-checks-2:
ifeq ("$(wildcard $(TESTS_DIR)/2/$(TESTS_CHECKS_DIR))", "")
	$(error The requested checks are unavailable)
endif
	$(MAKE) run-clean
	$(MAKE) build-task-2
	$(MAKE) run-init
	mkdir -p $(IXP1S1_CONTROLLER_TEMP_DIR)
	mkdir -p $(IXP2S1_CONTROLLER_TEMP_DIR)
	$(PYTHON_INTERPRETER) $(TASKS_DIR)/2/$(IXP1S1_CONTROLLER_SCRIPT_FILE) > $(IXP1S1_CONTROLLER_LOG_FILE) 2>&1 & echo $$! > $(IXP1S1_CONTROLLER_PID_FILE)
	$(PYTHON_INTERPRETER) $(TASKS_DIR)/2/$(IXP2S1_CONTROLLER_SCRIPT_FILE) > $(IXP2S1_CONTROLLER_LOG_FILE) 2>&1 & echo $$! > $(IXP2S1_CONTROLLER_PID_FILE)
	sudo $(PYTHON_INTERPRETER) $(NETWORKS_DIR)/2/$(CHECKS_NETWORK_FILE)
	$(MAKE) run-stop
	$(PYTHON_INTERPRETER) $(TESTS_DIR)/2/$(CHECKS_CASES_FILE)

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
