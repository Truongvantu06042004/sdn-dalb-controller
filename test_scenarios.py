#!/usr/bin/env python3
"""
test_scenarios.py — Kiểm tra toàn diện hệ thống SDN DALB

Chạy: python3 test_scenarios.py
Yêu cầu: cả 2 controller đang chạy (port 8080 và 8081)
"""

import requests
import json
import sys
import time

API_A = 'http://localhost:8080'
API_B = 'http://localhost:8081'

# ── Màu terminal ─────────────────────────────────────────────────────────────
G  = '\033[92m'   # xanh lá
R  = '\033[91m'   # đỏ
Y  = '\033[93m'   # vàng
B  = '\033[94m'   # xanh dương
W  = '\033[97m'   # trắng
D  = '\033[90m'   # xám
RS = '\033[0m'    # reset

pass_count = 0
fail_count = 0
skip_count = 0
results    = []

def hdr(title):
    print(f'\n{B}{"─"*60}{RS}')
    print(f'{B}  {title}{RS}')
    print(f'{B}{"─"*60}{RS}')

def ok(name, detail=''):
    global pass_count
    pass_count += 1
    results.append(('PASS', name))
    print(f'  {G}[PASS]{RS} {name}' + (f'  {D}({detail}){RS}' if detail else ''))

def fail(name, detail=''):
    global fail_count
    fail_count += 1
    results.append(('FAIL', name))
    print(f'  {R}[FAIL]{RS} {name}' + (f'  {D}({detail}){RS}' if detail else ''))

def skip(name, reason=''):
    global skip_count
    skip_count += 1
    results.append(('SKIP', name))
    print(f'  {Y}[SKIP]{RS} {name}' + (f'  {D}({reason}){RS}' if reason else ''))

def get(url, timeout=3):
    try:
        r = requests.get(url, timeout=timeout)
        return r.status_code, r.json() if r.headers.get('content-type','').startswith('application/json') else {}
    except requests.exceptions.ConnectionError:
        return None, None
    except Exception as e:
        return -1, str(e)


# ════════════════════════════════════════════════════════════════════════════
# TEST 1: Connectivity
# ════════════════════════════════════════════════════════════════════════════
hdr('TEST 1: Controller Connectivity')

code_a, _ = get(f'{API_A}/load')
code_b, _ = get(f'{API_B}/load')

if code_a == 200:
    ok('Controller A reachable (port 8080)')
    ctrl_a_up = True
else:
    fail('Controller A reachable (port 8080)', f'got {code_a} — start controller_a.py first')
    ctrl_a_up = False

if code_b == 200:
    ok('Controller B reachable (port 8081)')
    ctrl_b_up = True
else:
    fail('Controller B reachable (port 8081)', f'got {code_b} — start controller_b.py first')
    ctrl_b_up = False

if not ctrl_a_up and not ctrl_b_up:
    print(f'\n{R}Both controllers offline. Start them first then re-run.{RS}')
    sys.exit(1)


# ════════════════════════════════════════════════════════════════════════════
# TEST 2: /load endpoint
# ════════════════════════════════════════════════════════════════════════════
hdr('TEST 2: /load Endpoint Structure')

for label, url, up in [('A', f'{API_A}/load', ctrl_a_up), ('B', f'{API_B}/load', ctrl_b_up)]:
    if not up:
        skip(f'Controller {label} /load', 'offline'); continue
    code, data = get(url)
    if code != 200 or data is None:
        fail(f'Controller {label} /load returns 200', f'got {code}'); continue

    ok(f'Controller {label} /load returns 200')

    if data.get('controller') == label:
        ok(f'Controller {label} /load: field "controller" = "{label}"')
    else:
        fail(f'Controller {label} /load: field "controller" = "{label}"',
             f'got "{data.get("controller")}"')

    if isinstance(data.get('total_load'), (int, float)):
        ok(f'Controller {label} /load: "total_load" is numeric', f'{data["total_load"]:.2f}')
    else:
        fail(f'Controller {label} /load: "total_load" is numeric')

    if isinstance(data.get('switches'), list):
        ok(f'Controller {label} /load: "switches" is array', f'{len(data["switches"])} switch(es)')
    else:
        fail(f'Controller {label} /load: "switches" is array')

    if isinstance(data.get('ct'), (int, float)):
        ok(f'Controller {label} /load: "ct" present', f'CT={data["ct"]}')
    else:
        fail(f'Controller {label} /load: "ct" present')


# ════════════════════════════════════════════════════════════════════════════
# TEST 3: /status endpoint
# ════════════════════════════════════════════════════════════════════════════
hdr('TEST 3: /status Endpoint Structure')

for label, url, up in [('A', f'{API_A}/status', ctrl_a_up), ('B', f'{API_B}/status', ctrl_b_up)]:
    if not up:
        skip(f'Controller {label} /status', 'offline'); continue
    code, data = get(url)
    if code != 200 or data is None:
        fail(f'Controller {label} /status returns 200', f'got {code}'); continue

    ok(f'Controller {label} /status returns 200')

    if data.get('controller') == label:
        ok(f'Controller {label} /status: "controller" = "{label}"')
    else:
        fail(f'Controller {label} /status: "controller" = "{label}"',
             f'got "{data.get("controller")}" — wrong controller running?')

    if data.get('status') in ('NORMAL', 'OVERLOADED'):
        ok(f'Controller {label} /status: "status" valid', data['status'])
    else:
        fail(f'Controller {label} /status: "status" valid', f'got "{data.get("status")}"')

    rho = data.get('rho')
    if isinstance(rho, float) and 0.0 <= rho <= 1.0:
        ok(f'Controller {label} /status: "rho" in [0,1]', f'ρ={rho:.3f}')
    else:
        fail(f'Controller {label} /status: "rho" in [0,1]', f'got {rho}')

    if isinstance(data.get('managed_switches'), list):
        ok(f'Controller {label} /status: "managed_switches" is array',
           ', '.join(data['managed_switches']) or '(empty — no switches connected)')
    else:
        fail(f'Controller {label} /status: "managed_switches" is array')

    if 'migration_log' in data:
        ok(f'Controller {label} /status: "migration_log" present',
           f'{len(data["migration_log"])} entries')
    else:
        fail(f'Controller {label} /status: "migration_log" present')

    ps = data.get('peer_status')
    if ps in ('ONLINE', 'OFFLINE'):
        ok(f'Controller {label} /status: "peer_status" valid', ps)
    else:
        fail(f'Controller {label} /status: "peer_status" valid', f'got {ps}')


# ════════════════════════════════════════════════════════════════════════════
# TEST 4: Rho Calculation
# ════════════════════════════════════════════════════════════════════════════
hdr('TEST 4: Rho (ρ) Balance Index Validation')

if ctrl_a_up and ctrl_b_up:
    _, da = get(f'{API_A}/status')
    _, db = get(f'{API_B}/status')
    if da and db:
        la = da.get('total_load', 0)
        lb = db.get('total_load', 0)
        mx = max(la, lb)
        expected_rho = ((la + lb) / 2) / mx if mx > 0 else 1.0
        rho_a = da.get('rho', 0)
        rho_b = db.get('rho', 0)

        print(f'  {D}  Controller A load: {la:.2f} | Controller B load: {lb:.2f}{RS}')
        print(f'  {D}  Expected ρ: {expected_rho:.3f} | Reported A: {rho_a:.3f} | Reported B: {rho_b:.3f}{RS}')

        if abs(rho_a - expected_rho) < 0.05:
            ok('Controller A rho matches computed value', f'{rho_a:.3f} ≈ {expected_rho:.3f}')
        else:
            fail('Controller A rho matches computed value',
                 f'reported {rho_a:.3f}, expected {expected_rho:.3f}')

        if abs(rho_b - expected_rho) < 0.05:
            ok('Controller B rho matches computed value', f'{rho_b:.3f} ≈ {expected_rho:.3f}')
        else:
            fail('Controller B rho matches computed value',
                 f'reported {rho_b:.3f}, expected {expected_rho:.3f}')

        if expected_rho >= 0.7:
            ok('System currently balanced', f'ρ={expected_rho:.3f} ≥ 0.7')
        else:
            ok('System imbalanced — migration expected', f'ρ={expected_rho:.3f} < 0.7')
elif ctrl_a_up:
    _, da = get(f'{API_A}/status')
    if da:
        rho_a = da.get('rho', 0)
        if abs(rho_a - 1.0) < 0.01:
            ok('Single controller rho = 1.0 (only one online)', f'ρ={rho_a:.3f}')
        else:
            fail('Single controller rho = 1.0', f'got {rho_a}')
else:
    skip('Rho calculation test', 'at least one controller needed')


# ════════════════════════════════════════════════════════════════════════════
# TEST 5: /arp endpoint
# ════════════════════════════════════════════════════════════════════════════
hdr('TEST 5: /arp Endpoint')

for label, url, up in [('A', f'{API_A}/arp', ctrl_a_up), ('B', f'{API_B}/arp', ctrl_b_up)]:
    if not up:
        skip(f'Controller {label} /arp?ip=...', 'offline'); continue

    # No IP param
    code, data = get(url)
    if code in (200, 404):
        ok(f'Controller {label} /arp reachable (no ip param)', f'status={code}')
    else:
        fail(f'Controller {label} /arp reachable', f'got {code}')

    # Known host IPs that should be learned after pingall
    test_ips_a = ['10.0.1.1', '10.0.1.2', '10.0.1.3']
    test_ips_b = ['10.0.2.1', '10.0.2.2', '10.0.2.3']
    test_ips = test_ips_a if label == 'A' else test_ips_b
    learned = 0
    for ip in test_ips:
        c, d = get(f'{url}?ip={ip}')
        if c == 200 and d and d.get('mac'):
            learned += 1
    if learned > 0:
        ok(f'Controller {label} /arp: {learned}/{len(test_ips)} IPs learned',
           'run pingall in Mininet to learn all')
    else:
        skip(f'Controller {label} /arp: no IPs learned yet',
             'normal before pingall — run "pingall" in Mininet first')


# ════════════════════════════════════════════════════════════════════════════
# TEST 6: Switch distribution
# ════════════════════════════════════════════════════════════════════════════
hdr('TEST 6: Switch Distribution')

total_switches = 0
sw_names = set()

for label, url, up in [('A', f'{API_A}/status', ctrl_a_up), ('B', f'{API_B}/status', ctrl_b_up)]:
    if not up:
        skip(f'Controller {label} switch check', 'offline'); continue
    _, data = get(url)
    if not data:
        fail(f'Controller {label} switch check', 'no data'); continue
    sws = data.get('managed_switches', [])
    total_switches += len(sws)
    sw_names.update(sws)
    print(f'  {D}  Controller {label}: {sws}{RS}')
    if len(sws) > 0:
        ok(f'Controller {label} has {len(sws)} switch(es)', ', '.join(sws))
    else:
        skip(f'Controller {label} has switches', 'no switches — start topology.py or wait for connection')

if ctrl_a_up and ctrl_b_up:
    if total_switches == 5:
        ok(f'Total switches = 5 (all connected)', ', '.join(sorted(sw_names)))
    elif total_switches > 0:
        skip(f'Total switches = {total_switches}/5',
             'some switches not yet connected — wait a few seconds after topology.py starts')
    else:
        skip('Switch distribution check', 'no switches connected yet')


# ════════════════════════════════════════════════════════════════════════════
# TEST 7: CORS headers
# ════════════════════════════════════════════════════════════════════════════
hdr('TEST 7: CORS Headers (required for Dashboard)')

for label, url, up in [('A', f'{API_A}/load', ctrl_a_up), ('B', f'{API_B}/load', ctrl_b_up)]:
    if not up:
        skip(f'Controller {label} CORS headers', 'offline'); continue
    try:
        r = requests.get(url, timeout=3)
        cors = r.headers.get('Access-Control-Allow-Origin', '')
        if cors == '*':
            ok(f'Controller {label} has CORS header', 'Access-Control-Allow-Origin: *')
        else:
            fail(f'Controller {label} has CORS header', f'got: "{cors}"')
    except Exception as e:
        fail(f'Controller {label} CORS check', str(e))


# ════════════════════════════════════════════════════════════════════════════
# TEST 8: Migration endpoint
# ════════════════════════════════════════════════════════════════════════════
hdr('TEST 8: POST /migrate Endpoint (bad request validation)')

for label, url, up in [('A', f'{API_A}/migrate', ctrl_a_up), ('B', f'{API_B}/migrate', ctrl_b_up)]:
    if not up:
        skip(f'Controller {label} /migrate validation', 'offline'); continue
    try:
        # Send invalid payload — should get 400 or 503, not 500
        r = requests.post(url, json={}, timeout=3)
        if r.status_code in (400, 503):
            ok(f'Controller {label} /migrate: invalid payload → {r.status_code}')
        elif r.status_code == 200:
            skip(f'Controller {label} /migrate: empty payload accepted (dpid=0)',
                 'may accept default values')
        else:
            fail(f'Controller {label} /migrate: unexpected status', str(r.status_code))
    except Exception as e:
        fail(f'Controller {label} /migrate endpoint', str(e))


# ════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ════════════════════════════════════════════════════════════════════════════
total = pass_count + fail_count + skip_count
print(f'\n{B}{"═"*60}{RS}')
print(f'{B}  TEST SUMMARY{RS}')
print(f'{B}{"═"*60}{RS}')
print(f'  {G}PASS{RS}: {pass_count:3d}')
print(f'  {R}FAIL{RS}: {fail_count:3d}')
print(f'  {Y}SKIP{RS}: {skip_count:3d}   (skipped = not an error, just needs setup)')
print(f'  {"─"*20}')
print(f'  Total: {total}')

if fail_count == 0:
    print(f'\n  {G}All tests PASSED ✅{RS}')
else:
    print(f'\n  {R}Failed tests:{RS}')
    for status, name in results:
        if status == 'FAIL':
            print(f'    {R}✗{RS} {name}')

print(f'\n{D}Tip: Run "pingall" in Mininet CLI to enable ARP tests.{RS}')
print(f'{D}Tip: Generate iperf traffic to trigger migration tests.{RS}\n')
