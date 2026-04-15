# FeBEx: Implementation Specification

> This document describes the FeBEx project design and implementation details.
> It serves as a reference for understanding the architecture, development decisions, and project structure.

---

## 1. What FeBEx is

In Helium-style LoRaWAN networks, IoT sensors broadcast data over LoRa. Multiple nearby hotspots (gateways) hear the same transmission and each independently forwards a UDP/IP copy to a cloud backend. This creates K redundant copies per uplink traversing the backhaul — wasted bandwidth. The cloud then de-duplicates and routes to the correct tenant's LoRaWAN Network Server (LNS).

**FeBEx** moves this logic into a P4-programmable switch sitting between hotspots and LNS backends (like an IXP aggregation point). It does three things at the switch:

1. **Tenant steering** — LPM match on DevAddr field → forward to correct LNS tenant's port
2. **In-network de-duplication** — suppress redundant copies of the same uplink (keyed on DevAddr + FCnt) using register-based state with controller-driven epoch expiry
3. **Receipt mirroring** — clone the first-forwarded copy to a Helium Cloud host so the correct hotspot miner gets paid (Proof-of-Coverage)

## 2. Architecture

```
N Edge Devices ──(LoRa broadcast, simulated by coverage matrix)──> K Hotspots
                                                                      |
                                                          UDP/IP (scapy packets)
                                                                      |
                                                                      v
                                                              FeBEx P4 Switch (BMv2)
                                                              /       |         \
                                                             v        v          v
                                                         LNS 1    LNS M    Helium Cloud
                                                       (tenant)  (tenant)  (payment receipts)

Control plane: Python controller using finsy, connects via P4Runtime gRPC
  - Installs tenant steering LPM entries
  - Toggles dedup on/off (register write)
  - Rotates epoch counter periodically (register write)
  - Configures clone session for receipt mirroring
```

**Mininet topology**: All K gateway hosts + M LNS hosts + 1 cloud host connected to a single BMv2 switch. The switch runs `febex.p4`. The controller connects via gRPC.

```
gw1..gwK  ──(ports 1..K)──  s1 (BMv2)  ──(ports K+1..K+M)──  lns1..lnsM
                                        ──(port K+M+1)──      cloud1
```

IP scheme: gateways `10.0.1.{1..K}`, LNS hosts `10.0.2.{1..M}`, cloud `10.0.3.1`. MACs deterministic: `00:00:00:00:SS:HH` where SS=subnet, HH=host index.

---

## 3. Custom packet format

We do NOT implement real LoRaWAN framing. Instead, gateways send UDP packets with a custom 12-byte **FeBEx metadata header** at the start of the UDP payload:

```
Ethernet | IPv4 | UDP (dst port 5555) | FeBEx Meta (12B) | dummy payload
```

FeBEx metadata fields (network byte order):
- `dev_addr` : 32 bits — LoRaWAN DevAddr, determines tenant routing
- `fcnt`     : 32 bits — frame counter, monotonic per device, used for dedup
- `gw_id`    : 16 bits — which hotspot forwarded this copy
- `flags`    : 8 bits  — reserved (set 0)
- `padding`  : 8 bits  — reserved (set 0)

Build these packets with **scapy** (define a custom `FeBExMeta` layer) in the traffic generator. Parse them with scapy or raw `struct.unpack` in receivers.

---

## 4. P4 data plane (`febex.p4`)

Target: `v1model` (BMv2 simple_switch_grpc). Split into `headers.p4`, `parser.p4`, and main `febex.p4` via `#include`.

### Headers
Standard Ethernet, IPv4, UDP headers. Custom `febex_meta_t` header (see Section 3). A `metadata_t` struct for internal pipeline state (tenant_id, is_duplicate, hash index, cloud_port, etc.).

### Parser
`Ethernet → IPv4 (ether_type 0x0800) → UDP (protocol 17) → FeBEx metadata → accept`

### Ingress pipeline — 3 stages

**Stage 1: Tenant steering (table)**
```
table tenant_steering:
  key: hdr.febex.dev_addr (lpm)
  actions: set_tenant(port, tenant_id, cloud_port, dst_mac), drop
  default: drop
```
The `set_tenant` action sets egress_spec, rewrites Ethernet dst_mac (since P4 switch doesn't do ARP), and stores tenant_id in metadata.

**Stage 2: De-duplication (registers)**
```
Registers:
  dedup_keys[DEDUP_TABLE_SIZE]    — bit<32>, stores a verification hash per slot
  dedup_epochs[DEDUP_TABLE_SIZE]  — bit<16>, epoch when slot was written
  current_epoch[1]                — bit<16>, global epoch (controller writes this)
  dedup_enabled[1]                — bit<1>,  0=skip dedup, 1=run dedup
```
Use `#define DEDUP_TABLE_SIZE 65536` (overridable via `-D` flag for experiments).

Logic in apply block:
1. Read `dedup_enabled`. If 0 → skip to forwarding.
2. Read `current_epoch[0]`.
3. Hash `(tenant_id, dev_addr, fcnt)` with CRC32 → index into register array.
4. Compute a second hash → `key_value` (to distinguish collisions at same index).
5. Read `dedup_keys[index]` and `dedup_epochs[index]`.
6. If `stored_epoch == current_epoch AND stored_key == key_value` → **duplicate**, mark for drop.
7. Else → **new**, write key_value and epoch to registers, forward.

**Stage 3: Receipt mirroring + drop**
- If NOT duplicate → `clone3(CloneType.I2E, 100)` to clone packet to cloud port. (Session 100 configured by controller.)
- If duplicate → `mark_to_drop()`.

### Egress
Empty pass-through.

### Deparser
Emit: Ethernet → IPv4 → UDP → FeBEx metadata.

---

## 5. Control plane (`controller.py`)

Use **finsy** (same as the template). Connect to BMv2 via P4Runtime gRPC.

### What it does
1. Push P4Info + BMv2 JSON to switch
2. Install LPM entries in `tenant_steering` for each tenant (DevAddr prefix → port + MAC)
3. Write `dedup_enabled` register: 1 for dedup mode, 0 for routing-only baseline
4. Configure clone session 100 → cloud host port
5. Run epoch rotation in background thread: increment `current_epoch` register every N seconds
6. Accept `--no-dedup` flag to disable dedup (sets register to 0, skips epoch thread)

### DevAddr prefix computation
For M tenants, `prefix_len = ceil(log2(M))`. Tenant i gets prefix `i << (32 - prefix_len)` with mask length `prefix_len`. Example with 4 tenants: /2 prefixes → `0x00000000/2`, `0x40000000/2`, `0x80000000/2`, `0xC0000000/2`.

---

## 6. Scenario configuration (YAML)

All topology, coverage, workload, and experiment parameters live in YAML config files. Create 4 configs:

```yaml
# Example: medium_city.yaml
topology:
  num_edge_devices: 100   # N (simulated, not Mininet hosts)
  num_hotspots: 10         # K (actual Mininet gateway hosts)
  num_tenants: 4           # M (actual Mininet LNS hosts)

coverage:
  mode: "probabilistic"    # or "radius" or "explicit"
  avg_hotspots_per_device: 3.2
  min_coverage: 1
  max_coverage: 8
  distribution: "poisson"
  # explicit mode: provide matrix: [[1,0,1],[0,1,1],...]
  # radius mode: area_km2, hotspot_range_km, placement strategy

tenants:  # auto-computed from num_tenants if omitted
  - id: 1
    devaddr_prefix: 0x00000000
    devaddr_mask_len: 2
  # ...

workload:
  uplinks_per_device: 50
  inter_arrival_ms: 100
  payload_size_bytes: 20

dedup:
  enabled: true
  register_size: 65536
  epoch_interval_s: 5

experiment:
  seed: 42
```

**4 scenario files to create:**
- `small_test.yaml` — 5 EDs, 3 hotspots, 2 tenants, explicit coverage matrix (for unit tests)
- `medium_city.yaml` — 100 EDs, 10 hotspots, 4 tenants, probabilistic coverage
- `large_city.yaml` — 500 EDs, 50 hotspots, 4 tenants
- `stress_test.yaml` — 200 EDs, 20 hotspots, 8 tenants, high avg coverage (6.0)

---

## 7. Coverage matrix generator (`generate_coverage.py`)

Reads config YAML → outputs a JSON file describing which hotspots hear which edge devices.

**Output format:**
```json
{
  "num_edge_devices": 100,
  "num_hotspots": 10,
  "coverage_matrix": [[1,0,1,0,...], [0,1,1,0,...], ...],
  "device_tenant_map": [1, 3, 2, ...],
  "device_devaddr": [25600, 2400000256, ...],
  "stats": { "avg_coverage": 3.18, "min_coverage": 1, "max_coverage": 7 }
}
```

`device_tenant_map` assigns each ED to a tenant (round-robin). `device_devaddr` assigns a random DevAddr within that tenant's range. `coverage_matrix[i][j] = 1` means ED i is heard by hotspot j.

**Three modes:**
- **probabilistic**: For each ED, sample coverage count from Poisson(avg), clip to [min,max], randomly pick that many hotspots.
- **radius**: Place hotspots and EDs in 2D space, coverage = distance < range.
- **explicit**: Directly specify the matrix in YAML (for small test cases).

---

## 8. Traffic generator (`traffic_gen.py`)

Runs on each gateway host inside Mininet. Uses **scapy** to build and send packets.

**Logic:**
```python
# Runs on gateway host gw{X}
# For each uplink event (fcnt), for each ED that this gateway covers:
#   Build scapy packet: Ether/IP/UDP/FeBExMeta(dev_addr, fcnt, gw_id=X)
#   Send it

for fcnt in range(uplinks_per_device):
    for ed_index in range(N):
        if coverage_matrix[ed_index][my_gw_index] == 1:
            pkt = Ether(dst=switch_mac)/IP(dst=lns_ip)/UDP(dport=5555)/FeBExMeta(...)
            sendp(pkt)
    sleep(inter_arrival_ms / 1000)
```

All gateway hosts run simultaneously (started by the experiment orchestrator), so the switch sees near-simultaneous duplicates — the real-world scenario.

Define a scapy layer class `FeBExMeta` with the 4 fields from Section 3.

---

## 9. Receivers (`lns_receiver.py`, `cloud_receiver.py`)

**LNS receiver** — runs on each LNS host, sniffs/receives UDP on port 5555, logs every packet:
```
timestamp_ns  dev_addr  fcnt  gw_id  src_ip  tenant_id
```
to `logs/lns{X}_received.tsv`. Handle SIGTERM for clean shutdown.

**Cloud receiver** — identical but runs on the cloud host, logs to `logs/cloud_receipts.tsv`. These are cloned receipt packets showing which hotspot first forwarded each unique uplink.

Use scapy sniff or raw UDP sockets — match the template's style.

---

## 10. Experiment orchestrator (`run_experiment.py`)

Automates one full experiment run:

1. Generate coverage matrix if needed
2. Start Mininet with the FeBEx topology (headless)
3. Pre-populate ARP tables on all hosts (P4 switch doesn't handle ARP)
4. Launch `lns_receiver.py` on each LNS host (background, via `host.popen()`)
5. Launch `cloud_receiver.py` on cloud host (background)
6. Start `controller.py` — installs rules, begins epoch rotation
7. Wait warmup period
8. Launch `traffic_gen.py` on all gateway hosts simultaneously (background)
9. Wait for traffic generators to finish
10. Drain period (2× epoch interval)
11. Kill receivers, stop controller, stop Mininet
12. Collect logs

Each experiment point needs **two runs**: dedup OFF (routing-only baseline, Configuration A) and dedup ON (FeBEx, Configuration B).

---

## 11. Experiments (`run_all.py`)

### E1: Backhaul savings vs. duplicate factor
- **Sweep**: `avg_hotspots_per_device` ∈ {1, 2, 3, 5, 7, 10}
- **Fixed**: N=100, K=10, M=2
- **Measure**: `savings % = 1 - (packets_dedup / packets_nodedup)`
- **Plot**: Line chart (x=avg dup factor, y=savings%). Include theoretical `1 - 1/avg_dup`.

### E2: Correctness — zero unique-uplink loss
- Same sweep as E1
- **Measure**: delivery ratio = unique (dev_addr, fcnt) pairs received / total unique sent
- Must be 1.0000 at register_size=65536

### E3: Multi-tenant isolation
- Single run: N=100, K=10, M=4, dedup ON
- **Verify**: Every packet at lns{X} has dev_addr within tenant X's DevAddr range. Zero cross-talk.

### E4: City-scale scalability
- **Sweep**: N ∈ {50, 100, 200, 500} × K ∈ {5, 10, 20, 50}
- **Measure**: throughput (pps), savings %, latency distribution
- Note BMv2 software limits vs. real P4 hardware

### E5: Dedup state sizing (hash collisions)
- **Sweep**: `register_size` ∈ {256, 1024, 4096, 16384, 65536}
- Requires recompiling P4 with `-DDEDUP_TABLE_SIZE=X`
- **Measure**: false-positive suppression rate, savings %
- **Plot**: Dual-axis (x=register size log scale, y1=FP rate, y2=savings%)

### E6: Epoch interval sensitivity
- **Sweep**: `epoch_interval_s` ∈ {0.5, 1, 2, 5, 10, 30}
- **Measure**: dedup effectiveness, boundary leakage (dups arriving right after epoch flip)

### E7: Payment receipt accuracy
- Single run: N=50, K=5, M=2, explicit coverage, dedup ON
- **Verify**: cloud receipts correctly identify the first-forwarding hotspot's gw_id

---

## 12. Evaluation (`evaluate.py`)

Reads logs from `results/`, computes all metrics above, generates matplotlib plots (PNG + PDF). Key functions:
- `load_lns_logs(dir)` — merge all `lns*_received.tsv` files
- `compute_backhaul_savings(logs_A, logs_B)` → savings %
- `compute_delivery_ratio(logs_dedup, coverage_json)` → ratio
- `check_tenant_isolation(per_tenant_logs, devaddr_ranges)` → confusion matrix
- Plot functions for each experiment (E1–E7)

---

## 13. Tests

Each test is self-contained: creates a small Mininet topology, runs a specific scenario, asserts correctness.

| Test | Setup | Assert |
|------|-------|--------|
| `test_basic_forwarding` | 1 gw, 1 lns, no dedup | 10 packets sent → 10 received |
| `test_tenant_steering` | 1 gw, 2 lns, no dedup | 5 pkts to tenant1 range, 5 to tenant2 → each LNS gets exactly 5 |
| `test_dedup` | 3 gw, 1 lns, dedup ON, long epoch | Same (DevAddr, FCnt) from all 3 → LNS gets 1 copy |
| `test_epoch_reset` | 2 gw, 1 lns, dedup ON, 2s epoch | Send from gw1, wait epoch flip, send same from gw2 → LNS gets 2 copies |
| `test_correctness` | 3 gw, 2 lns, dedup ON | 100 unique uplinks, each from 2 gws → delivery ratio = 1.0 |
| `test_receipt` | 2 gw, 1 lns, 1 cloud, dedup ON | Same uplink from both gws → cloud gets 1 receipt, LNS gets 1 pkt |

---

## 14. Visualization (`visualize_network.py`) — build LAST

Generate these plots from the coverage matrix JSON:
- **Bipartite graph**: EDs left, hotspots right, edges = coverage (networkx + matplotlib)
- **Coverage heatmap**: N×K matrix as imshow, rows sorted by tenant
- **2D spatial view** (radius mode): hotspot circles with range rings, ED dots colored by tenant
- **Coverage distribution histogram**: how many EDs covered by 1, 2, 3, ... K hotspots

---

## 15. Build order

Follow this order strictly. Get each step working before moving on.

1. P4: headers + parser + minimal forwarding (just set egress to port 1). Compile.
2. Mininet topology: 2 gws, 1 lns, 1 switch. Verify it starts.
3. Controller: connect to switch with finsy, push P4 config.
4. Add tenant steering table. Install 1 entry. Send 1 scapy packet from gw1 → verify lns1 receives it.
5. **test_basic_forwarding passes.**
6. Multi-tenant steering (2+ tenants). **test_tenant_steering passes.**
7. Add dedup registers and logic. **test_dedup passes.**
8. Add epoch rotation in controller. **test_epoch_reset passes.**
9. Add clone/receipt mirroring + cloud host. **test_receipt passes.**
10. Coverage matrix generator (3 modes).
11. Config files (4 YAML scenarios).
12. Experiment orchestrator.
13. run_all.py (E1–E7 parameter sweeps).
14. evaluate.py (metrics + plots).
15. visualize_network.py.
16. README.md.

---

## 16. Notes

- **Use finsy** for P4Runtime (same as template). Use **scapy** for packet construction and sniffing.
- **Single P4 program** with a `dedup_enabled` register — don't maintain two separate P4 files. Toggle via controller for A/B experiments.
- **BMv2 is slow** (~10-100K pps). That's fine — document it and note real hardware (Tofino) eliminates this.
- The switch does **not** do IP routing or ARP. Rewrite Ethernet dst_mac in the `set_tenant` action. Pre-populate ARP on all Mininet hosts.
- **Epoch = dedup window**: controller increments a 16-bit counter every N seconds. Entries from a previous epoch are treated as stale → new packet overwrites them. LoRaWAN multi-gateway copies arrive within ms, so even 1s epochs are fine.
- **Receipt mirroring**: `clone3()` in P4 clones the first-forwarded copy to the cloud port. The cloud host sees the `gw_id` of whichever hotspot's packet won the race → that miner gets payment credit.
- For **experiment E5**, recompile P4 with different `-DDEDUP_TABLE_SIZE` values.
- The template folder will show you how Mininet, BMv2, finsy, and scapy fit together. **Follow those patterns.** Adapt the FeBEx files to match the template's directory layout and Makefile conventions.
