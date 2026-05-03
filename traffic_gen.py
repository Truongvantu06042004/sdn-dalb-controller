#!/usr/bin/env python3
"""
traffic_gen.py — Automated 4-phase traffic generator for SDN DALB demo.

Two modes:
  1. Inside Mininet CLI  → paste commands from each phase manually
  2. Auto mode          → sudo python3 traffic_gen.py --auto
                          (launches its own Mininet + runs all phases)

Traffic strategy
----------------
Phase 1 (BASELINE):   Light ping only → A ~50/s, B ~150/s   → both NORMAL
Phase 2 (OVERLOAD):   Heavy UDP (small packets) on domain B
                      → B ~2000+/s, A ~50/s → B OVERLOADED, ρ < 0.7
Phase 3 (MIGRATION):  Observe DALB migrate S3 or S4 from B → A
Phase 4 (RECOVERY):   Verify ρ ≥ 0.7, both controllers NORMAL

Why UDP small packets?
  TCP iperf installs flow rules quickly → Packet-in drops after 1st packet.
  UDP (-u) + small payload (-l 64) keeps generating many packets/s:
    10 Mbps link / (64 bytes × 8) = ~19 500 pkt/s → F dominates C_Load
"""

import sys
import time
import subprocess
import argparse

# ── Color output ──────────────────────────────────────────────────────────────
G  = '\033[92m'
R  = '\033[91m'
Y  = '\033[93m'
B  = '\033[94m'
W  = '\033[97m'
D  = '\033[90m'
RS = '\033[0m'

# ── Expected load estimates (for documentation) ───────────────────────────────
#   C_Load = 0.1×N + 0.8×F + 0.1×R
#   10 Mbps link, 64-byte UDP: ~9 766 pkt/s per stream
#   Conservative estimate with Mininet virtualization overhead: ~1 500 pkt/s
PHASE1_LOAD_A = '~30–80'
PHASE1_LOAD_B = '~80–200'
PHASE2_LOAD_A = '~50–100'          # no change
PHASE2_LOAD_B = '~1 500–3 000'     # 3 UDP streams
PHASE4_LOAD_A = '~600–1 200'       # after receiving migrated switch
PHASE4_LOAD_B = '~800–1 400'       # after shedding one switch

# ── Phase commands ────────────────────────────────────────────────────────────

PHASE1_CMDS = """\
# ── Stop any leftover traffic first ──
h1 kill %1 2>/dev/null; h2 kill %1 2>/dev/null; h4 kill %1 2>/dev/null
h6 kill %1 2>/dev/null; h7 kill %1 2>/dev/null; h8 kill %1 2>/dev/null
h9 kill %1 2>/dev/null

# ── Light ping traffic (0.5s interval) ──
h1 ping -i 0.5 10.0.1.2 &
h2 ping -i 0.5 10.0.1.3 &
h4 ping -i 0.5 10.0.1.5 &
h6 ping -i 0.5 10.0.2.2 &
h7 ping -i 0.5 10.0.2.3 &
h8 ping -i 0.5 10.0.2.4 &
h9 ping -i 0.5 10.0.2.5 &
"""

PHASE2_CMDS = """\
# ── Start UDP iperf servers on domain B hosts ──
h6  iperf -s -u -p 5001 &
h9  iperf -s -u -p 5002 &
h10 iperf -s -u -p 5003 &

# ── Flood domain B with small UDP packets (64-byte payload, 8 Mbps each) ──
# 8 Mbps / (64 bytes × 8 bits) = ~15 600 pkt/s per stream
# × 3 streams = ~46 000 pkt/s across B's switches → F >> CT=1000
h7  iperf -c 10.0.2.1 -u -p 5001 -t 120 -b 8M -l 64 &
h8  iperf -c 10.0.2.4 -u -p 5002 -t 120 -b 8M -l 64 &
h11 iperf -c 10.0.2.5 -u -p 5003 -t 120 -b 8M -l 64 &

# ── Also stress S3 specifically to push Controller B load highest ──
h6  iperf -s -u -p 5010 &
h8  iperf -c 10.0.2.1 -u -p 5010 -t 120 -b 6M -l 64 &
"""

PHASE2_EXTRA = """\
# ── [Optional] Add cross-domain traffic to stress inter-domain link S2↔S4 ──
h1 iperf -s -u -p 5020 &
h5 iperf -c 10.0.1.1 -u -p 5020 -t 120 -b 4M -l 64 &
"""

PHASE4_CHECK = """\
# ── Verify state after migration ──
curl -s http://localhost:8080/status | python3 -m json.tool
curl -s http://localhost:8081/status | python3 -m json.tool
"""

STOP_ALL = """\
# ── Kill all background iperf / ping processes ──
h1 kill %1 %2 2>/dev/null; h2 kill %1 2>/dev/null
h3 kill %1 2>/dev/null; h4 kill %1 2>/dev/null; h5 kill %1 %2 2>/dev/null
h6 kill %1 %2 %3 2>/dev/null; h7 kill %1 2>/dev/null
h8 kill %1 %2 2>/dev/null; h9 kill %1 2>/dev/null
h10 kill %1 2>/dev/null; h11 kill %1 2>/dev/null
"""


# ── Guide output ──────────────────────────────────────────────────────────────

def print_guide():
    sep = '─' * 66
    print(f'\n{B}╔{"═"*66}╗')
    print(f'║  SDN DALB — 4-PHASE TRAFFIC DEMO GUIDE{" "*27}║')
    print(f'╚{"═"*66}╝{RS}')

    print(f"""
{W}Architecture:{RS}
  Controller A (port 8080): S1(H1–H3) + S2(H4,H5)     → Domain 10.0.1.x
  Controller B (port 8081): S3(H6–H8) + S4(H9) + S5(H10,H11) → Domain 10.0.2.x

{W}C_Load formula (from paper Eq.1):{RS}
  C_Load = {Y}0.1{RS}×N  +  {Y}0.8{RS}×F  +  {Y}0.1{RS}×R
  N = flow table entries  |  F = pkt/s (port stats)  |  R = RTT ms
  {D}→ F dominates (80%), so more packets/s = higher load{RS}

{W}Migration triggers when:{RS}
  (1) ρ = mean(loadA, loadB) / max(loadA, loadB)  {R}< 0.7{RS}
  (2) This controller has the {R}highest{RS} load in cluster
  CT (threshold) initial = {Y}1000{RS}
""")

    # ── Phase 1 ──────────────────────────────────────────────────────────────
    print(f'{B}{sep}')
    print(f'  PHASE 1  (0 – 60 s)  BASELINE — Both controllers NORMAL')
    print(f'{sep}{RS}')
    print(f'  {D}Expected: A={PHASE1_LOAD_A}/s  B={PHASE1_LOAD_B}/s  →  both < CT=1000{RS}')
    print(f'  {D}ρ ≈ 0.85–1.0  →  NO migration{RS}\n')
    print(f'  {W}Paste into Mininet CLI:{RS}')
    for line in PHASE1_CMDS.strip().split('\n'):
        print(f'    {G if not line.startswith("#") else D}{line}{RS}')

    print(f'\n  {Y}→ Wait 20–30 s, watch Terminal 1 & 2 for LOAD TABLE output.{RS}')
    print(f'  {Y}→ Verify both show NORMAL ✅{RS}')

    # ── Phase 2 ──────────────────────────────────────────────────────────────
    print(f'\n{B}{sep}')
    print(f'  PHASE 2  (60 – 90 s)  OVERLOAD — Stress Domain B')
    print(f'{sep}{RS}')
    print(f'  {D}Expected: A={PHASE2_LOAD_A}/s  B={PHASE2_LOAD_B}/s  →  B >> CT=1000{RS}')
    print(f'  {D}ρ ≈ 0.4–0.6  →  migration WILL trigger{RS}')
    print(f'  {D}UDP 64-byte: 8 Mbps / (64×8 bits) = ~15 600 pkt/s per stream{RS}\n')
    print(f'  {W}Paste into Mininet CLI:{RS}')
    for line in PHASE2_CMDS.strip().split('\n'):
        print(f'    {G if not line.startswith("#") else D}{line}{RS}')

    print(f'\n  {W}Optional (even more traffic):{RS}')
    for line in PHASE2_EXTRA.strip().split('\n'):
        print(f'    {D}{line}{RS}')

    # ── Phase 3 ──────────────────────────────────────────────────────────────
    print(f'\n{B}{sep}')
    print(f'  PHASE 3  (90 s)  OBSERVE MIGRATION in Terminal 2 (Controller B)')
    print(f'{sep}{RS}')
    print(f"""  {D}Expected log output:{RS}
    {Y}[B][DALB] Load=2100/s >= CT=1000 | EXCEEDED ⚠️{RS}
    {Y}[B][DALB] Peer Controller A Load: 70/s{RS}
    {Y}[B][DALB] ρ = mean(2100,70) / 2100 = 0.517{RS}
    {Y}[B][DALB] ρ=0.517 < 0.7 [C1 ✅] AND my_load is MAX [C2 ✅] → MIGRATE{RS}
    {G}[B][MIGRATE] ✅ SUCCESS: S3 → Controller A{RS}
    {G}[B][MIGRATE] New CT = AdaptiveCT([2100, 70]) = 1085{RS}
""")

    # ── Phase 4 ──────────────────────────────────────────────────────────────
    print(f'{B}{sep}')
    print(f'  PHASE 4  (90–130 s)  RECOVERY — Verify balanced state')
    print(f'{sep}{RS}')
    print(f'  {D}Expected: A={PHASE4_LOAD_A}/s  B={PHASE4_LOAD_B}/s{RS}')
    print(f'  {D}ρ ≥ 0.7  →  BALANCED ✓  no more migration{RS}\n')
    print(f'  {W}Check in a new terminal:{RS}')
    for line in PHASE4_CHECK.strip().split('\n'):
        print(f'    {G}{line}{RS}')

    print(f'\n  {W}Continuous monitor (two terminals):{RS}')
    print(f'    {G}watch -n 5 \'curl -s http://localhost:8080/status | python3 -m json.tool\'{RS}')
    print(f'    {G}watch -n 5 \'curl -s http://localhost:8081/status | python3 -m json.tool\'{RS}')

    # ── Stop all ──────────────────────────────────────────────────────────────
    print(f'\n{B}{sep}')
    print(f'  STOP ALL TRAFFIC')
    print(f'{sep}{RS}')
    for line in STOP_ALL.strip().split('\n'):
        print(f'    {R if not line.startswith("#") else D}{line}{RS}')

    # ── Quick start ───────────────────────────────────────────────────────────
    print(f'\n{B}{sep}')
    print(f'  QUICK START (4 terminals)')
    print(f'{sep}{RS}')
    print(f"""  {W}Terminal 1{RS} — Controller A:
    {G}cd ~/Downloads/SDN_FInal/project && ryu-manager controller_a.py --observe-links{RS}

  {W}Terminal 2{RS} — Controller B:
    {G}cd ~/Downloads/SDN_FInal/project && ryu-manager controller_b.py --observe-links{RS}

  {W}Terminal 3{RS} — Topology:
    {G}cd ~/Downloads/SDN_FInal/project && sudo python3 topology.py{RS}

  {W}Mininet CLI{RS}:
    {G}mininet> pingall{RS}            ← wait until all hosts reachable
    {G}mininet> {RS}[paste Phase 1 commands]
    {G}mininet> {RS}[paste Phase 2 commands after 60s]

  {W}Terminal 4{RS} — Visualization:
    {G}python3 visualize.py{RS}
    {D}# or open dashboard.html in browser{RS}
""")

    print(f'\n{D}Tip: Run python3 test_scenarios.py to verify all APIs are working.{RS}\n')


# ── Auto mode ─────────────────────────────────────────────────────────────────

def run_auto():
    """Run automated demo by launching Mininet and executing traffic phases."""
    try:
        from mininet.net import Mininet
        from mininet.node import RemoteController, OVSSwitch
        from mininet.link import TCLink
        from mininet.log import setLogLevel
    except ImportError:
        print(f'{R}mininet not found. Run with sudo, or use guide mode (no --auto).{RS}')
        sys.exit(1)

    print(f'{B}Auto demo mode — launching Mininet topology...{RS}')
    print(f'{Y}Make sure Controller A (port 8080) and Controller B (port 8081) are running!{RS}')
    print(f'{Y}Press Ctrl+C to abort at any time.{RS}\n')

    # Import topology builder
    sys.path.insert(0, '.')
    try:
        from topology import build_topology
    except ImportError:
        print(f'{R}Cannot import topology.py — run from the project directory.{RS}')
        sys.exit(1)

    setLogLevel('warning')
    net = build_topology()

    def mn(host_name, cmd):
        """Run cmd on a named host."""
        return net.get(host_name).cmd(cmd)

    try:
        # ── Connectivity check ────────────────────────────────────────────
        print(f'{B}[AUTO] Waiting 5s for controller connections...{RS}')
        time.sleep(5)
        print(f'{B}[AUTO] Running pingall...{RS}')
        net.pingAll(timeout='3')

        # ── Phase 1: Baseline ─────────────────────────────────────────────
        print(f'\n{G}[PHASE 1] Starting baseline traffic (60 s)...{RS}')
        mn('h1', 'ping -i 0.5 10.0.1.2 &')
        mn('h2', 'ping -i 0.5 10.0.1.3 &')
        mn('h4', 'ping -i 0.5 10.0.1.5 &')
        mn('h6', 'ping -i 0.5 10.0.2.2 &')
        mn('h7', 'ping -i 0.5 10.0.2.3 &')
        mn('h8', 'ping -i 0.5 10.0.2.4 &')
        mn('h9', 'ping -i 0.5 10.0.2.5 &')
        print(f'  {D}Waiting 60 s — check controllers are NORMAL...{RS}')
        time.sleep(60)

        # ── Phase 2: Overload ─────────────────────────────────────────────
        print(f'\n{Y}[PHASE 2] Starting overload traffic on Domain B...{RS}')
        # UDP iperf servers
        mn('h6',  'iperf -s -u -p 5001 &')
        mn('h9',  'iperf -s -u -p 5002 &')
        mn('h10', 'iperf -s -u -p 5003 &')
        mn('h6',  'iperf -s -u -p 5010 &')
        time.sleep(1)
        # UDP iperf clients — small packets, 8 Mbps
        mn('h7',  'iperf -c 10.0.2.1 -u -p 5001 -t 120 -b 8M -l 64 &')
        mn('h8',  'iperf -c 10.0.2.4 -u -p 5002 -t 120 -b 8M -l 64 &')
        mn('h11', 'iperf -c 10.0.2.5 -u -p 5003 -t 120 -b 8M -l 64 &')
        mn('h8',  'iperf -c 10.0.2.1 -u -p 5010 -t 120 -b 6M -l 64 &')
        print(f'  {D}Waiting 30 s — watching for migration...{RS}')
        time.sleep(30)

        # ── Phase 3 status ────────────────────────────────────────────────
        print(f'\n{B}[PHASE 3] Checking migration status...{RS}')
        for port in [8080, 8081]:
            try:
                import requests
                r = requests.get(f'http://localhost:{port}/status', timeout=3)
                d = r.json()
                print(f'  Controller {d["controller"]}: '
                      f'load={d["total_load"]:.1f}  status={d["status"]}  '
                      f'ρ={d["rho"]:.3f}  '
                      f'migrations={d["migration_count"]}  '
                      f'switches={d["managed_switches"]}')
            except Exception as e:
                print(f'  port {port}: {e}')

        # ── Phase 4 ───────────────────────────────────────────────────────
        print(f'\n{G}[PHASE 4] Waiting 30 s for system to stabilize...{RS}')
        time.sleep(30)
        print(f'{B}[PHASE 4] Final state:{RS}')
        for port in [8080, 8081]:
            try:
                import requests
                r = requests.get(f'http://localhost:{port}/status', timeout=3)
                d = r.json()
                ok = '✅' if d['status'] == 'NORMAL' else '⚠️'
                print(f'  Controller {d["controller"]} {ok}: '
                      f'load={d["total_load"]:.1f}  ρ={d["rho"]:.3f}  '
                      f'switches={d["managed_switches"]}')
            except Exception as e:
                print(f'  port {port}: {e}')

        print(f'\n{G}Demo complete. Opening Mininet CLI for manual inspection...{RS}')
        from mininet.cli import CLI
        CLI(net)

    except KeyboardInterrupt:
        print(f'\n{Y}Interrupted by user.{RS}')
    finally:
        net.stop()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='SDN DALB traffic generator demo')
    parser.add_argument('--auto', action='store_true',
                        help='Run automated Mininet demo (requires sudo + controllers running)')
    args = parser.parse_args()

    if args.auto:
        run_auto()
    else:
        print_guide()


if __name__ == '__main__':
    main()
