#include <core.p4>
#include <v1model.p4>

/*************************************************************************
*********************** H E A D E R S  ***********************************
*************************************************************************/

typedef bit<9>  port_t;
typedef bit<48> macAddr_t;

const bit<16> TYPE_IPV4 = 0x0800;
const bit<16> TYPE_ARP  = 0x0806;

header ethernet_t {
    macAddr_t dstAddr;
    macAddr_t srcAddr;
    bit<16>   etherType;
}

struct metadata {
    port_t expected_port;
}

struct headers {
    ethernet_t ethernet;
}

/*************************************************************************
*********************** D I G E S T  *************************************
*************************************************************************/

// Digest message sent to controller for MAC learning
struct mac_learn_digest_t {
    macAddr_t srcAddr;
    port_t    srcPort;
}

/*************************************************************************
*********************** P A R S E R  *************************************
*************************************************************************/

parser MyParser(
    packet_in packet,
    out headers hdr,
    inout metadata meta,
    inout standard_metadata_t standard_metadata
) {
    state start {
        transition parse_ethernet;
    }

    state parse_ethernet {
        packet.extract(hdr.ethernet);
        transition accept;
    }
}

/*************************************************************************
*********************** C H E C K S U M   V E R I F Y ********************
*************************************************************************/

control MyVerifyChecksum(
    inout headers hdr,
    inout metadata meta
) {
    apply { }
}

/*************************************************************************
*********************** I N G R E S S  P R O C E S S I N G ****************
*************************************************************************/

control MyIngress(
    inout headers hdr,
    inout metadata meta,
    inout standard_metadata_t standard_metadata
) {
    // Action to forward packet to a specific port
    action forward(port_t port) {
        standard_metadata.egress_spec = port;
    }

    // Action to flood packet to all ports except ingress port
    action flood() {
        // Use multicast group 1 for flooding
        standard_metadata.mcast_grp = 1;
    }

    // Action to drop packet
    action drop() {
        mark_to_drop(standard_metadata);
    }

    // Store the expected ingress port for a known source MAC.
    // If the actual ingress port differs, the host has moved.
    action smac_known(port_t expected_port) {
        meta.expected_port = expected_port;
    }

    // Table for source MAC learning check.
    // On hit, smac_known stores the expected port so the apply block
    // can detect port moves.  support_timeout enables idle timeout
    // notifications from the data plane.
    table smac_table {
        key = {
            hdr.ethernet.srcAddr: exact;
        }
        actions = {
            smac_known;
            NoAction;
        }
        size = 1024;
        default_action = NoAction();
    }

    // Table for destination MAC lookup (forwarding table)
    table dmac_table {
        key = {
            hdr.ethernet.dstAddr: exact;
        }
        actions = {
            forward;
            flood;
            drop;
        }
        size = 1024;
        default_action = flood();
    }


    apply {
        if (hdr.ethernet.isValid()) {
            if (smac_table.apply().hit) {
                // Known MAC — check if host moved to a different port
                if (standard_metadata.ingress_port != meta.expected_port) {
                    digest<mac_learn_digest_t>(1, {
                        hdr.ethernet.srcAddr,
                        standard_metadata.ingress_port
                    });
                }
            } else {
                // Unknown source MAC — trigger learning
                digest<mac_learn_digest_t>(1, {
                    hdr.ethernet.srcAddr,
                    standard_metadata.ingress_port
                });
            }

            dmac_table.apply();
        } else {
            drop();
        }
    }
}

/*************************************************************************
*********************** E G R E S S  P R O C E S S I N G ******************
*************************************************************************/

control MyEgress(
    inout headers hdr,
    inout metadata meta,
    inout standard_metadata_t standard_metadata
) {
    apply {
        // Prune multicast packets going back to ingress port
        // This prevents broadcast storms
        if (standard_metadata.egress_port == standard_metadata.ingress_port) {
            mark_to_drop(standard_metadata);
        }
    }
}

/*************************************************************************
*********************** C H E C K S U M   C O M P U T E ******************
*************************************************************************/

control MyComputeChecksum(
    inout headers hdr,
    inout metadata meta
) {
    apply { }
}

/*************************************************************************
*********************** D E P A R S E R  *********************************
*************************************************************************/

control MyDeparser(
    packet_out packet,
    in headers hdr
) {
    apply {
        packet.emit(hdr.ethernet);
    }
}

/*************************************************************************
*********************** S W I T C H  *************************************
*************************************************************************/

V1Switch(
    MyParser(),
    MyVerifyChecksum(),
    MyIngress(),
    MyEgress(),
    MyComputeChecksum(),
    MyDeparser()
) main;