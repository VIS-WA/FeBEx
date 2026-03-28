from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet

from networks import Topology

if __name__ == "__main__":
    setLogLevel("info")

    topology = Topology()

    network = Mininet(
        topo=topology,
        link=TCLink,
        autoSetMacs=False,
    )
    network.start()

    CLI(network)

    network.stop()
