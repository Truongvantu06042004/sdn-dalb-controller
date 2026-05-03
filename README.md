# SDN Load Balancing with DALB Algorithm

Distributed SDN controller load balancing using Ryu + OpenFlow 1.3, implementing the DALB algorithm from the IEEE TrustCom 2014 paper *"A Load Balancing Strategy for SDN Controller based on Distributed Decision"* (Beihang University).

---

## Architecture

```
           CONTROLLER A (port 6633)        CONTROLLER B (port 6634)
           REST API: :8080                 REST API: :8081
                 │                                │
        ┌────────┴────────┐             ┌─────────┴──────────┐
        │  Domain A       │             │  Domain B           │
        │                 │             │                     │
      [S1]─h1,h2,h3    [S2]─h4,h5   [S3]─h6,h7,h8  [S4]─h9  [S5]─h10,h11
        └────────────────┘               └─────────────────────┘
                         │                    │
                    S2 ──┼──── 100Mbps ───────┼── S4
                    (port4)    2ms delay    (port4)
                    (inter-domain link)

Each switch connects to BOTH controllers simultaneously:
  S1, S2  →  cA = MASTER,  cB = SLAVE
  S3,S4,S5 →  cB = MASTER,  cA = SLAVE
Migration: OFPRoleRequest swaps MASTER/SLAVE ownership
```

### Files

| File | Description |
|------|-------------|
| `controller_a.py` | Controller A — OpenFlow 1.3, L2 switch, ARP proxy, DALB, REST :8080 |
| `controller_b.py` | Controller B — identical logic, different config, REST :8081 |
| `topology.py` | Mininet topology — 2 controllers, 5 switches, 11 hosts |
| `dalb_module.py` | Pure DALB math — all formulas from paper |
| `dashboard.html` | Web dashboard — live charts (open in browser) |
| `visualize.py` | Matplotlib real-time visualization |
| `traffic_gen.py` | Automated traffic generator |

---

## DALB Algorithm

### Formula 1 — Switch Load

```
C_Load(switch) = w1×N + w2×F + w3×R
```

| Variable | Meaning | Weight |
|----------|---------|--------|
| N | Number of flow table entries | w1 = 0.1 |
| F | Average Packet-in arrival rate (pkts/s) | w2 = 0.8 |
| R | Round-trip time switch→controller (ms) | w3 = 0.1 |

Weights sum to 1.0. F dominates (0.8) because traffic rate is the primary bottleneck.

### Formula 2 — Controller Load

```
L_controller = Σ C_Load(switch_i)   for all switches owned
```

### Formula 3 — Load Balance Index ρ

```
ρ = mean(loads) / max(loads)    ρ ∈ [0, 1]
```

- ρ → 1.0 : cluster is balanced
- ρ < 0.7 : imbalanced → migration should trigger

### Algorithm 1 — AdaptiveCT

```
δ = mean(all controller loads)
CT = δ    if δ > ICT (1000)
CT = ICT  otherwise
```

Raises the threshold when the whole cluster is overloaded to avoid thrashing.

### Formula 4 — Migration Candidate Selection

```
L_migrate ≤ (L_overloaded − L_target) / 2
```

Pick the switch with the largest load that still satisfies the constraint.

### Migration Decision (both conditions must be true)

- **C1**: ρ < 0.7 — cluster is imbalanced
- **C2**: my_load == max(all loads) — only the busiest controller initiates

---

## Installation

```bash
# Python dependencies
pip install ryu requests matplotlib

# System tools
sudo apt-get install mininet iperf openvswitch-switch

# Verify versions
ryu-manager --version     # 4.x
mn --version              # 2.x
ovs-vsctl --version       # 2.x
```

---

## Running the Demo (3 terminals required)

### Terminal 1 — Start Controller A

```bash
cd ~/Downloads/SDN_FInal/project
ryu-manager controller_a.py
```

Wait for this line before continuing:
```
(XXXXX) wsgi starting up on http://0.0.0.0:8080
```

### Terminal 2 — Start Controller B

```bash
cd ~/Downloads/SDN_FInal/project
ryu-manager controller_b.py
```

Wait for:
```
(XXXXX) wsgi starting up on http://0.0.0.0:8081
```

### Terminal 3 — Start Topology

```bash
cd ~/Downloads/SDN_FInal/project
sudo python3 topology.py
```

If you forget a controller, the script exits immediately:
```
[ERROR] Controller B is NOT running on 127.0.0.1:6634
        Start it first:  ryu-manager controller_b.py
Aborting.
```

When topology starts correctly, both controllers log switch connections:
```
# Controller A log:
[SWITCH] S1 connected — OF1.3 | ctrl=A
[SWITCH] S2 connected — OF1.3 | ctrl=A
[SWITCH] S3 → SLAVE on ctrl=A       ← A holds SLAVE for B's switches
[SWITCH] S4 → SLAVE on ctrl=A
[SWITCH] S5 → SLAVE on ctrl=A

# Controller B log:
[SWITCH] S3 connected — OF1.3 | ctrl=B
[SWITCH] S4 connected — OF1.3 | ctrl=B
[SWITCH] S5 connected — OF1.3 | ctrl=B
[SWITCH] S1 → SLAVE on ctrl=B       ← B holds SLAVE for A's switches
[SWITCH] S2 → SLAVE on ctrl=B
```

### Test Connectivity

In the Mininet CLI (Terminal 3):

```
mininet> pingall
```

Expected (after ~30s for ARP learning):
```
*** Results: 0% dropped (110/110 received)
```

If some X appear on the first run, run `pingall` again — second run will be 100%.

---

## Generating Traffic — Manual

### Phase 1: Baseline (both controllers NORMAL)

Start background pings — generates low constant Packet-In traffic:

```
mininet> h1 ping -i 0.5 10.0.1.2 &
mininet> h2 ping -i 0.5 10.0.1.3 &
mininet> h4 ping -i 0.5 10.0.1.5 &
mininet> h6 ping -i 0.5 10.0.2.2 &
mininet> h7 ping -i 0.5 10.0.2.3 &
mininet> h8 ping -i 0.5 10.0.2.4 &
mininet> h9 ping -i 0.5 10.0.2.5 &
```

Expected controller load tables (every 10s):
```
Controller A: Total ~14/s | CT=1000 | NORMAL
Controller B: Total ~42/s | CT=1000 | NORMAL
ρ ≈ 0.75 → balanced, no migration
```

### Phase 2: Overload Domain B

Start iperf UDP servers on domain B:
```
mininet> h6  iperf -s -u -p 5001 &
mininet> h9  iperf -s -u -p 5002 &
mininet> h10 iperf -s -u -p 5003 &
mininet> h6  iperf -s -u -p 5010 &
```

Send high-rate 64-byte UDP floods (~15,600 pkt/s per stream):
```
mininet> h7  iperf -c 10.0.2.1 -u -p 5001 -t 120 -b 8M -l 64 &
mininet> h8  iperf -c 10.0.2.4 -u -p 5002 -t 120 -b 8M -l 64 &
mininet> h11 iperf -c 10.0.2.5 -u -p 5003 -t 120 -b 8M -l 64 &
mininet> h8  iperf -c 10.0.2.1 -u -p 5010 -t 120 -b 6M -l 64 &
```

Rate math: 8 Mbps ÷ (64 bytes × 8 bits/byte) ≈ 15,625 pkt/s per stream

Expected logs within 10–30 seconds:
```
Controller B: Total ~2100/s | CT=1000 | EXCEEDED ⚠️
Controller B: ρ=0.52 < 0.70 [C1 ✅] AND my_load=2100 is MAX [C2 ✅] → MIGRATE
Controller B: [MIGRATE] S3 (load=700.0) Controller B → A
Controller A: [MIGRATE] S3 → MASTER on Controller A
```

### Phase 3: Verify Migration

```bash
# Controller A now manages S1, S2, S3
curl http://127.0.0.1:8080/status

# Controller B now manages only S4, S5
curl http://127.0.0.1:8081/status
```

Look for `"migration_count": 1` and `"managed_switches": ["S1","S2","S3"]` on Controller A.

Expected loads after migration:
```
Controller A: ~700/s (received S3's load)
Controller B: ~1400/s (shed S3's load)
ρ = 0.70+ → balanced
```

### Stop All Traffic

```
mininet> sh pkill iperf
mininet> sh pkill ping
```

---

## Automated Demo

```bash
# Terminal 1 — Controller A must be running
ryu-manager controller_a.py

# Terminal 2 — Controller B must be running
ryu-manager controller_b.py

# Terminal 3 — Interactive guided demo
python3 traffic_gen.py

# Terminal 3 — Fully automatic (no input needed)
sudo python3 traffic_gen.py --auto
```

`--auto` mode sequence:
1. Verifies both controllers are up
2. Launches Mininet topology internally
3. Waits for switches to connect
4. Runs baseline ping traffic (20s)
5. Starts iperf flood on domain B
6. Waits 60s for DALB to trigger migration
7. Prints final load comparison table

---

## Web Dashboard

Open directly in browser — no web server needed:

```bash
xdg-open dashboard.html
# or
firefox dashboard.html
```

Features:
- Live bar charts: Controller A and B load
- ρ history chart with 0.7 threshold line
- Per-switch table: Switch | Flows(N) | Rate(F)/s | RTT(R)ms | C_Load
- Migration event log
- Auto-refresh every 3 seconds

---

## Matplotlib Visualization

```bash
python3 visualize.py
```

4-panel live plot (similar to paper Fig. 6):
- Top-left: Controller load over time
- Top-right: ρ index over time
- Bottom-left: Per-switch C_Load bars
- Bottom-right: System status text

Migration events appear as vertical purple dashed lines.

---

## REST API Reference

All responses are JSON with CORS enabled (`Access-Control-Allow-Origin: *`).

### GET /load — Current load data

```bash
curl http://127.0.0.1:8080/load
curl http://127.0.0.1:8081/load
```

```json
{
  "controller": "A",
  "total_load": 14.3,
  "ct": 1000.0,
  "switches": [
    {"name": "S1", "dpid": 1, "load": 8.2, "flows": 4, "rate": 8.0, "rtt_ms": 1.2},
    {"name": "S2", "dpid": 2, "load": 6.1, "flows": 3, "rate": 6.0, "rtt_ms": 1.1}
  ],
  "timestamp": "19:23:45"
}
```

### GET /status — Full system status

```bash
curl http://127.0.0.1:8081/status
```

```json
{
  "controller": "B",
  "total_load": 2104.5,
  "peer_load": 14.3,
  "peer_status": "ONLINE",
  "ct": 1000.0,
  "rho": 0.519,
  "status": "OVERLOADED",
  "managed_switches": ["S3", "S4", "S5"],
  "migration_count": 0,
  "migration_log": [],
  "uptime_seconds": 87
}
```

### GET /arp?ip=x.x.x.x — MAC lookup (cross-domain ARP proxy)

```bash
curl "http://127.0.0.1:8080/arp?ip=10.0.1.1"
```

```json
{"ip": "10.0.1.1", "mac": "00:00:00:00:00:01"}
```

### POST /migrate — Accept a migrated switch

```bash
curl -X POST http://127.0.0.1:8080/migrate \
     -H "Content-Type: application/json" \
     -d '{"dpid": 3, "name": "S3"}'
```

```json
{"status": "ok", "message": "S3 is now MASTER on Controller A"}
```

---

## Host / IP / Switch Reference

| Host | IP | MAC | Switch | Domain | Controller |
|------|----|-----|--------|--------|------------|
| h1 | 10.0.1.1/16 | 00:00:00:00:00:01 | S1 | A | A |
| h2 | 10.0.1.2/16 | 00:00:00:00:00:02 | S1 | A | A |
| h3 | 10.0.1.3/16 | 00:00:00:00:00:03 | S1 | A | A |
| h4 | 10.0.1.4/16 | 00:00:00:00:00:04 | S2 | A | A |
| h5 | 10.0.1.5/16 | 00:00:00:00:00:05 | S2 | A | A |
| h6 | 10.0.2.1/16 | 00:00:00:00:00:06 | S3 | B | B |
| h7 | 10.0.2.2/16 | 00:00:00:00:00:07 | S3 | B | B |
| h8 | 10.0.2.3/16 | 00:00:00:00:00:08 | S3 | B | B |
| h9 | 10.0.2.4/16 | 00:00:00:00:00:09 | S4 | B | B |
| h10 | 10.0.2.5/16 | 00:00:00:00:00:0a | S5 | B | B |
| h11 | 10.0.2.6/16 | 00:00:00:00:00:0b | S5 | B | B |

Inter-domain link: S2 port 4 ↔ S4 port 4 (100 Mbps, 2 ms delay)

---

## Troubleshooting

**Topology exits with "Controller B is NOT running"**
→ Start both `ryu-manager` processes first and wait for `wsgi starting up`, then run topology.

**pingall shows X for all domain B hosts (h6–h11)**
→ Controller B was not running when topology started. Switches already connected without it.
Fix: `exit` → `sudo mn -c` → restart all three in order.

**pingall shows some X on first run**
→ Normal. ARP tables are empty on first run. Run `pingall` a second time.

**Controller load shows 0.0/s despite traffic**
→ Monitor loop polls every 10 seconds. Wait 10–15 seconds after starting iperf.

**Migration never triggers**
→ Check that iperf server is listening before client starts.
→ Watch Controller B log for `EXCEEDED` — if you never see it, iperf traffic isn't reaching the switch.
→ Confirm you're running the latest code (both controllers must have `owned_switches` logic).

**`Address already in use` when starting controller**
→ `pkill -f ryu-manager` then restart.

**`sch_htb: quantum of class 50001 is big` warnings**
→ Harmless kernel warning about HTB queue sizing. Does not affect functionality.

**After migration, some cross-domain pings fail**
→ After a switch migrates from B to A, the new controller's ARP/MAC table needs to repopulate. Run `pingall` once to trigger ARP learning on the new controller.
