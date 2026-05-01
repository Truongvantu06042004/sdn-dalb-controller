"""
auto_demo.py — Fully automated 4-phase DALB demonstration.

Starts Mininet internally, runs traffic phases automatically,
monitors controllers via REST, and prints a live dashboard.

Run with:
    sudo python3 auto_demo.py

Requirements:
  - Controller A running: ryu-manager controller_a.py --ofp-tcp-listen-port 6633 --wsapi-port 8080
  - Controller B running: ryu-manager controller_b.py --ofp-tcp-listen-port 6634 --wsapi-port 8081
"""

import sys
import time
import threading
import json
import subprocess

# ── Colour helpers (ANSI) ─────────────────────────────────────────────────────
R  = '\033[31m'
G  = '\033[32m'
Y  = '\033[33m'
B  = '\033[34m'
M  = '\033[35m'
C  = '\033[36m'
W  = '\033[37m'
BO = '\033[1m'
RS = '\033[0m'

def cprint(colour, msg):
    print(f'{colour}{msg}{RS}')

def banner(title, colour=C):
    w = 64
    print(f'\n{colour}{"═"*w}')
    print(f'  {BO}{title}{RS}{colour}')
    print(f'{"═"*w}{RS}')


CTRL_A = 'http://127.0.0.1:8080'
CTRL_B = 'http://127.0.0.1:8081'


# ── REST helpers ─────────────────────────────────────────────────────────────

def get_status(url):
    """Fetch controller status; return dict or None."""
    try:
        import urllib.request
        with urllib.request.urlopen(f'{url}/status', timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        return None


def check_controllers():
    """Return (ok_A, ok_B)."""
    return get_status(CTRL_A) is not None, get_status(CTRL_B) is not None


def print_status_line(label, data, colour):
    if data is None:
        print(f'  {colour}{label:<14}{RS} OFFLINE')
        return
    load   = data.get('total_load', 0)
    ct     = data.get('ct', 1000)
    status = data.get('status', '?')
    rho    = data.get('rho', 1.0)
    mc     = data.get('migration_count', 0)
    mgd    = ', '.join(data.get('managed_switches', []))
    icon   = f'{G}✅{RS}' if status == 'NORMAL' else f'{R}⚠️ {RS}'
    print(f'  {colour}{BO}{label:<14}{RS}'
          f' load={Y}{load:>8.1f}{RS}/s'
          f' CT={ct:<6.0f}'
          f' ρ={rho:.3f}'
          f' migrations={M}{mc}{RS}'
          f' [{mgd}] {icon}')


def live_monitor(stop_event, interval=5):
    """Background thread: print a status table every `interval` seconds."""
    while not stop_event.is_set():
        sa = get_status(CTRL_A)
        sb = get_status(CTRL_B)
        ts = time.strftime('%H:%M:%S')
        print(f'\n{B}[{ts}] Controller Status:{RS}')
        print_status_line('Controller A', sa, B)
        print_status_line('Controller B', sb, M)
        stop_event.wait(interval)


def wait_for_migration(timeout=120, poll=5):
    """
    Block until migration_count increases on either controller.
    Returns (who_migrated, elapsed) or (None, timeout) if timed out.
    """
    sa0 = get_status(CTRL_A)
    sb0 = get_status(CTRL_B)
    mc_a0 = sa0.get('migration_count', 0) if sa0 else 0
    mc_b0 = sb0.get('migration_count', 0) if sb0 else 0
    t0 = time.time()

    while time.time() - t0 < timeout:
        time.sleep(poll)
        sa = get_status(CTRL_A)
        sb = get_status(CTRL_B)
        mc_a = sa.get('migration_count', 0) if sa else 0
        mc_b = sb.get('migration_count', 0) if sb else 0
        if mc_a > mc_a0:
            return 'A', int(time.time() - t0)
        if mc_b > mc_b0:
            return 'B', int(time.time() - t0)

    return None, timeout


# ── Mininet topology helper ───────────────────────────────────────────────────

def build_net():
    """Build and return the Mininet network (must run as root)."""
    from mininet.net import Mininet
    from mininet.node import RemoteController, OVSSwitch
    from mininet.link import TCLink
    from mininet.log import setLogLevel
    setLogLevel('warning')

    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink, autoSetMacs=False)

    cA = net.addController('cA', controller=RemoteController, ip='127.0.0.1', port=6633)
    cB = net.addController('cB', controller=RemoteController, ip='127.0.0.1', port=6634)

    s1 = net.addSwitch('s1', cls=OVSSwitch, protocols='OpenFlow13', dpid='1')
    s2 = net.addSwitch('s2', cls=OVSSwitch, protocols='OpenFlow13', dpid='2')
    s3 = net.addSwitch('s3', cls=OVSSwitch, protocols='OpenFlow13', dpid='3')
    s4 = net.addSwitch('s4', cls=OVSSwitch, protocols='OpenFlow13', dpid='4')
    s5 = net.addSwitch('s5', cls=OVSSwitch, protocols='OpenFlow13', dpid='5')

    L  = dict(bw=10,  delay='1ms', use_htb=True)
    IL = dict(bw=100, delay='2ms', use_htb=True)

    h  = {}
    for i, (name, ip, mac) in enumerate([
        ('h1',  '10.0.1.1', '00:00:00:00:00:01'),
        ('h2',  '10.0.1.2', '00:00:00:00:00:02'),
        ('h3',  '10.0.1.3', '00:00:00:00:00:03'),
        ('h4',  '10.0.1.4', '00:00:00:00:00:04'),
        ('h5',  '10.0.1.5', '00:00:00:00:00:05'),
        ('h6',  '10.0.2.1', '00:00:00:00:00:06'),
        ('h7',  '10.0.2.2', '00:00:00:00:00:07'),
        ('h8',  '10.0.2.3', '00:00:00:00:00:08'),
        ('h9',  '10.0.2.4', '00:00:00:00:00:09'),
        ('h10', '10.0.2.5', '00:00:00:00:00:0a'),
        ('h11', '10.0.2.6', '00:00:00:00:00:0b'),
    ], 1):
        h[name] = net.addHost(name, ip=f'{ip}/16', mac=mac)

    net.addLink(h['h1'],  s1, **L); net.addLink(h['h2'], s1, **L); net.addLink(h['h3'], s1, **L)
    net.addLink(s1, s2, **L)
    net.addLink(h['h4'], s2, **L); net.addLink(h['h5'], s2, **L)

    net.addLink(h['h6'],  s3, **L); net.addLink(h['h7'], s3, **L); net.addLink(h['h8'], s3, **L)
    net.addLink(s3, s4, **L)
    net.addLink(h['h9'], s4, **L)
    net.addLink(s4, s5, **L)
    net.addLink(h['h10'], s5, **L); net.addLink(h['h11'], s5, **L)

    net.addLink(s2, s4, **IL)   # inter-domain: port4 on both s2 and s4

    net.build()
    s1.start([cA]); s2.start([cA])
    s3.start([cB]); s4.start([cB]); s5.start([cB])
    return net, h


# ── Traffic phases ────────────────────────────────────────────────────────────

def phase1(net, h, duration=60):
    """Baseline: light cross-domain and intra-domain pings."""
    banner('PHASE 1 — Baseline Traffic (60s)', B)
    cprint(G, '  Goal: Controller A ~200/s, Controller B ~600/s — both below CT=1000')

    # Light cross-domain pings
    h['h1'].cmd('ping -i 1 10.0.2.1 > /tmp/p_h1.log 2>&1 &')
    h['h2'].cmd('ping -i 1 10.0.2.2 > /tmp/p_h2.log 2>&1 &')
    h['h4'].cmd('ping -i 1 10.0.2.3 > /tmp/p_h4.log 2>&1 &')

    # Intra-domain pings in domain B
    h['h6'].cmd('ping -i 0.5 10.0.2.2 > /tmp/p_h6.log 2>&1 &')
    h['h7'].cmd('ping -i 0.5 10.0.2.3 > /tmp/p_h7.log 2>&1 &')
    h['h8'].cmd('ping -i 0.5 10.0.2.4 > /tmp/p_h8.log 2>&1 &')
    h['h9'].cmd('ping -i 0.5 10.0.2.5 > /tmp/p_h9.log 2>&1 &')

    cprint(Y, f'  Running for {duration}s …')
    _countdown(duration)

    _stop_pings(h, ['h1','h2','h4','h6','h7','h8','h9'])
    cprint(G, '  Phase 1 complete.')


def phase2(net, h, duration=60):
    """Heavy traffic on domain B — trigger DALB."""
    banner('PHASE 2 — Heavy Traffic on Domain B (60s)', Y)
    cprint(R, '  Goal: Controller B >> CT=1000 → DALB migration triggered!')

    # iperf servers on domain B
    h['h6'].cmd('iperf -s -p 5001 > /tmp/s_h6.log 2>&1 &')
    h['h9'].cmd('iperf -s -p 5002 > /tmp/s_h9.log 2>&1 &')
    h['h10'].cmd('iperf -s -p 5003 > /tmp/s_h10.log 2>&1 &')
    time.sleep(1)

    # iperf clients — heavy intra-domain B traffic
    h['h7'].cmd( 'iperf -c 10.0.2.1 -p 5001 -t 90 -b 8M > /tmp/c_h7.log  2>&1 &')
    h['h8'].cmd( 'iperf -c 10.0.2.4 -p 5002 -t 90 -b 8M > /tmp/c_h8.log  2>&1 &')
    h['h11'].cmd('iperf -c 10.0.2.5 -p 5003 -t 90 -b 8M > /tmp/c_h11.log 2>&1 &')

    # Also keep cross-domain pings from Phase 1
    h['h1'].cmd('ping -i 0.5 10.0.2.1 > /tmp/p2_h1.log 2>&1 &')
    h['h3'].cmd('ping -i 0.5 10.0.2.3 > /tmp/p2_h3.log 2>&1 &')

    cprint(Y, f'  iperf running, monitor thread will detect when B > CT …')
    _countdown(duration)

    _stop_all_traffic(h)
    cprint(G, '  Phase 2 complete. Waiting for DALB to react …')


def phase3_wait(timeout=90):
    """Wait for migration event."""
    banner('PHASE 3 — Observing DALB Migration', M)
    cprint(Y, f'  Waiting up to {timeout}s for migration …')
    who, elapsed = wait_for_migration(timeout=timeout, poll=5)
    if who:
        cprint(G, f'  ✅ Migration detected on Controller {who} after {elapsed}s!')
        return True
    else:
        cprint(Y, '  ⚠ No migration detected in time window. '
                  'Traffic may have been below CT. Check controller logs.')
        return False


def phase4_verify():
    """Verify balanced state via REST."""
    banner('PHASE 4 — Verify Balanced State', G)
    time.sleep(5)

    sa = get_status(CTRL_A)
    sb = get_status(CTRL_B)

    print_status_line('Controller A', sa, B)
    print_status_line('Controller B', sb, M)

    if sa and sb:
        la = sa.get('total_load', 0)
        lb = sb.get('total_load', 0)
        mean = (la + lb) / 2
        mx   = max(la, lb) or 1
        rho  = mean / mx
        colour = G if rho >= 0.7 else Y
        verdict = 'BALANCED' if rho >= 0.7 else 'STILL IMBALANCED'
        cprint(colour, f'\n  Cluster rho = {rho:.3f}  ({verdict})')
    else:
        cprint(R, '  Could not reach controllers for verification.')


# ── Utility ───────────────────────────────────────────────────────────────────

def _countdown(seconds):
    for remaining in range(seconds, 0, -1):
        sa = get_status(CTRL_A)
        sb = get_status(CTRL_B)
        la = sa.get('total_load', 0) if sa else -1
        lb = sb.get('total_load', 0) if sb else -1
        icon_a = f'{G}✅{RS}' if (sa and sa.get('status') == 'NORMAL') else f'{R}⚠️{RS}'
        icon_b = f'{G}✅{RS}' if (sb and sb.get('status') == 'NORMAL') else f'{R}⚠️{RS}'
        print(f'\r  t-{remaining:>3}s │ '
              f'{B}A{RS}={Y}{la:>7.1f}{RS}/s{icon_a} │ '
              f'{M}B{RS}={Y}{lb:>7.1f}{RS}/s{icon_b}   ', end='', flush=True)
        time.sleep(1)
    print()


def _stop_pings(h, host_names):
    for name in host_names:
        try:
            h[name].cmd('kill %ping 2>/dev/null; true')
        except Exception:
            pass


def _stop_all_traffic(h):
    for host in h.values():
        try:
            host.cmd('kill %ping %iperf 2>/dev/null; pkill -f iperf 2>/dev/null; true')
        except Exception:
            pass


def pingall_test(net):
    """Quick connectivity test before phases."""
    banner('CONNECTIVITY TEST — pingall', C)
    cprint(Y, '  Running pingall (may take ~30s for first run) …')
    loss = net.pingAll(timeout='5')
    colour = G if loss == 0 else Y
    cprint(colour, f'  Packet loss: {loss:.0f}%')
    return loss


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    banner('DALB AUTO DEMO — SDN Load Balancing', C)

    # ── 1. Check controllers ──────────────────────────────────────────────────
    cprint(Y, 'Checking controllers …')
    ok_a, ok_b = check_controllers()

    if not ok_a or not ok_b:
        cprint(R, '\n  One or both controllers are offline. Start them first:\n')
        cprint(W, '  # Terminal 1')
        cprint(W, '  ryu-manager controller_a.py --ofp-tcp-listen-port 6633 --wsapi-port 8080\n')
        cprint(W, '  # Terminal 2')
        cprint(W, '  ryu-manager controller_b.py --ofp-tcp-listen-port 6634 --wsapi-port 8081\n')
        sys.exit(1)

    cprint(G, f'  Controller A: {"✅ online" if ok_a else "❌ offline"}')
    cprint(G, f'  Controller B: {"✅ online" if ok_b else "❌ offline"}')

    # ── 2. Build Mininet ──────────────────────────────────────────────────────
    cprint(Y, '\nBuilding Mininet topology …')
    try:
        net, h = build_net()
    except Exception as e:
        cprint(R, f'  Failed to build topology: {e}')
        cprint(Y, '  Make sure you are running with sudo.')
        sys.exit(1)

    cprint(G, '  Topology ready. Waiting 4s for controllers to connect …')
    time.sleep(4)

    try:
        # ── 3. Connectivity test ──────────────────────────────────────────────
        pingall_test(net)
        time.sleep(2)

        # ── 4. Phase 1: Baseline ─────────────────────────────────────────────
        phase1(net, h, duration=60)

        # ── 5. Phase 2: Heavy traffic ────────────────────────────────────────
        phase2(net, h, duration=60)

        # ── 6. Phase 3: Wait for DALB ────────────────────────────────────────
        phase3_wait(timeout=90)

        # ── 7. Phase 4: Verify ───────────────────────────────────────────────
        phase4_verify()

        banner('DEMO COMPLETE', G)
        cprint(G, '  Dashboard: open dashboard.html in your browser')
        cprint(G, '  Logs: check /tmp/*.log for iperf output')
        cprint(Y, '\nPress Ctrl-C to exit and clean up Mininet.')

        from mininet.cli import CLI
        CLI(net)

    finally:
        _stop_all_traffic(h)
        net.stop()
        cprint(G, '\nMininet stopped. Run: sudo mn -c  to clean up OVS state.')


if __name__ == '__main__':
    if sys.argv[1:] == ['--help']:
        print(__doc__)
        sys.exit(0)

    if '--no-sudo-check' not in sys.argv:
        import os
        if os.geteuid() != 0:
            cprint(R, 'Error: must run as root (sudo python3 auto_demo.py)')
            sys.exit(1)

    main()
