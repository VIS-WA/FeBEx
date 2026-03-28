/*
 * FeBEx Parser
 * Parse chain: Ethernet → IPv4 (0x0800) → UDP (proto 17) → FeBEx meta (dport 5555)
 */

parser FeBExParser(
    packet_in             packet,
    out headers_t         hdr,
    inout metadata_t      meta,
    inout standard_metadata_t standard_meta
) {
    state start {
        transition parse_ethernet;
    }

    state parse_ethernet {
        packet.extract(hdr.ethernet);
        transition select(hdr.ethernet.etherType) {
            ETHERTYPE_IPV4: parse_ipv4;
            default:        accept;
        }
    }

    state parse_ipv4 {
        packet.extract(hdr.ipv4);
        transition select(hdr.ipv4.protocol) {
            IP_PROTO_UDP: parse_udp;
            default:      accept;
        }
    }

    state parse_udp {
        packet.extract(hdr.udp);
        transition select(hdr.udp.dstPort) {
            FEBEX_UDP_PORT: parse_febex;
            default:        accept;
        }
    }

    state parse_febex {
        packet.extract(hdr.febex);
        transition accept;
    }
}
