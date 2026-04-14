/* FeBEx V3 — Dual-register Bloom guard deduplication.
 *
 * Variant of febex.p4 that uses two independent register arrays (different
 * hash seeds).  A packet is marked as a duplicate only when it matches in
 * BOTH arrays simultaneously (AND logic).
 *
 * Primary benefit — reduced false-positive suppression:
 *   V1/V2 can incorrectly suppress a UNIQUE uplink if its primary index hash
 *   collides with a different flow's slot.  V3 requires simultaneous collision
 *   in two independent hash spaces, reducing the probability from O(1/N) to
 *   O(1/N^2).  For DEDUP_TABLE_SIZE=65536 and N=100 devices this is negligible.
 *
 * Epoch boundary behaviour (vs V1):
 *   At an epoch tick both arrays become stale simultaneously (both store the
 *   old epoch tag).  A boundary copy finds BOTH arrays stale → treated as
 *   fresh → same boundary leakage as V1.  V3 does NOT fix the boundary race;
 *   it trades collision-resistance for no improvement on leakage.
 *
 * Compile-time knob:
 *   -DDEDUP_TABLE_SIZE=N   (default 65536)
 *   Note: total register memory is 2× febex.p4 (V1).
 */

#include <core.p4>
#include <v1model.p4>

#include "includes/headers_v3.p4"
#include "includes/parser.p4"

control FeBExVerifyChecksum(
    inout headers_t  hdr,
    inout metadata_t meta
) {
    apply { }
}

control FeBExIngress(
    inout headers_t           hdr,
    inout metadata_t          meta,
    inout standard_metadata_t standard_meta
) {
    // Primary register array
    register<bit<32>>(DEDUP_TABLE_SIZE) dedup_keys;
    register<bit<16>>(DEDUP_TABLE_SIZE) dedup_epochs;

    // Secondary register array (independent hashes)
    register<bit<32>>(DEDUP_TABLE_SIZE) dedup_keys2;
    register<bit<16>>(DEDUP_TABLE_SIZE) dedup_epochs2;

    register<bit<16>>(1) current_epoch;
    register<bit<8>>(1)  dedup_enabled;

    action drop() {
        mark_to_drop(standard_meta);
    }

    action set_tenant(
        bit<9>    port,
        bit<9>    tenant_id,
        bit<9>    cloud_port,
        macAddr_t dst_mac
    ) {
        standard_meta.egress_spec = port;
        hdr.ethernet.dstAddr      = dst_mac;
        meta.tenant_id            = (bit<9>)tenant_id;
        meta.cloud_port           = (bit<9>)cloud_port;
    }

    table tenant_steering {
        key = { hdr.febex.dev_addr: lpm; }
        actions = { set_tenant; drop; }
        size = 256;
        default_action = drop();
    }

    apply {
        if (!hdr.febex.isValid()) { drop(); return; }

        if (!tenant_steering.apply().hit) { return; }

        bit<8> enabled;
        dedup_enabled.read(enabled, 0);

        if (enabled != 0) {
            bit<16> epoch;
            current_epoch.read(epoch, 0);

            // Primary: index hash seed=0, key hash seed=1
            hash(meta.dedup_index, HashAlgorithm.crc32, (bit<32>)0,
                 { meta.tenant_id, hdr.febex.dev_addr, hdr.febex.fcnt },
                 (bit<32>)DEDUP_TABLE_SIZE);
            hash(meta.key_value, HashAlgorithm.crc32, (bit<32>)1,
                 { meta.tenant_id, hdr.febex.dev_addr, hdr.febex.fcnt },
                 (bit<32>)KEY_HASH_MAX);

            // Secondary: index hash seed=2, key hash seed=3
            hash(meta.dedup_index2, HashAlgorithm.crc32, (bit<32>)2,
                 { meta.tenant_id, hdr.febex.dev_addr, hdr.febex.fcnt },
                 (bit<32>)DEDUP_TABLE_SIZE);
            hash(meta.key_value2, HashAlgorithm.crc32, (bit<32>)3,
                 { meta.tenant_id, hdr.febex.dev_addr, hdr.febex.fcnt },
                 (bit<32>)KEY_HASH_MAX);

            bit<32> stored_key;
            bit<16> stored_epoch;
            dedup_keys.read(stored_key,    meta.dedup_index);
            dedup_epochs.read(stored_epoch, meta.dedup_index);

            bit<32> stored_key2;
            bit<16> stored_epoch2;
            dedup_keys2.read(stored_key2,    meta.dedup_index2);
            dedup_epochs2.read(stored_epoch2, meta.dedup_index2);

            /* Duplicate only if BOTH arrays match this epoch.
             * Single-array collision cannot cause false-positive suppression. */
            if (stored_epoch  == epoch && stored_key  == meta.key_value &&
                stored_epoch2 == epoch && stored_key2 == meta.key_value2) {
                meta.is_duplicate = 1;
            } else {
                dedup_keys.write(meta.dedup_index,    meta.key_value);
                dedup_epochs.write(meta.dedup_index,   epoch);
                dedup_keys2.write(meta.dedup_index2,  meta.key_value2);
                dedup_epochs2.write(meta.dedup_index2, epoch);
                meta.is_duplicate = 0;
            }
        } else {
            meta.is_duplicate = 0;
        }

        if (meta.is_duplicate == 1) {
            drop();
        } else {
            if (meta.cloud_port != 0) {
                clone(CloneType.I2E, (bit<32>)100);
            }
        }
    }
}

control FeBExEgress(
    inout headers_t           hdr,
    inout metadata_t          meta,
    inout standard_metadata_t standard_meta
) { apply { } }

control FeBExComputeChecksum(
    inout headers_t  hdr,
    inout metadata_t meta
) { apply { } }

control FeBExDeparser(packet_out packet, in headers_t hdr) {
    apply {
        packet.emit(hdr.ethernet);
        packet.emit(hdr.ipv4);
        packet.emit(hdr.udp);
        packet.emit(hdr.febex);
    }
}

V1Switch(
    FeBExParser(),
    FeBExVerifyChecksum(),
    FeBExIngress(),
    FeBExEgress(),
    FeBExComputeChecksum(),
    FeBExDeparser()
) main;
