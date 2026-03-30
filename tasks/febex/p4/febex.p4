/* FeBEx — Forward, deduplicate, and mirror LoRaWAN uplinks at a P4 switch.
 *
 * Three-stage ingress pipeline:
 *   1. Tenant steering  — LPM on dev_addr → egress port + MAC rewrite
 *   2. De-duplication   — register-based dedup keyed on (tenant, dev_addr, fcnt)
 *   3. Receipt mirror   — clone first-forwarded packet to Helium Cloud (I2E, session 100)
 *
 * Compile-time knob:
 *   -DDEDUP_TABLE_SIZE=N   (default 65536) — size of dedup register arrays
 */

#include <core.p4>
#include <v1model.p4>

#include "includes/headers.p4"
#include "includes/parser.p4"

/*************************************************************************
 *  V E R I F Y   C H E C K S U M
 *************************************************************************/

control FeBExVerifyChecksum(
    inout headers_t  hdr,
    inout metadata_t meta
) {
    apply { }
}

/*************************************************************************
 *  I N G R E S S
 *************************************************************************/

control FeBExIngress(
    inout headers_t           hdr,
    inout metadata_t          meta,
    inout standard_metadata_t standard_meta
) {
    /* ── Dedup registers ─────────────────────────────────────────────── */

    // Verification hash stored per slot (collision disambiguation)
    register<bit<32>>(DEDUP_TABLE_SIZE) dedup_keys;

    // Epoch tag stored per slot; stale if != current_epoch
    register<bit<16>>(DEDUP_TABLE_SIZE) dedup_epochs;

    // Global epoch counter; controller increments this periodically
    register<bit<16>>(1) current_epoch;

    // 1 = dedup active, 0 = routing-only baseline
    register<bit<8>>(1) dedup_enabled;

    /* ── Actions ─────────────────────────────────────────────────────── */

    action drop() {
        mark_to_drop(standard_meta);
    }

    /*
     * set_tenant — matched by tenant_steering LPM table.
     * Sets egress port, rewrites Ethernet dst (P4 switch has no ARP),
     * and stores tenant_id + cloud_port in metadata for later stages.
     */
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

    /* ── Stage 1: Tenant steering table ─────────────────────────────── */

    table tenant_steering {
        key = {
            hdr.febex.dev_addr: lpm;
        }
        actions = {
            set_tenant;
            drop;
        }
        size = 256;
        default_action = drop();
    }

    /* ── Apply block ─────────────────────────────────────────────────── */

    apply {
        // Drop any non-FeBEx packet (not a LoRaWAN uplink)
        if (!hdr.febex.isValid()) {
            drop();
            return;
        }

        /* ── Stage 1: Tenant steering ───────────────────────────────── */
        if (!tenant_steering.apply().hit) {
            // No LPM match → already dropped by default action; nothing more to do
            return;
        }

        /* ── Stage 2: De-duplication ─────────────────────────────────── */
        bit<8> enabled;
        dedup_enabled.read(enabled, 0);

        if (enabled != 0) {
            bit<16> epoch;
            current_epoch.read(epoch, 0);

            // Hash (tenant_id ++ dev_addr ++ fcnt) → register index
            hash(
                meta.dedup_index,
                HashAlgorithm.crc32,
                (bit<32>)0,
                { meta.tenant_id, hdr.febex.dev_addr, hdr.febex.fcnt },
                (bit<32>)DEDUP_TABLE_SIZE
            );

            // Second independent hash → key_value for slot verification.
            // base=1 ensures key_value ∈ [1, 0xFFFFFFFE], avoiding collision
            // with the zero-initialised register on the very first lookup.
            hash(
                meta.key_value,
                HashAlgorithm.crc32,
                (bit<32>)1,
                { meta.tenant_id, hdr.febex.dev_addr, hdr.febex.fcnt },
                (bit<32>)0xFFFFFFFE
            );

            bit<32> stored_key;
            bit<16> stored_epoch;
            dedup_keys.read(stored_key,    meta.dedup_index);
            dedup_epochs.read(stored_epoch, meta.dedup_index);

            if (stored_epoch == epoch && stored_key == meta.key_value) {
                // Same epoch, same key → duplicate; suppress
                meta.is_duplicate = 1;
            } else {
                // New or stale-epoch entry → first copy; record it
                dedup_keys.write(meta.dedup_index,  meta.key_value);
                dedup_epochs.write(meta.dedup_index, epoch);
                meta.is_duplicate = 0;
            }
        } else {
            // Dedup disabled (routing-only baseline)
            meta.is_duplicate = 0;
        }

        /* ── Stage 3: Receipt mirroring + drop ──────────────────────── */
        if (meta.is_duplicate == 1) {
            drop();
        } else {
            // Clone the first-forwarded copy to the Helium Cloud host,
            // but only if the controller configured a cloud port.
            // Clone session 100 is set by the controller to point to
            // the cloud port.  The clone carries gw_id in hdr.febex,
            // letting the cloud identify which hotspot sent first.
            if (meta.cloud_port != 0) {
                clone(CloneType.I2E, (bit<32>)100);
            }
        }
    }
}

/*************************************************************************
 *  E G R E S S   (pass-through)
 *************************************************************************/

control FeBExEgress(
    inout headers_t           hdr,
    inout metadata_t          meta,
    inout standard_metadata_t standard_meta
) {
    apply { }
}

/*************************************************************************
 *  C O M P U T E   C H E C K S U M
 *************************************************************************/

control FeBExComputeChecksum(
    inout headers_t  hdr,
    inout metadata_t meta
) {
    apply { }
}

/*************************************************************************
 *  D E P A R S E R
 *************************************************************************/

control FeBExDeparser(
    packet_out packet,
    in headers_t hdr
) {
    apply {
        packet.emit(hdr.ethernet);
        packet.emit(hdr.ipv4);
        packet.emit(hdr.udp);
        packet.emit(hdr.febex);
    }
}

/*************************************************************************
 *  S W I T C H   I N S T A N T I A T I O N
 *************************************************************************/

V1Switch(
    FeBExParser(),
    FeBExVerifyChecksum(),
    FeBExIngress(),
    FeBExEgress(),
    FeBExComputeChecksum(),
    FeBExDeparser()
) main;
