import asyncio
import json
import logging
import os
import pathlib
import sys
import time

import finsy
from finsy import P4TableEntry, P4MulticastGroupEntry, P4DigestEntry, Match, Action

SCRIPT_DIRECTORY = os.path.abspath(os.path.dirname(__file__))
REPOSITORY_DIRECTORY = os.path.abspath(os.path.join(SCRIPT_DIRECTORY, "../../../"))
sys.path.append(REPOSITORY_DIRECTORY)
BUILD_DIRECTORY = os.path.join(REPOSITORY_DIRECTORY, "build/p4")

ROUTE_ALTERATION_CONFIG = os.path.join(
    REPOSITORY_DIRECTORY, "networks/2/ixp_switch/ixp1s1-route-alterations.json"
)

logger = finsy.LoggerAdapter(logging.getLogger("finsy"))

MAC_TIMEOUT_S = 10.0
NUM_PORTS = 3

# Active entries: MAC (str) -> (port, last_seen_monotonic).
mac_table: dict[str, tuple[int, float]] = {}


# ── Helpers ──────────────────────────────────────────────────────────


def _to_mac_str(value) -> str:
    """Normalise any MAC representation to a colon-separated hex string."""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return ":".join(f"{b:02x}" for b in value)
    if isinstance(value, int):
        return ":".join(f"{b:02x}" for b in value.to_bytes(6, "big"))
    raise TypeError(f"Unexpected MAC type: {type(value)}")


def _smac_entry(mac: str, port: int) -> P4TableEntry:
    return P4TableEntry(
        "MyIngress.smac_table",
        match=Match(**{"hdr.ethernet.srcAddr": mac}),
        action=Action("MyIngress.smac_known", expected_port=port),
    )


def _dmac_entry(mac: str, port: int) -> P4TableEntry:
    return P4TableEntry(
        "MyIngress.dmac_table",
        match=Match(**{"hdr.ethernet.dstAddr": mac}),
        action=Action("MyIngress.forward", port=port),
    )


# ── Route-alteration loading ────────────────────────────────────────


def load_route_alterations() -> list:
    """Load route alteration rules from the JSON config file."""
    if not os.path.exists(ROUTE_ALTERATION_CONFIG):
        logger.warning(f"Route alteration config not found: {ROUTE_ALTERATION_CONFIG}")
        return []
    with open(ROUTE_ALTERATION_CONFIG, "r") as f:
        return json.load(f)


async def install_route_alterations(switch: finsy.Switch):
    """Parse JSON rules and insert them into route_alteration_table.

    Tables with ternary match fields require a priority on every entry.
    ICMP rules (which wildcard L4 ports) get a lower priority;
    TCP/UDP rules (which specify exact ports) get a higher priority.
    """
    rules = load_route_alterations()

    for rule in rules:
        protocol = rule["protocol"]

        match_dict: dict = {
            "hdr.ipv4.srcAddr": rule["src_addr"],
            "hdr.ipv4.dstAddr": rule["dst_addr"],
            "hdr.ipv4.protocol": protocol,
        }

        # Default priority for wildcard-port rules (e.g. ICMP)
        priority = 10

        if protocol in (6, 17):  # TCP or UDP
            src_port = rule.get("src_port", 0)
            dst_port = rule.get("dst_port", 0)
            if src_port:
                match_dict["meta.l4_src_port"] = (src_port, 0xFFFF)
            if dst_port:
                match_dict["meta.l4_dst_port"] = (dst_port, 0xFFFF)
            priority = 20  # More specific → higher priority

        entry = P4TableEntry(
            "MyIngress.route_alteration_table",
            match=Match(**match_dict),
            action=Action(
                "MyIngress.alter_route",
                egress_mac=rule["egress_mac"],
                egress_port=rule["egress_port"],
            ),
            priority=priority,
        )

        await switch.write([+entry], strict=False)
        logger.info(
            f"[{switch.name}] Route alteration: "
            f"{rule['src_addr']} -> {rule['dst_addr']} proto={protocol} "
            f"-> port {rule['egress_port']} (priority={priority})"
        )


# ── Multicast / MAC learning (from Task 1) ──────────────────────────


async def setup_multicast_group(switch: finsy.Switch):
    replicas = [(p, p) for p in range(1, NUM_PORTS + 1)]
    await switch.write([
        +P4MulticastGroupEntry(multicast_group_id=1, replicas=replicas),
    ], strict=False)
    logger.info(f"[{switch.name}] Multicast group configured (ports 1-{NUM_PORTS})")


async def add_mac_entry(switch: finsy.Switch, mac: str, port: int):
    now = time.monotonic()
    old = mac_table.get(mac)

    if old is not None and old[0] == port:
        # Same port — just refresh timestamp.
        mac_table[mac] = (port, now)
        return

    if old is not None:
        # MAC moved — delete old, insert new.
        old_port = old[0]
        await switch.write([
            -P4TableEntry("MyIngress.smac_table",
                          match=Match(**{"hdr.ethernet.srcAddr": mac})),
            -P4TableEntry("MyIngress.dmac_table",
                          match=Match(**{"hdr.ethernet.dstAddr": mac})),
        ], strict=False)
        await switch.write([+_smac_entry(mac, port), +_dmac_entry(mac, port)], strict=False)
        mac_table[mac] = (port, now)
        logger.info(f"[{switch.name}] ~ MOVED  {mac} : port {old_port} -> {port}")
        return

    # New MAC.
    await switch.write([+_smac_entry(mac, port), +_dmac_entry(mac, port)], strict=False)
    mac_table[mac] = (port, now)
    logger.info(f"[{switch.name}] + LEARNED {mac} on port {port}")


async def handle_digests(switch: finsy.Switch):
    async for digest in switch.read_digests("mac_learn_digest_t"):
        for entry in digest:
            mac = _to_mac_str(entry["srcAddr"])
            port = int(entry["srcPort"])
            await add_mac_entry(switch, mac, port)
        await switch.write([digest.ack()], strict=False)


async def aging_loop(switch: finsy.Switch):
    while True:
        await asyncio.sleep(1.0)
        now = time.monotonic()

        expired = [
            (mac, port, now - ts)
            for mac, (port, ts) in mac_table.items()
            if now - ts > MAC_TIMEOUT_S
        ]
        if not expired:
            continue

        dels = []
        for mac, port, idle in expired:
            mac_table.pop(mac, None)
            dels.append(-P4TableEntry("MyIngress.smac_table",
                                      match=Match(**{"hdr.ethernet.srcAddr": mac})))
            dels.append(-P4TableEntry("MyIngress.dmac_table",
                                      match=Match(**{"hdr.ethernet.dstAddr": mac})))
            logger.info(f"[{switch.name}] - REMOVED {mac} (port {port})")

        await switch.write(dels, strict=False)


# ── Ready handler ────────────────────────────────────────────────────


async def controller_ready_handler(switch: finsy.Switch):
    await switch.delete_all()
    mac_table.clear()

    await setup_multicast_group(switch)

    # Install route-alteration rules from JSON config
    await install_route_alterations(switch)

    # Start background tasks for digest handling and MAC aging
    asyncio.create_task(handle_digests(switch))
    asyncio.create_task(aging_loop(switch))

    await switch.insert([
        P4DigestEntry("mac_learn_digest_t",
                      max_list_size=1,
                      max_timeout_ns=1_000_000,
                      ack_timeout_ns=1_000_000_000),
    ])
    logger.info(f"[{switch.name}] Controller ready")


# ── Main ─────────────────────────────────────────────────────────────


async def main():
    info_file = pathlib.Path(os.path.join(BUILD_DIRECTORY, "ixp_switch.p4info.txtpb"))
    prog_file = pathlib.Path(os.path.join(BUILD_DIRECTORY, "ixp_switch.json"))

    switch = finsy.Switch(
        "ixp1s1",
        "127.0.0.1:50001",
        finsy.SwitchOptions(
            p4info=info_file,
            p4blob=prog_file,
            device_id=1,
            ready_handler=controller_ready_handler,
        ),
    )
    await finsy.Controller([switch]).run()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    )
    finsy.run(main())