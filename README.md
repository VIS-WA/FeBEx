# FeBEx

FeBEx is a CS6204 networking project that implements in-network filtering and backhaul experiment workflows on top of a P4/Mininet environment.  
The repository includes the FeBEx P4 program, P4Runtime controller, topology scripts, experiment runners, and evaluation/plotting tools.

## Repository structure

```text
FeBEx/
├── Makefile                       # Build/run/test/experiment entry points
├── requirements.txt               # Extra Python dependencies for experiments/evaluation
├── networks/
│   └── febex/                     # Mininet topology/network runner
├── tasks/
│   └── febex/
│       ├── p4/                    # FeBEx P4 pipeline sources
│       ├── p4rt_controller/       # Python P4Runtime controller
│       ├── configs/               # Scenario configuration files
│       ├── run_all.py             # Experiment orchestrator (E1-E7)
│       ├── run_experiment.py      # Single-experiment runner
│       ├── evaluate.py            # Result aggregation/metrics
│       ├── visualize_network.py   # Coverage visualization utility
│       ├── visualize_singapore.py # Interactive Singapore map dashboard
│       └── test_febex.py          # FeBEx integration test script
├── results/                       # Generated experiment outputs
├── plots/                         # Generated figures (including dashboard)
├── misc/                          # Development documentation
│   ├── PROGRESS.md                # Project status and development log
│   └── FeBEx_Implementation_Spec.md  # Design specification
└── ...
```

## Setup

FeBEx is intended to run in the P4 VM/lab environment that provides:

- `p4c-bm2-ss`
- BMv2 + Mininet (`mn`)
- `/opt/p4/p4dev-python-venv/bin/python3`

Then install the additional Python packages:

```bash
pip install -r requirements.txt
```

## Build and run

From the repository root:

```bash
# Compile the FeBEx P4 pipeline
make build-febex

# Launch FeBEx controller + Mininet network
make run-febex

# Run FeBEx tests in Mininet
make run-tests-febex
```

## Run experiments and evaluate

```bash
# Run all official experiments (E1-E7)
make run-experiments

# Quick mode (reduced sweep points)
make run-experiments-quick

# Run one experiment (example: E1)
make run-experiment-e1

# Evaluate existing experiment results
make evaluate
```

Optional utilities:

```bash
# Generate coverage from config
make generate-coverage CONFIG=tasks/febex/configs/medium_city.yaml OUTPUT=coverage.json

# Visualize a coverage file
make visualize COVERAGE=coverage.json

# Interactive Singapore map dashboard
make visualize-singapore
```

## Interactive Dashboard

FeBEx includes an interactive **Singapore map visualization** that animates the deduplication process in real-time:

```bash
make visualize-singapore
```

This generates `plots/singapore_map.html` — an interactive dashboard showing:
- **Four city-scale scenarios** (Small, Medium, Large, Singapore realistic network)
- **Live packet animation**: sensors emit → hotspots relay → P4 switch deduplicates → LNS receives + cloud mirrors
- **Dedup visualization**: duplicate copies highlighted in red when suppressed
- **Live metrics**: event count, packets forwarded, packets suppressed, backhaul savings %
- **Playback controls**: ▶/⏸ play, adjustable speed (¼×, ½×, 1×, 2×, 4×)

Open the HTML in any modern web browser (requires internet for map tiles). Use the left sidebar to switch configs and adjust playback speed.

## Project Development

This repository was developed with assistance from AI tools (Claude Code). Implementation, testing, and evaluation remain the work of the project team.

For detailed project history and development notes, see `misc/PROGRESS.md` and `misc/FeBEx_Implementation_Spec.md`.
