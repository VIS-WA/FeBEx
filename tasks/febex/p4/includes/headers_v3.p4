/*
 * FeBEx Headers
 * Defines all header types, the metadata struct, and top-level structs
 * used by the FeBEx P4 pipeline.
 */

// Dedup register array size; override at compile time: p4c-bm2-ss -DDEDUP_TABLE_SIZE=4096 ...
#ifndef DEDUP_TABLE_SIZE
#define DEDUP_TABLE_SIZE 65536
#endif

// Key-value hash space. Default 2^32 (negligible false positives).
// Override: -DKEY_HASH_MAX=16 for stress-test (4-bit key, frequent collisions).

typedef bit<9>  port_t;
typedef bit<48> macAddr_t;
typedef bit<32> ip4Addr_t;

/* ─── EtherType / Protocol constants ────────────────────────────────── */
const bit<16> ETHERTYPE_IPV4 = 0x0800;
const bit<8>  IP_PROTO_UDP   = 17;
const bit<16> FEBEX_UDP_PORT = 5555;

/* ─── Standard L2/L3 headers ────────────────────────────────────────── */

header ethernet_t {
    macAddr_t dstAddr;
    macAddr_t srcAddr;
    bit<16>   etherType;
}

header ipv4_t {
    bit<4>    version;
    bit<4>    ihl;
    bit<8>    diffserv;
    bit<16>   totalLen;
    bit<16>   identification;
    bit<3>    flags;
    bit<13>   fragOffset;
    bit<8>    ttl;
    bit<8>    protocol;
    bit<16>   hdrChecksum;
    ip4Addr_t srcAddr;
    ip4Addr_t dstAddr;
}

header udp_t {
    bit<16> srcPort;
    bit<16> dstPort;
    bit<16> len;
    bit<16> checksum;
}

/*
 * FeBEx metadata header (12 bytes, Section 3):
 *   dev_addr  32b  – LoRaWAN DevAddr; determines tenant routing (LPM key)
 *   fcnt      32b  – Frame counter, monotonic per device; dedup key
 *   gw_id     16b  – Which hotspot forwarded this copy
 *   flags      8b  – Reserved, set 0
 *   padding    8b  – Reserved, set 0
 */
header febex_meta_t {
    bit<32> dev_addr;
    bit<32> fcnt;
    bit<16> gw_id;
    bit<8>  flags;
    bit<8>  padding;
}

/* ─── Internal pipeline metadata (not transmitted) ──────────────────── */

/* V3: extra fields for the secondary register array (dual-register Bloom guard). */
struct metadata_t {
    bit<9>  tenant_id;      // Assigned by tenant_steering
    bit<1>  is_duplicate;   // Set by dedup logic
    bit<32> dedup_index;    // Primary hash → register array index
    bit<32> key_value;      // Primary key hash for collision disambiguation
    bit<9>  cloud_port;     // Cloud host egress port (stored from set_tenant)
    bit<32> dedup_index2;   // Secondary hash → register array index
    bit<32> key_value2;     // Secondary key hash
}

/* ─── Top-level header struct ────────────────────────────────────────── */

struct headers_t {
    ethernet_t   ethernet;
    ipv4_t       ipv4;
    udp_t        udp;
    febex_meta_t febex;
}
