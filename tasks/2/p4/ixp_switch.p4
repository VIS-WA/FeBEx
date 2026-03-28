#include <core.p4>
#include <v1model.p4>

/*************************************************************************
*********************** H E A D E R S  ***********************************
*************************************************************************/

typedef bit<9>  port_t;
typedef bit<48> macAddr_t;
typedef bit<32> ipAddr_t;

const bit<16> TYPE_IPV4 = 0x0800;
const bit<16> TYPE_ARP  = 0x0806;
const bit<8>  PROTO_ICMP = 1;
const bit<8>  PROTO_TCP  = 6;
const bit<8>  PROTO_UDP  = 17;

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
    ipAddr_t  srcAddr;
    ipAddr_t  dstAddr;
}

header tcp_t {
    bit<16> srcPort;
    bit<16> dstPort;
    bit<32> seqNo;
    bit<32> ackNo;
    bit<4>  dataOffset;
    bit<3>  res;
    bit<9>  flags;
    bit<16> window;
    bit<16> checksum;
    bit<16> urgentPtr;
}

header udp_t {
    bit<16> srcPort;
    bit<16> dstPort;
    bit<16> length;
    bit<16> checksum;
}

struct metadata {
    port_t  expected_port;  // Expected ingress port for known source MACs
    bit<16> l4_src_port;    // L4 source port (TCP or UDP)
    bit<16> l4_dst_port;    // L4 destination port (TCP or UDP)
}

struct headers {
    ethernet_t ethernet;
    ipv4_t     ipv4;
    tcp_t      tcp;
    udp_t      udp;
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
        transition select(hdr.ethernet.etherType) {
            TYPE_IPV4: parse_ipv4;
            default: accept;
        }
    }

    state parse_ipv4 {
        packet.extract(hdr.ipv4);
        transition select(hdr.ipv4.protocol) {
            PROTO_TCP: parse_tcp;
            PROTO_UDP: parse_udp;
            default: accept;
        }
    }

    state parse_tcp {
        packet.extract(hdr.tcp);
        meta.l4_src_port = hdr.tcp.srcPort;
        meta.l4_dst_port = hdr.tcp.dstPort;
        transition accept;
    }

    state parse_udp {
        packet.extract(hdr.udp);
        meta.l4_src_port = hdr.udp.srcPort;
        meta.l4_dst_port = hdr.udp.dstPort;
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

    // Action for route alteration — rewrite dst MAC and forward to
    // the designated egress port, bypassing normal L2 forwarding.
    action alter_route(macAddr_t egress_mac, port_t egress_port) {
        hdr.ethernet.dstAddr = egress_mac;
        standard_metadata.egress_spec = egress_port;
    }

    // Table for route alteration based on 5-tuple match.
    // Takes priority over normal L2 switching for matching IPv4 packets.
    // L4 ports use ternary match so ICMP rules can wildcard them.
    table route_alteration_table {
        key = {
            hdr.ipv4.srcAddr:    exact;
            hdr.ipv4.dstAddr:    exact;
            hdr.ipv4.protocol:   exact;
            meta.l4_src_port:    ternary;
            meta.l4_dst_port:    ternary;
        }
        actions = {
            alter_route;
            NoAction;
        }
        size = 1024;
        default_action = NoAction();
    }

    // Table for source MAC learning check.
    // On hit, smac_known stores the expected port so the apply block
    // can detect port moves.
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
            // ── Step 1: MAC learning (same logic as Task 1) ──
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

            // ── Step 2: Forwarding decision ──
            if (hdr.ipv4.isValid()) {
                // IPv4 packet: try route alteration first
                if (!route_alteration_table.apply().hit) {
                    // No alteration matched — normal L2 forwarding
                    dmac_table.apply();
                }
                // If route alteration hit, alter_route already set in controller
                // egress_spec and rewrote dst MAC — skip dmac_table.
            } else {
                // Non-IPv4 (ARP, etc.) — normal L2 forwarding
                dmac_table.apply();
            }
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
        packet.emit(hdr.ipv4);
        packet.emit(hdr.tcp);
        packet.emit(hdr.udp);
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