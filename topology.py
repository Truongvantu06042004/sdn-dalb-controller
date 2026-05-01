"""
Mininet topology for SDN Load Balancing project.

Architecture:
  Controller A (port 6633): S1(H1,H2,H3) + S2(H4,H5)
  Controller B (port 6634): S3(H6,H7,H8) + S4(H9) + S5(H10,H11)

Subnets use /16 mask so hosts in different domains can ARP each other
directly without a gateway — the SDN controller handles forwarding.

Inter-domain link: S2 <-> S4  (100Mbps, 2ms delay)
Normal links: 10Mbps, 1ms delay, TCLink
"""

import sys
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info


def build_topology():
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink, autoSetMacs=False)

    info("*** Adding remote controllers\n")
    cA = net.addController('cA', controller=RemoteController,
                           ip='127.0.0.1', port=6633)
    cB = net.addController('cB', controller=RemoteController,
                           ip='127.0.0.1', port=6634)

    info("*** Adding switches\n")
    s1 = net.addSwitch('s1', cls=OVSSwitch, protocols='OpenFlow13', dpid='1')
    s2 = net.addSwitch('s2', cls=OVSSwitch, protocols='OpenFlow13', dpid='2')
    s3 = net.addSwitch('s3', cls=OVSSwitch, protocols='OpenFlow13', dpid='3')
    s4 = net.addSwitch('s4', cls=OVSSwitch, protocols='OpenFlow13', dpid='4')
    s5 = net.addSwitch('s5', cls=OVSSwitch, protocols='OpenFlow13', dpid='5')

    info("*** Adding hosts\n")
    # /16 mask: all hosts are in 10.0.0.0/16, so cross-domain ARP works
    # without a gateway — the controller acts as the inter-domain forwarder.
    h1  = net.addHost('h1',  ip='10.0.1.1/16', mac='00:00:00:00:00:01')
    h2  = net.addHost('h2',  ip='10.0.1.2/16', mac='00:00:00:00:00:02')
    h3  = net.addHost('h3',  ip='10.0.1.3/16', mac='00:00:00:00:00:03')
    h4  = net.addHost('h4',  ip='10.0.1.4/16', mac='00:00:00:00:00:04')
    h5  = net.addHost('h5',  ip='10.0.1.5/16', mac='00:00:00:00:00:05')
    h6  = net.addHost('h6',  ip='10.0.2.1/16', mac='00:00:00:00:00:06')
    h7  = net.addHost('h7',  ip='10.0.2.2/16', mac='00:00:00:00:00:07')
    h8  = net.addHost('h8',  ip='10.0.2.3/16', mac='00:00:00:00:00:08')
    h9  = net.addHost('h9',  ip='10.0.2.4/16', mac='00:00:00:00:00:09')
    h10 = net.addHost('h10', ip='10.0.2.5/16', mac='00:00:00:00:00:0a')
    h11 = net.addHost('h11', ip='10.0.2.6/16', mac='00:00:00:00:00:0b')

    link_opts = dict(bw=10, delay='1ms', use_htb=True)

    info("*** Adding links (domain A)\n")
    # Port assignment (sequential as links are added):
    # S1: port1=h1, port2=h2, port3=h3, port4=s2
    # S2: port1=s1, port2=h4, port3=h5, port4=s4(inter-domain)
    net.addLink(h1,  s1, **link_opts)
    net.addLink(h2,  s1, **link_opts)
    net.addLink(h3,  s1, **link_opts)
    net.addLink(s1,  s2, **link_opts)
    net.addLink(h4,  s2, **link_opts)
    net.addLink(h5,  s2, **link_opts)

    info("*** Adding links (domain B)\n")
    # S3: port1=h6, port2=h7, port3=h8, port4=s4
    # S4: port1=s3, port2=h9, port3=s5, port4=s2(inter-domain)
    # S5: port1=h10, port2=h11, port3=s4
    net.addLink(h6,  s3, **link_opts)
    net.addLink(h7,  s3, **link_opts)
    net.addLink(h8,  s3, **link_opts)
    net.addLink(s3,  s4, **link_opts)
    net.addLink(h9,  s4, **link_opts)
    net.addLink(s4,  s5, **link_opts)
    net.addLink(h10, s5, **link_opts)
    net.addLink(h11, s5, **link_opts)

    info("*** Adding inter-domain link S2 <-> S4 (100Mbps, 2ms)\n")
    # This becomes port4 on both S2 and S4 (last link added)
    net.addLink(s2, s4, bw=100, delay='2ms', use_htb=True)

    info("*** Starting network\n")
    net.build()

    s1.start([cA])
    s2.start([cA])
    s3.start([cB])
    s4.start([cB])
    s5.start([cB])

    info("*** Waiting for controllers (3s)\n")
    import time
    time.sleep(3)

    return net


def test_connectivity(net):
    info("\n*** Testing connectivity (pingall)\n")
    loss = net.pingAll(timeout='5')
    info(f"*** Packet loss: {loss}%\n")
    return loss


def run(args=None):
    setLogLevel('info')
    net = build_topology()

    if args and '--test' in args:
        test_connectivity(net)

    info("*** Running CLI\n")
    CLI(net)
    net.stop()


if __name__ == '__main__':
    run(sys.argv[1:])
