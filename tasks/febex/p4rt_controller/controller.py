"""
FeBEx P4Runtime Controller
==========================
Connects to a single BMv2 FeBEx switch via finsy/P4Runtime and:
  1. Pushes P4Info + BMv2 JSON
  2. Installs LPM entries in tenant_steering for each tenant
  3. Writes dedup_enabled and current_epoch registers (via simple_switch_CLI / Thrift)
  4. Configures clone session 100 → cloud port      (via simple_switch_CLI / Thrift)
  5. Runs epoch rotation in a background asyncio task

Usage:
    python3 controller.py [options]

Options:
    --gateways  K           Number of gateway hosts  (default: 2)
    --tenants   M           Number of LNS tenants    (default: 1)
    --cloud-port P          Switch egress port for cloud host (0 = auto = K+M+1)
    --no-cloud              Skip clone session configuration
    --no-dedup              Disable deduplication (routing-only baseline)
    --epoch-interval N      Seconds between epoch increments  (default: 5)
    --grpc-addr ADDR        gRPC address of BMv2 switch  (default: 127.0.0.1:50051)
    --thrift-port P         Thrift port for simple_switch_CLI  (default: 9090)
    --device-id  N          P4Runtime device ID  (default: 1)
"""

import argparse
import asyncio
import logging
import math
import os
import pathlib
import subprocess
import sys

# ── Log is set up before any finsy import so we can see crash messages ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
)
_raw_logger = logging.getLogger("febex.controller")
_raw_logger.info("Controller process starting (pid=%d)", os.getpid())

import finsy
from finsy import P4TableEntry, Match, Action

logger = finsy.LoggerAdapter(logging.getLogger("finsy"))

# ── Paths ─────────────────────────────────────────────────────────────
SCRIPT_DIRECTORY     = os.path.abspath(os.path.dirname(__file__))
REPOSITORY_DIRECTORY = os.path.abspath(os.path.join(SCRIPT_DIRECTORY, "../../../"))
BUILD_DIRECTORY      = os.path.join(REPOSITORY_DIRECTORY, "build/p4")


# ═══════════════════════════════════════════════════════════════════════
#  Thrift helpers  (simple_switch_CLI — does NOT require finsy extensions)
# ═══════════════════════════════════════════════════════════════════════

def _thrift(thrift_port: int, cmd: str, retries: int = 3) -> bool:
    """
    Send a single command to simple_switch_CLI via stdin.
    Returns True on success.  Retries on transient failures.
    """
    for attempt in range(1, retries + 1):
        try:
            result = subprocess.run(
                ["simple_switch_CLI", "--thrift-port", str(thrift_port)],
                input=cmd + "\nexit\n",
                capture_output=True,
                text=True,
                timeout=8,
            )
            if result.returncode != 0 or "Error" in result.stdout:
                _raw_logger.warning("simple_switch_CLI '%s' attempt %d failed: %s",
                                    cmd, attempt, result.stdout.strip())
                if attempt < retries:
                    import time; time.sleep(0.5)
                    continue
                return False
            _raw_logger.info("CLI %-50s → OK", cmd)
            return True
        except FileNotFoundError:
            _raw_logger.error("simple_switch_CLI not found in PATH")
            return False
        except subprocess.TimeoutExpired:
            _raw_logger.error("simple_switch_CLI timed out for: %s", cmd)
            if attempt < retries:
                continue
            return False
        except Exception as exc:
            _raw_logger.error("simple_switch_CLI error: %s", exc)
            return False
    return False


def write_register(thrift_port: int, register_name: str, index: int, value: int):
    _thrift(thrift_port, f"register_write {register_name} {index} {value}")


def configure_mirror(thrift_port: int, mirror_id: int, egress_port: int):
    _thrift(thrift_port, f"mirroring_add {mirror_id} {egress_port}")


# ═══════════════════════════════════════════════════════════════════════
#  DevAddr prefix computation
# ═══════════════════════════════════════════════════════════════════════

def tenant_devaddr_prefix(tenant_idx: int, num_tenants: int):
    """
    (prefix_value, prefix_len) for tenant_idx (0-based).
    Special case M=1 → 0-bit prefix (match all).
    """
    if num_tenants <= 1:
        return (0x00000000, 0)
    prefix_len = math.ceil(math.log2(num_tenants))
    prefix_val = tenant_idx << (32 - prefix_len)
    return (prefix_val, prefix_len)


def lns_mac(tenant_idx: int) -> str:
    """Deterministic MAC for LNS (1-based tenant index)."""
    return f"00:00:00:00:02:{tenant_idx:02x}"


# ═══════════════════════════════════════════════════════════════════════
#  Table entry builders
# ═══════════════════════════════════════════════════════════════════════

def _steering_entry(
    tenant_idx: int,
    num_tenants: int,
    num_gateways: int,
    cloud_port: int,
) -> P4TableEntry:
    prefix_val, prefix_len = tenant_devaddr_prefix(tenant_idx, num_tenants)
    lns_port = num_gateways + tenant_idx + 1
    mac = lns_mac(tenant_idx + 1)

    return P4TableEntry(
        "FeBExIngress.tenant_steering",
        match=Match(**{"hdr.febex.dev_addr": (prefix_val, prefix_len)}),
        action=Action(
            "FeBExIngress.set_tenant",
            port=lns_port,
            tenant_id=tenant_idx,
            cloud_port=cloud_port if cloud_port > 0 else 0,
            dst_mac=mac,
        ),
    )


# ═══════════════════════════════════════════════════════════════════════
#  Epoch rotation loop
# ═══════════════════════════════════════════════════════════════════════

async def epoch_rotation_loop(thrift_port: int, interval: float):
    """Increment current_epoch register every `interval` seconds."""
    epoch = 0
    while True:
        await asyncio.sleep(interval)
        epoch = (epoch + 1) & 0xFFFF
        write_register(thrift_port, "FeBExIngress.current_epoch", 0, epoch)
        logger.info("Epoch → %d", epoch)


# ═══════════════════════════════════════════════════════════════════════
#  Main / controller ready handler
# ═══════════════════════════════════════════════════════════════════════

async def main(args: argparse.Namespace):
    num_gateways   = args.gateways
    num_tenants    = args.tenants
    dedup_on       = not args.no_dedup
    epoch_interval = args.epoch_interval
    thrift_port    = args.thrift_port
    cloud_port     = (
        args.cloud_port if args.cloud_port > 0
        else (num_gateways + num_tenants + 1)
    )
    use_cloud = not args.no_cloud

    _initialised = False          # guard against re-entrant ready_handler

    async def controller_ready_handler(switch: finsy.Switch):
        nonlocal _initialised
        if _initialised:
            logger.info("[%s] ready_handler re-invoked — skipping (already initialised)",
                        switch.name)
            return

        logger.info("[%s] Starting FeBEx initialisation...", switch.name)

        # Clear existing table entries only (avoid P4Runtime register ops
        # which BMv2 does not support and would cause errors)
        try:
            await switch.delete_all()
        except Exception as exc:
            logger.warning("[%s] delete_all partial failure (expected on BMv2): %s",
                           switch.name, exc)

        # ── 1. Tenant steering LPM entries ──────────────────────────
        entries = []
        for t in range(num_tenants):
            entry = _steering_entry(t, num_tenants, num_gateways,
                                    cloud_port if use_cloud else 0)
            entries.append(+entry)
            pv, pl = tenant_devaddr_prefix(t, num_tenants)
            lp = num_gateways + t + 1
            logger.info(
                "[%s] Tenant %d: DevAddr %#010x/%d → port %d  mac %s",
                switch.name, t, pv, pl, lp, lns_mac(t + 1)
            )
        await switch.write(entries, strict=False)

        # ── 2. Registers via simple_switch_CLI ──────────────────────
        #    (avoids finsy P4RegisterEntry version dependency)
        dedup_val = 1 if dedup_on else 0
        write_register(thrift_port, "FeBExIngress.dedup_enabled", 0, dedup_val)
        write_register(thrift_port, "FeBExIngress.current_epoch",  0, 0)
        logger.info("[%s] Registers: dedup_enabled=%d current_epoch=0", switch.name, dedup_val)

        # ── 3. Clone session 100 → cloud port ───────────────────────
        if use_cloud and cloud_port > 0:
            configure_mirror(thrift_port, 100, cloud_port)
            logger.info("[%s] Mirror session 100 → port %d", switch.name, cloud_port)

        # ── 4. Epoch rotation ────────────────────────────────────────
        if dedup_on and epoch_interval > 0:
            asyncio.create_task(
                epoch_rotation_loop(thrift_port, epoch_interval),
                name=f"{switch.name}-epoch",
            )
            logger.info(
                "[%s] Epoch rotation every %.1fs", switch.name, epoch_interval
            )

        _initialised = True
        logger.info("[%s] Controller ready", switch.name)

    # ── Connect and run ──────────────────────────────────────────────
    info_file = pathlib.Path(BUILD_DIRECTORY) / "febex.p4info.txtpb"
    prog_file = pathlib.Path(BUILD_DIRECTORY) / "febex.json"

    if not info_file.exists():
        _raw_logger.error("P4Info not found: %s  (run 'make build-febex' first)", info_file)
        return
    if not prog_file.exists():
        _raw_logger.error("P4 binary not found: %s  (run 'make build-febex' first)", prog_file)
        return

    _raw_logger.info("Connecting to %s (device_id=%d)", args.grpc_addr, args.device_id)
    _raw_logger.info("P4Info: %s", info_file)
    _raw_logger.info("Binary: %s", prog_file)

    switch = finsy.Switch(
        "s1",
        args.grpc_addr,
        finsy.SwitchOptions(
            p4info=info_file,
            p4blob=prog_file,
            device_id=args.device_id,
            ready_handler=controller_ready_handler,
        ),
    )

    await finsy.Controller([switch]).run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FeBEx P4Runtime controller")
    parser.add_argument("--gateways",       type=int,   default=2)
    parser.add_argument("--tenants",        type=int,   default=1)
    parser.add_argument("--cloud-port",     type=int,   default=0,
                        help="Switch port for cloud host (0 = auto = K+M+1)")
    parser.add_argument("--no-cloud",       action="store_true",
                        help="Skip clone session; no cloud host")
    parser.add_argument("--no-dedup",       action="store_true",
                        help="Disable deduplication (routing-only baseline)")
    parser.add_argument("--epoch-interval", type=float, default=5.0)
    parser.add_argument("--grpc-addr",      type=str,   default="127.0.0.1:50051")
    parser.add_argument("--thrift-port",    type=int,   default=9090)
    parser.add_argument("--device-id",      type=int,   default=1)
    args = parser.parse_args()

    finsy.run(main(args))
