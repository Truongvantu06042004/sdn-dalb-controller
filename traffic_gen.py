"""
Traffic Generator — guidance for 4-phase DALB demonstration.

Run this file to print the step-by-step traffic commands.
Or execute individual phases directly in the Mininet CLI.
"""


PHASES = """
╔══════════════════════════════════════════════════════════════════╗
║         DALB TRAFFIC GENERATION — 4-PHASE DEMO GUIDE            ║
╚══════════════════════════════════════════════════════════════════╝

Expected initial loads (after topology starts):
  Controller A: S1(H1,H2,H3) + S2(H4,H5)            → light load
  Controller B: S3(H6,H7,H8) + S4(H9) + S5(H10,H11) → heavier load

CT (Controller Threshold) = 1000 packet-in/s
ρ threshold = 0.7

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE 1 (0s – 60s) — Baseline traffic (both controllers below CT)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Goal: Controller A ~200/s, Controller B ~600/s — both NORMAL.

Paste into Mininet CLI:

    h1 ping -i 0.5 10.0.1.2 &
    h2 ping -i 0.5 10.0.1.3 &
    h4 ping -i 0.5 10.0.1.5 &
    h6 ping -i 0.5 10.0.2.2 &
    h7 ping -i 0.5 10.0.2.3 &
    h8 ping -i 0.5 10.0.2.4 &
    h9 ping -i 0.5 10.0.2.5 &

Monitor:
    curl -s http://localhost:8080/status | python3 -m json.tool
    curl -s http://localhost:8081/status | python3 -m json.tool

Wait ~20s for the monitoring thread to log the first LOAD TABLE.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE 2 (60s – 90s) — Spike traffic on domain B (Controller B > CT)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Goal: Controller B ~1820/s → EXCEEDED ⚠️, triggers DALB.

Start iperf servers first (Mininet CLI):

    h6 iperf -s -p 5001 &
    h9 iperf -s -p 5002 &
    h10 iperf -s -p 5003 &

Then start high-bandwidth clients:

    h7  iperf -c 10.0.2.1 -p 5001 -t 90 -b 8M &
    h8  iperf -c 10.0.2.4 -p 5002 -t 90 -b 8M &
    h11 iperf -c 10.0.2.5 -p 5003 -t 90 -b 8M &

Also add cross-domain traffic to stress inter-domain link:

    h1 iperf -s -p 5004 &
    h3 iperf -c 10.0.1.1 -p 5004 -t 90 -b 4M &

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE 3 (90s) — Observe DALB migration in Controller B terminal
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
No new commands — watch the controller B log for:

    [DALB] Load=1820/s >= CT=1000 | EXCEEDED ⚠️
    [DALB] Peer Controller A Load: 200/s
    [DALB] ρ = mean(1820,200) / max = 0.554
    [DALB] ρ=0.554 < 0.7 [C1 ✅] AND my_load=1820 is MAX [C2 ✅] → MIGRATE
    [DALB] → MIGRATION TRIGGERED!
    [MIGRATE] Migrating S3 (load=420.0) from Controller B → A
    [MIGRATE] ✅ SUCCESS: S3 → Controller A
    [MIGRATE] New CT = AdaptiveCT([1820, 200]) = 1010.0
    [MIGRATE] Expected new load: ~1400/s | ρ will improve to ~0.9

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE 4 (90s – 130s) — Verify balanced state
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Check status of both controllers:

    curl -s http://localhost:8080/status | python3 -m json.tool
    curl -s http://localhost:8081/status | python3 -m json.tool

Expected:
  Controller A: status=NORMAL, managed_switches includes S3
  Controller B: status=NORMAL or close to CT boundary
  ρ > 0.7 across the cluster

Continuous watch (open two terminals):

    watch -n 5 'curl -s http://localhost:8080/status | python3 -m json.tool'
    watch -n 5 'curl -s http://localhost:8081/status | python3 -m json.tool'

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
QUICK START COMMANDS (all 4 terminals)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Terminal 0 — clean up any old Mininet state
sudo mn -c

# Terminal 1 — Controller A
cd ~/Downloads/SDN_FInal/project
ryu-manager controller_a.py --ofp-tcp-listen-port 6633 --wsapi-port 8080

# Terminal 2 — Controller B
cd ~/Downloads/SDN_FInal/project
ryu-manager controller_b.py --ofp-tcp-listen-port 6634 --wsapi-port 8081

# Terminal 3 — Topology
cd ~/Downloads/SDN_FInal/project
sudo python3 topology.py

# In Mininet CLI — test connectivity first
mininet> pingall

# Then run phases 1-4 as described above.
"""


def main():
    print(PHASES)


if __name__ == '__main__':
    main()
