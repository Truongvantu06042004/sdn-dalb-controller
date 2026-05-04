#!/usr/bin/env python3
"""
traffic_gen.py — 5-Phase Traffic Demo Guide for SDN DALB.

Two modes:
  1. Guide mode  (default) → print step-by-step commands to paste into Mininet CLI
  2. Auto mode  (--auto)   → sudo python3 traffic_gen.py --auto
                              launches Mininet internally, runs all 5 phases

Traffic strategy
----------------
Phase 1 (BASELINE):    Light ping only          → A ~50/s,   B ~150/s  → NORMAL
Phase 2 (OVERLOAD B):  Heavy UDP on domain B    → B ~2000/s             → B EXCEEDED
Phase 3 (MIGRATE B→A): DALB migrates S4 or S5 from B → A               → ρ recovers
Phase 4 (VERIFY):      Confirm balanced state   → ρ ≥ 0.7, both NORMAL
Phase 5 (OVERLOAD A):  Heavy UDP on domain A    → A ~2000/s             → A EXCEEDED
                        DALB migrates S1 or S2 from A → B               → ρ recovers

Why UDP small packets?
  TCP installs flow rules quickly → Packet-in drops after 1st packet.
  UDP (-u) + 64-byte payload keeps port rx_packets rate high:
    8 Mbps / (64 bytes × 8 bits) ≈ 15 600 pkt/s per stream → F >> CT=1000
"""

import sys
import time
import argparse

# ── Color output ──────────────────────────────────────────────────────────────
G  = '\033[92m'
R  = '\033[91m'
Y  = '\033[93m'
B  = '\033[94m'
W  = '\033[97m'
D  = '\033[90m'
RS = '\033[0m'

# ── Load estimates (approximate, vary with virtualization overhead) ────────────
PHASE1_LOAD_A  = '~30–80'
PHASE1_LOAD_B  = '~80–200'
PHASE2_LOAD_A  = '~50–100'
PHASE2_LOAD_B  = '~1 500–3 000'
PHASE4_LOAD_A  = '~600–1 200'    # A received migrated B switches
PHASE4_LOAD_B  = '~800–1 400'    # B shed load
PHASE5_LOAD_A  = '~1 500–3 000'  # A flooded with domain A traffic
PHASE5_LOAD_B  = '~50–150'       # B is quiet

# ── Phase commands ────────────────────────────────────────────────────────────

PHASE1_CMDS = """\
# ── Stop any leftover traffic first ──
sh pkill -f 'iperf' 2>/dev/null; sh pkill -f 'ping -i' 2>/dev/null

# ── Light background pings (generates low constant Packet-in traffic) ──
h1 ping -i 0.5 10.0.1.2 &
h2 ping -i 0.5 10.0.1.3 &
h4 ping -i 0.5 10.0.1.5 &
h6 ping -i 0.5 10.0.2.2 &
h7 ping -i 0.5 10.0.2.3 &
h8 ping -i 0.5 10.0.2.4 &
h9 ping -i 0.5 10.0.2.5 &
"""

PHASE2_CMDS = """\
# ── Start UDP iperf servers on domain B ──
h6  iperf -s -u -p 5001 &
h9  iperf -s -u -p 5002 &
h10 iperf -s -u -p 5003 &
h6  iperf -s -u -p 5010 &

# ── Flood domain B: 8 Mbps 64-byte UDP (~15 600 pkt/s per stream) ──
h7  iperf -c 10.0.2.1 -u -p 5001 -t 120 -b 8M -l 64 &
h8  iperf -c 10.0.2.4 -u -p 5002 -t 120 -b 8M -l 64 &
h11 iperf -c 10.0.2.5 -u -p 5003 -t 120 -b 8M -l 64 &
h8  iperf -c 10.0.2.1 -u -p 5010 -t 120 -b 6M -l 64 &
"""

PHASE5_CMDS = """\
# ── Stop Phase 2 iperf first ──
sh pkill -f 'iperf' 2>/dev/null

# ── Start UDP iperf servers on domain A ──
h1  iperf -s -u -p 6001 &
h2  iperf -s -u -p 6002 &
h3  iperf -s -u -p 6003 &
h4  iperf -s -u -p 6004 &
h1  iperf -s -u -p 6010 &

# ── Flood domain A: same recipe — 8 Mbps 64-byte UDP ──
h2  iperf -c 10.0.1.1 -u -p 6001 -t 120 -b 8M -l 64 &
h3  iperf -c 10.0.1.2 -u -p 6002 -t 120 -b 8M -l 64 &
h5  iperf -c 10.0.1.3 -u -p 6003 -t 120 -b 8M -l 64 &
h5  iperf -c 10.0.1.4 -u -p 6004 -t 120 -b 8M -l 64 &
h3  iperf -c 10.0.1.1 -u -p 6010 -t 120 -b 6M -l 64 &
"""

STOP_ALL = """\
sh pkill -f 'iperf' 2>/dev/null; sh pkill -f 'ping -i' 2>/dev/null
"""

VERIFY_CMDS = """\
curl -s http://localhost:8080/status | python3 -m json.tool
curl -s http://localhost:8081/status | python3 -m json.tool
"""


# ── Guide output ──────────────────────────────────────────────────────────────

def print_guide():
    sep = '─' * 66
    print(f'\n{B}╔{"═"*66}╗')
    print(f'║  SDN DALB — 5-PHASE TRAFFIC DEMO GUIDE{" "*27}║')
    print(f'╚{"═"*66}╝{RS}')

    print(f"""
{W}Architecture:{RS}
  Controller A (port 8080): S1(H1–H3) + S2(H4,H5)             Domain 10.0.1.x
  Controller B (port 8081): S3(H6–H8) + S4(H9) + S5(H10,H11)  Domain 10.0.2.x

{W}C_Load formula (from paper Eq.1):{RS}
  C_Load = {Y}0.1{RS}×N  +  {Y}0.8{RS}×F  +  {Y}0.1{RS}×R
  N = flow table entries  |  F = pkt/s (port stats)  |  R = RTT ms
  {D}→ F dominates (80%), so more packets/s = higher load{RS}

{W}Migration triggers when BOTH are true:{RS}
  C1: ρ = mean(loads) / max(loads)  {R}< 0.7{RS}   (cluster imbalanced)
  C2: This controller has the {R}highest{RS} load   (only busiest migrates)
  CT (initial threshold) = {Y}1 000{RS}
""")

    # ── Phase 1 ──────────────────────────────────────────────────────────────
    _phase_header('1', '0–60 s', 'BASELINE — Both controllers NORMAL', sep)
    print(f'  {D}Expected: A={PHASE1_LOAD_A}/s  B={PHASE1_LOAD_B}/s  →  both < CT=1000{RS}')
    print(f'  {D}ρ ≈ 0.85–1.0  →  NO migration{RS}\n')
    _print_cmds(PHASE1_CMDS)
    print(f'\n  {Y}→ Wait 20–30 s, watch controllers for LOAD TABLE.{RS}')
    print(f'  {Y}→ Verify both show NORMAL ✅{RS}')

    # ── Phase 2 ──────────────────────────────────────────────────────────────
    _phase_header('2', '60–90 s', 'OVERLOAD Domain B', sep)
    print(f'  {D}Expected: B={PHASE2_LOAD_B}/s  >>  CT=1000{RS}')
    print(f'  {D}ρ ≈ 0.4–0.6  →  migration WILL trigger{RS}')
    print(f'  {D}8 Mbps / (64 B × 8 bits) = ~15 600 pkt/s per stream{RS}\n')
    _print_cmds(PHASE2_CMDS)

    # ── Phase 3 ──────────────────────────────────────────────────────────────
    _phase_header('3', '~90 s', 'OBSERVE Migration B → A  (watch Terminal 2)', sep)
    print(f"""  {D}Expected log on Controller B:{RS}
    {Y}[B][DALB] Load=2100/s >= CT=1000 | EXCEEDED ⚠️{RS}
    {Y}[B][DALB] ρ=0.52 < 0.70 [C1 ✅] AND my_load is MAX [C2 ✅] → MIGRATE{RS}
    {G}[B][MIGRATE] ✅ SUCCESS: S4 → Controller A{RS}
    {G}[A][MIGRATE] S4 → MASTER on Controller A{RS}
""")
    print(f'  {W}Verify in a new terminal:{RS}')
    _print_cmds(VERIFY_CMDS, color=G)

    # ── Phase 4 ──────────────────────────────────────────────────────────────
    _phase_header('4', '90–130 s', 'VERIFY Recovery — ρ ≥ 0.7', sep)
    print(f'  {D}Expected: A={PHASE4_LOAD_A}/s  B={PHASE4_LOAD_B}/s  →  ρ ≥ 0.7 ✓{RS}')
    print(f'  {D}Both controllers show NORMAL, migration_count ≥ 1 on B{RS}')
    print(f'  {D}A migration_log shows received switch from B{RS}')
    print(f'\n  {W}Continuous monitor:{RS}')
    print(f'    {G}watch -n 5 \'curl -s http://localhost:8080/status | python3 -m json.tool\'{RS}')
    print(f'    {G}watch -n 5 \'curl -s http://localhost:8081/status | python3 -m json.tool\'{RS}')

    # ── Phase 5 ──────────────────────────────────────────────────────────────
    _phase_header('5', '130–180 s', 'OVERLOAD Domain A → Migration A → B', sep)
    print(f'  {D}Purpose: demonstrate bidirectional load balancing{RS}')
    print(f'  {D}Expected: A={PHASE5_LOAD_A}/s  >>  CT=1000{RS}')
    print(f'  {D}DALB on A: ρ < 0.7 AND A is max → migrate S1 or S2 → B{RS}\n')
    _print_cmds(PHASE5_CMDS)
    print(f'\n  {D}Expected log on Controller A:{RS}')
    print(f'    {Y}[A][DALB] Load=2500/s >= CT=1000 | EXCEEDED ⚠️{RS}')
    print(f'    {Y}[A][DALB] ρ=0.45 < 0.70 [C1 ✅] AND my_load is MAX [C2 ✅] → MIGRATE{RS}')
    print(f'    {G}[A][MIGRATE] ✅ SUCCESS: S1 → Controller B{RS}')
    print(f'    {G}[B][MIGRATE] S1 → MASTER on Controller B{RS}')
    print(f'\n  {Y}→ After migration, ρ should recover to ≥ 0.7 again ✓{RS}')

    # ── Stop all ──────────────────────────────────────────────────────────────
    print(f'\n{B}{sep}')
    print(f'  STOP ALL TRAFFIC')
    print(f'{sep}{RS}')
    _print_cmds(STOP_ALL, color=R)

    # ── Quick start ───────────────────────────────────────────────────────────
    print(f'\n{B}{sep}')
    print(f'  QUICK START (4 terminals)')
    print(f'{sep}{RS}')
    print(f"""
  {W}Terminal 1 — Open dashboard FIRST (so you can watch live):{RS}
    {G}firefox ~/Downloads/SDN_FInal/project/dashboard.html{RS}
    {D}# or: python3 visualize.py  (Matplotlib window){RS}

  {W}Terminal 2 — Controller A:{RS}
    {G}cd ~/Downloads/SDN_FInal/project && ryu-manager controller_a.py{RS}
    {D}# Wait for: wsgi starting up on http://0.0.0.0:8080{RS}

  {W}Terminal 3 — Controller B:{RS}
    {G}cd ~/Downloads/SDN_FInal/project && ryu-manager controller_b.py{RS}
    {D}# Wait for: wsgi starting up on http://0.0.0.0:8081{RS}

  {W}Terminal 4 — Topology:{RS}
    {G}cd ~/Downloads/SDN_FInal/project && sudo python3 topology.py{RS}
    {D}# First command in Mininet CLI:{RS}
    {G}mininet> pingall{RS}   ← run twice if some X appear

  {W}Then paste Phase 1 → 2 → 3 → 4 → 5 commands into Mininet CLI.{RS}
""")
    print(f'\n{D}Tip: Run python3 test_scenarios.py to verify all APIs first.{RS}\n')


# ── Helper printers ───────────────────────────────────────────────────────────

def _phase_header(num, timerange, title, sep):
    print(f'\n{B}{sep}')
    print(f'  PHASE {num}  ({timerange})  {title}')
    print(f'{sep}{RS}')


def _print_cmds(block, color=G):
    for line in block.strip().split('\n'):
        c = D if line.startswith('#') else color
        print(f'    {c}{line}{RS}')


# ── Auto mode ─────────────────────────────────────────────────────────────────

def run_auto():
    """Automated 5-phase demo — launches Mininet and executes all traffic phases."""
    try:
        from mininet.log import setLogLevel
    except ImportError:
        print(f'{R}mininet not found. Run with sudo.{RS}')
        sys.exit(1)

    print(f'{B}Auto demo — launching Mininet topology...{RS}')
    print(f'{Y}Make sure Controller A (:8080) and Controller B (:8081) are running!{RS}')
    print(f'{Y}Press Ctrl+C to abort at any time.{RS}\n')

    sys.path.insert(0, '.')
    try:
        from topology import build_topology
    except ImportError:
        print(f'{R}Cannot import topology.py — run from the project directory.{RS}')
        sys.exit(1)

    setLogLevel('warning')
    net = build_topology()

    def mn(host, cmd):
        return net.get(host).cmd(cmd)

    def status():
        try:
            import requests
            for port, letter in [(8080, 'A'), (8081, 'B')]:
                r = requests.get(f'http://localhost:{port}/status', timeout=3)
                d = r.json()
                ok = '✅' if d['status'] == 'NORMAL' else '⚠️ '
                print(f'  Ctrl {letter} {ok}: load={d["total_load"]:7.1f}  '
                      f'ρ={d["rho"]:.3f}  mig={d["migration_count"]}  '
                      f'sw={d["managed_switches"]}')
        except Exception as e:
            print(f'  status error: {e}')

    try:
        # ── Wait & connectivity ───────────────────────────────────────────
        print(f'{B}[AUTO] Waiting 5 s for OF connections...{RS}')
        time.sleep(5)
        print(f'{B}[AUTO] pingall...{RS}')
        net.pingAll(timeout='3')

        # ── Phase 1: Baseline ─────────────────────────────────────────────
        print(f'\n{G}[PHASE 1] Baseline pings (60 s)...{RS}')
        for h, dst in [('h1','10.0.1.2'),('h2','10.0.1.3'),('h4','10.0.1.5'),
                       ('h6','10.0.2.2'),('h7','10.0.2.3'),('h8','10.0.2.4'),
                       ('h9','10.0.2.5')]:
            mn(h, f'ping -i 0.5 {dst} &')
        print(f'  {D}Waiting 60 s — verify controllers show NORMAL...{RS}')
        time.sleep(60)
        print(f'  State after Phase 1:'); status()

        # ── Phase 2: Overload B ───────────────────────────────────────────
        print(f'\n{Y}[PHASE 2] Overloading Controller B (30 s)...{RS}')
        for h, port in [('h6','5001'),('h9','5002'),('h10','5003'),('h6','5010')]:
            mn(h, f'iperf -s -u -p {port} &')
        time.sleep(1)
        mn('h7',  'iperf -c 10.0.2.1 -u -p 5001 -t 180 -b 8M -l 64 &')
        mn('h8',  'iperf -c 10.0.2.4 -u -p 5002 -t 180 -b 8M -l 64 &')
        mn('h11', 'iperf -c 10.0.2.5 -u -p 5003 -t 180 -b 8M -l 64 &')
        mn('h8',  'iperf -c 10.0.2.1 -u -p 5010 -t 180 -b 6M -l 64 &')
        print(f'  {D}Waiting 30 s for DALB to detect B overload...{RS}')
        time.sleep(30)

        # ── Phase 3: Status after B migration ────────────────────────────
        print(f'\n{B}[PHASE 3] State after B → A migration:{RS}'); status()

        # ── Phase 4: Verify recovery ──────────────────────────────────────
        print(f'\n{G}[PHASE 4] Waiting 30 s to verify balanced state...{RS}')
        time.sleep(30)
        print(f'  State after Phase 4:'); status()

        # ── Phase 5: Overload A → migrate A → B ──────────────────────────
        print(f'\n{Y}[PHASE 5] Stopping B traffic, overloading Controller A...{RS}')
        net.get('h7').cmd('pkill -f iperf 2>/dev/null')
        net.get('h8').cmd('pkill -f iperf 2>/dev/null')
        net.get('h11').cmd('pkill -f iperf 2>/dev/null')
        time.sleep(2)

        for h, port in [('h1','6001'),('h2','6002'),('h3','6003'),
                        ('h4','6004'),('h1','6010')]:
            mn(h, f'iperf -s -u -p {port} &')
        time.sleep(1)
        mn('h2', 'iperf -c 10.0.1.1 -u -p 6001 -t 180 -b 8M -l 64 &')
        mn('h3', 'iperf -c 10.0.1.2 -u -p 6002 -t 180 -b 8M -l 64 &')
        mn('h5', 'iperf -c 10.0.1.3 -u -p 6003 -t 180 -b 8M -l 64 &')
        mn('h5', 'iperf -c 10.0.1.4 -u -p 6004 -t 180 -b 8M -l 64 &')
        mn('h3', 'iperf -c 10.0.1.1 -u -p 6010 -t 180 -b 6M -l 64 &')
        print(f'  {D}Waiting 30 s for DALB to detect A overload and migrate A → B...{RS}')
        time.sleep(30)

        print(f'\n{B}[PHASE 5] Final state after A → B migration:{RS}'); status()

        print(f'\n{G}Demo complete. Opening Mininet CLI...{RS}')
        from mininet.cli import CLI
        CLI(net)

    except KeyboardInterrupt:
        print(f'\n{Y}Interrupted.{RS}')
    finally:
        net.get('h1').cmd('pkill -f iperf 2>/dev/null; pkill -f ping 2>/dev/null')
        net.stop()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='SDN DALB 5-phase traffic demo')
    parser.add_argument('--auto', action='store_true',
                        help='Run automated Mininet demo (requires sudo + controllers running)')
    args = parser.parse_args()
    if args.auto:
        run_auto()
    else:
        print_guide()


if __name__ == '__main__':
    main()
