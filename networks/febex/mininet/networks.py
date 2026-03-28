"""
FeBEx Mininet topology.

Layout:
    gw1..gwK   ──(ports 1..K)──    s1 (BMv2)   ──(ports K+1..K+M)──   lns1..lnsM
                                               ──(port K+M+1)──        cloud1  [optional]

IP scheme:
    Gateways  : 10.0.1.{1..K} / 24
    LNS hosts : 10.0.2.{1..M} / 24
    Cloud     : 10.0.3.1 / 24

MAC scheme (deterministic): 00:00:00:00:{subnet}:{host}
    subnet 1 → gateways, subnet 2 → LNS, subnet 3 → cloud
"""

import os
import sys

from mininet.topo import Topo

SCRIPT_DIRECTORY   = os.path.abspath(os.path.dirname(__file__))
REPOSITORY_DIRECTORY = os.path.abspath(os.path.join(SCRIPT_DIRECTORY, "../../../"))
sys.path.append(REPOSITORY_DIRECTORY)

from common.p4.functions import HelperFunctions  # noqa: E402
from common.mininet.nodes import Client, P4Switch  # noqa: E402


def gw_mac(idx: int) -> str:
    """MAC for gateway idx (1-based): 00:00:00:00:01:HH."""
    return f"00:00:00:00:01:{idx:02x}"


def lns_mac(idx: int) -> str:
    """MAC for LNS idx (1-based): 00:00:00:00:02:HH."""
    return f"00:00:00:00:02:{idx:02x}"


def cloud_mac() -> str:
    return "00:00:00:00:03:01"


class FeBExTopology(Topo):
    """
    Build a FeBEx Mininet topology.

    Parameters
    ----------
    num_gateways  : int  — K  (number of hotspot hosts)
    num_lns       : int  — M  (number of LNS tenant hosts)
    with_cloud    : bool — whether to include the Helium Cloud host
    grpc_port     : int  — BMv2 gRPC port (default 50051)
    thrift_port   : int  — BMv2 Thrift port (default 9090)
    """

    def build(
        self,
        num_gateways: int = 2,
        num_lns: int = 1,
        with_cloud: bool = False,
        grpc_port: int = 50051,
        thrift_port: int = 9090,
        **_params,
    ):
        self._num_gateways = num_gateways
        self._num_lns      = num_lns
        self._with_cloud   = with_cloud

        # ── FeBEx P4 switch ───────────────────────────────────────────
        switch = self.addSwitch(
            "s1",
            cls=P4Switch,
            identifier=1,
            thrift_port=thrift_port,
            grpc_address="0.0.0.0",
            grpc_port=grpc_port,
        )

        # ── Gateway hosts (port 1..K) ─────────────────────────────────
        for i in range(1, num_gateways + 1):
            mac  = gw_mac(i)
            ip   = f"10.0.1.{i}/24"
            host = self.addHost(
                f"gw{i}",
                cls=Client,
                configCmds=HelperFunctions.generate_set_interface_mac_commands(
                    {f"gw{i}-eth0": mac}
                ),
            )
            self.addLink(
                host,
                switch,
                intfName1=f"gw{i}-eth0",
                params1={"ip": ip},
                intfName2=f"s1-eth{i}",
            )

        # ── LNS hosts (port K+1..K+M) ────────────────────────────────
        for i in range(1, num_lns + 1):
            port = num_gateways + i
            mac  = lns_mac(i)
            ip   = f"10.0.2.{i}/24"
            host = self.addHost(
                f"lns{i}",
                cls=Client,
                configCmds=HelperFunctions.generate_set_interface_mac_commands(
                    {f"lns{i}-eth0": mac}
                ),
            )
            self.addLink(
                host,
                switch,
                intfName1=f"lns{i}-eth0",
                params1={"ip": ip},
                intfName2=f"s1-eth{port}",
            )

        # ── Cloud host (port K+M+1, optional) ────────────────────────
        if with_cloud:
            cloud_port = num_gateways + num_lns + 1
            mac = cloud_mac()
            host = self.addHost(
                "cloud1",
                cls=Client,
                configCmds=HelperFunctions.generate_set_interface_mac_commands(
                    {"cloud1-eth0": mac}
                ),
            )
            self.addLink(
                host,
                switch,
                intfName1="cloud1-eth0",
                params1={"ip": "10.0.3.1/24"},
                intfName2=f"s1-eth{cloud_port}",
            )


# ── Mininet CLI entry point ────────────────────────────────────────────
# Used by `make run-febex` for interactive exploration

if __name__ == "__main__":
    from mininet.net import Mininet
    from mininet.log import setLogLevel
    from mininet.cli import CLI
    from mininet.link import TCLink

    setLogLevel("info")
    topo = FeBExTopology(num_gateways=2, num_lns=2, with_cloud=True)
    net  = Mininet(topo=topo, link=TCLink, autoSetMacs=False)
    net.start()
    CLI(net)
    net.stop()


topos = {
    "topology": (lambda: FeBExTopology())
}
