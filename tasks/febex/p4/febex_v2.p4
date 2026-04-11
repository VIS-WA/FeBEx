/* FeBEx V2 — Sliding two-epoch window deduplication.
 *
 * Variant of febex.p4 that eliminates epoch-boundary duplicate leakage by
 * accepting copies whose stored epoch matches EITHER the current epoch OR
 * the immediately preceding epoch.
 *
 * How the race condition is fixed:
 *   V1 problem: copy arrives in epoch N, writes register (epoch=N, key=K).
 *               Controller rotates to epoch N+1.  Next copy arrives, sees
 *               stored_epoch=N != current=N+1 → treated as fresh → leaked.
 *   V2 fix:     Controller writes prev_epoch=N before writing current_epoch=N+1.
 *               Next copy arrives, sees stored_epoch=N == prev=N → duplicate.
 *               Suppressed correctly.
 *
 * Trade-off:
 *   Entries are valid for TWO full epoch durations.  A genuine new uplink
 *   whose (dev_addr, fcnt) happens to hash-collide with a two-epoch-old slot
 *   will be incorrectly suppressed.  In practice, fcnt is monotonic, so
 *   old (dev_addr, fcnt) pairs never reappear — this is not an issue.
 *
 * Compile-time knob:
 *   -DDEDUP_TABLE_SIZE=N   (default 65536)
 *
 * Controller requirement:
 *   Before writing current_epoch = N+1, write prev_epoch = N.
 *   See controller_v2.py for the updated epoch_rotation_loop.
 */

#include <core.p4>
#include <v1model.p4>

#include "includes/headers.p4"
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
    register<bit<32>>(DEDUP_TABLE_SIZE) dedup_keys;
    register<bit<16>>(DEDUP_TABLE_SIZE) dedup_epochs;
    register<bit<16>>(1) current_epoch;
    register<bit<16>>(1) prev_epoch;      // written by controller before each rotation
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
            bit<16> prev;
            current_epoch.read(epoch, 0);
            prev_epoch.read(prev, 0);

            hash(meta.dedup_index, HashAlgorithm.crc32, (bit<32>)0,
                 { meta.tenant_id, hdr.febex.dev_addr, hdr.febex.fcnt },
                 (bit<32>)DEDUP_TABLE_SIZE);

            hash(meta.key_value, HashAlgorithm.crc32, (bit<32>)1,
                 { meta.tenant_id, hdr.febex.dev_addr, hdr.febex.fcnt },
                 (bit<32>)0xFFFFFFFE);

            bit<32> stored_key;
            bit<16> stored_epoch;
            dedup_keys.read(stored_key,    meta.dedup_index);
            dedup_epochs.read(stored_epoch, meta.dedup_index);

            /* Duplicate if the key matches AND the stored epoch is current OR prev.
             * This catches copies that cross the epoch boundary:
             *   - stored_epoch == epoch : normal same-epoch duplicate
             *   - stored_epoch == prev  : boundary duplicate (arrived after rotation) */
            if (stored_key == meta.key_value &&
                    (stored_epoch == epoch || stored_epoch == prev)) {
                meta.is_duplicate = 1;
            } else {
                dedup_keys.write(meta.dedup_index,  meta.key_value);
                dedup_epochs.write(meta.dedup_index, epoch);
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
