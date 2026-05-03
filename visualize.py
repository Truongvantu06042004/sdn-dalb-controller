#!/usr/bin/env python3
"""
visualize.py — SDN DALB Scientific Visualization
6-panel real-time charts (updates every 3 s):
  1. Average Packet-in Arrival Rate over Time  (like Fig.6 in paper)
  2. Load Balance Index ρ over Time
  3. Switch-to-Controller RTT over Time        (latency evaluation)
  4. Distributed Controllers' Throughput       (like Fig.5 in paper)
  5. Current C_Load per Switch (bar)
  6. System Status + Migration Log
"""

import time
import threading
import requests
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
import matplotlib.lines as mlines
import numpy as np
from matplotlib.animation import FuncAnimation

# ── Config ────────────────────────────────────────────────────────────────────
API_A    = 'http://localhost:8080'
API_B    = 'http://localhost:8081'
INTERVAL = 3       # seconds per update
MAX_HIST = 80      # ~4 minutes history

# ── Colors ────────────────────────────────────────────────────────────────────
BG       = '#12121f'
PANEL    = '#1a1a2e'
BORDER   = '#2a2a4a'
TEXT     = '#e8e8f0'
GREY     = '#6a6a8a'
CA       = '#4fc3f7'   # blue  — Controller A
CB       = '#ef5350'   # red   — Controller B  (matches Fig.6)
CT_CLR   = '#ffa726'   # orange — CT threshold
RHO_OK   = '#66bb6a'   # green — balanced
RHO_BAD  = '#ef5350'   # red   — imbalanced
MIG_CLR  = '#ce93d8'   # purple — migration event

# ── Shared state ──────────────────────────────────────────────────────────────
lock  = threading.Lock()
state = {'a': None, 'b': None, 'la': None, 'lb': None}
history = {
    'elapsed': [],
    'load_a':  [],
    'load_b':  [],
    'rho':     [],
    'migrate': [],
    'rtt_a':   [],   # avg switch-to-controller RTT for A (ms)
    'rtt_b':   [],   # avg switch-to-controller RTT for B (ms)
    'tput_a':  [],   # total data throughput via A's switches (Mbps)
    'tput_b':  [],   # total data throughput via B's switches (Mbps)
}
_t0       = time.time()
_prev_mc  = [0, 0]   # [mc_a, mc_b]


def fetch_loop():
    while True:
        sa = sb = la = lb = None
        try: sa = requests.get(f'{API_A}/status', timeout=2).json()
        except: pass
        try: la = requests.get(f'{API_A}/load',   timeout=2).json()
        except: pass
        try: sb = requests.get(f'{API_B}/status', timeout=2).json()
        except: pass
        try: lb = requests.get(f'{API_B}/load',   timeout=2).json()
        except: pass

        elapsed = round(time.time() - _t0)
        load_a  = sa.get('total_load', 0.0) if sa else 0.0
        load_b  = sb.get('total_load', 0.0) if sb else 0.0
        mx      = max(load_a, load_b)
        rho     = ((load_a + load_b) / 2) / mx if mx > 0 else 1.0

        mc_a = sa.get('migration_count', 0) if sa else 0
        mc_b = sb.get('migration_count', 0) if sb else 0
        migrated = (mc_a > _prev_mc[0]) or (mc_b > _prev_mc[1])
        _prev_mc[0], _prev_mc[1] = mc_a, mc_b

        # RTT averages (avg switch-to-controller RTT per domain)
        sws_a = la.get('switches', []) if la else []
        sws_b = lb.get('switches', []) if lb else []
        rtt_a = (sum(sw.get('rtt_ms', 0) for sw in sws_a) / len(sws_a)) if sws_a else 0.0
        rtt_b = (sum(sw.get('rtt_ms', 0) for sw in sws_b) / len(sws_b)) if sws_b else 0.0

        # Total throughput per controller (Mbps)
        tput_a = sum(sw.get('throughput_mbps', 0) for sw in sws_a)
        tput_b = sum(sw.get('throughput_mbps', 0) for sw in sws_b)

        with lock:
            state.update({'a': sa, 'b': sb, 'la': la, 'lb': lb})
            history['elapsed'].append(elapsed)
            history['load_a'].append(load_a)
            history['load_b'].append(load_b)
            history['rho'].append(rho)
            history['migrate'].append(migrated)
            history['rtt_a'].append(rtt_a)
            history['rtt_b'].append(rtt_b)
            history['tput_a'].append(tput_a)
            history['tput_b'].append(tput_b)
            if len(history['elapsed']) > MAX_HIST:
                for k in history: history[k].pop(0)

        time.sleep(INTERVAL)


threading.Thread(target=fetch_loop, daemon=True).start()

# ── Figure ────────────────────────────────────────────────────────────────────
plt.rcParams.update({
    'font.family'     : 'DejaVu Sans',
    'font.size'       : 9,
    'axes.titlesize'  : 10,
    'axes.titleweight': 'bold',
    'axes.labelsize'  : 9,
    'axes.spines.top' : False,
    'axes.spines.right': False,
    'xtick.direction' : 'out',
    'ytick.direction' : 'out',
    'legend.framealpha': 0.6,
    'legend.fontsize' : 8,
})

fig = plt.figure(figsize=(14, 11), facecolor=BG)
fig.canvas.manager.set_window_title('SDN DALB — Scientific Visualization')

gs = gridspec.GridSpec(3, 2, figure=fig,
                       hspace=0.52, wspace=0.36,
                       left=0.07, right=0.97, top=0.93, bottom=0.07)

ax_load = fig.add_subplot(gs[0, 0])   # Packet-in arrival rate (like Fig.6)
ax_rho  = fig.add_subplot(gs[0, 1])   # ρ over time
ax_rtt  = fig.add_subplot(gs[1, 0])   # Switch-to-controller RTT (latency)
ax_tput = fig.add_subplot(gs[1, 1])   # Distributed Controllers' Throughput (like Fig.5)
ax_sw   = fig.add_subplot(gs[2, 0])   # Per-switch C_Load bars
ax_info = fig.add_subplot(gs[2, 1])   # System status

for ax in (ax_load, ax_rho, ax_rtt, ax_tput, ax_sw, ax_info):
    ax.set_facecolor(PANEL)
    for sp in ax.spines.values():
        sp.set_edgecolor(BORDER)


def _draw_migrate_lines(ax, xs, migrates, ymax):
    """Draw purple dotted vertical lines at migration events."""
    first = True
    for x, m in zip(xs, migrates):
        if m:
            kw = dict(color=MIG_CLR, lw=1.5, ls=':', alpha=0.9,
                      label='Migration' if first else '_nolegend_')
            ax.axvline(x, **kw)
            ax.text(x + 0.5, ymax * 0.92, '↑', color=MIG_CLR, fontsize=9, va='top')
            first = False


def update(_):
    with lock:
        sa   = state['a']
        sb   = state['b']
        la   = state['la']
        lb   = state['lb']
        hist = {k: list(v) for k, v in history.items()}

    ct = (sa.get('ct', 1000) if sa else
          sb.get('ct', 1000) if sb else 1000)

    # ════════════════════════════════════════════════════════════════════════
    # Panel 1 — Average Packet-in Arrival Rate over Time  (like Fig.6 in paper)
    # ════════════════════════════════════════════════════════════════════════
    ax_load.cla()
    ax_load.set_facecolor(PANEL)
    ax_load.set_title('Average Packet-in Arrival Rate over Time', pad=8, color=TEXT)
    ax_load.set_xlabel('Time (s)', color=TEXT)
    ax_load.set_ylabel('Arrival Rate (requests/s)', color=TEXT)
    ax_load.tick_params(colors=GREY)
    ax_load.grid(True, alpha=0.2, linestyle='--', color=BORDER)
    ax_load.set_axisbelow(True)

    if len(hist['elapsed']) >= 2:
        xs  = hist['elapsed']
        la_ = hist['load_a']
        lb_ = hist['load_b']
        ax_load.plot(xs, la_, color=CA, lw=2.2, label='Controller A',
                     marker='o', markersize=3, markevery=5)
        ax_load.plot(xs, lb_, color=CB, lw=2.2, label='Controller B',
                     marker='s', markersize=3, markevery=5)
        ax_load.fill_between(xs, la_, alpha=0.12, color=CA)
        ax_load.fill_between(xs, lb_, alpha=0.12, color=CB)
        ax_load.axhline(ct, color=CT_CLR, lw=1.5, ls='--', alpha=0.85, label=f'CT = {ct:.0f}')
        top = max(max(la_) if la_ else 0, max(lb_) if lb_ else 0, ct) * 1.28
        top = max(top, 200)
        _draw_migrate_lines(ax_load, xs, hist['migrate'], top)
        ax_load.set_ylim(0, top)
        ax_load.set_xlim(xs[0], xs[-1] + 1)
        ax_load.legend(loc='upper left')
        if la_:
            ax_load.annotate(f' {la_[-1]:.0f}', xy=(xs[-1], la_[-1]),
                             fontsize=8.5, color=CA, fontweight='bold', va='center')
        if lb_:
            ax_load.annotate(f' {lb_[-1]:.0f}', xy=(xs[-1], lb_[-1]),
                             fontsize=8.5, color=CB, fontweight='bold', va='center')
    else:
        ax_load.text(0.5, 0.5, 'Collecting data…\nStart controllers + topology first',
                     ha='center', va='center', transform=ax_load.transAxes,
                     color=GREY, fontsize=11)

    # ════════════════════════════════════════════════════════════════════════
    # Panel 2 — Load Balance Index ρ over Time
    # ════════════════════════════════════════════════════════════════════════
    ax_rho.cla()
    ax_rho.set_facecolor(PANEL)
    ax_rho.set_title('Load Balance Index ρ over Time', pad=8, color=TEXT)
    ax_rho.set_xlabel('Time (s)', color=TEXT)
    ax_rho.set_ylabel('ρ', color=TEXT)
    ax_rho.tick_params(colors=GREY)
    ax_rho.set_ylim(0, 1.1)
    ax_rho.grid(True, alpha=0.2, linestyle='--', color=BORDER)
    ax_rho.set_axisbelow(True)
    ax_rho.axhline(0.7, color=RHO_BAD, lw=1.5, ls='--', alpha=0.8, label='ρ = 0.7 threshold')

    if len(hist['elapsed']) >= 2:
        xs   = np.array(hist['elapsed'], dtype=float)
        rhos = np.array(hist['rho'],     dtype=float)
        ax_rho.plot(xs, rhos, color=TEXT, lw=1.8, zorder=3)
        ax_rho.fill_between(xs, rhos, 0.7, where=(rhos >= 0.7), color=RHO_OK,  alpha=0.25,
                            label='Balanced (ρ ≥ 0.7)')
        ax_rho.fill_between(xs, rhos, 0.7, where=(rhos < 0.7),  color=RHO_BAD, alpha=0.3,
                            label='Imbalanced')
        _draw_migrate_lines(ax_rho, hist['elapsed'], hist['migrate'], 1.05)
        ax_rho.set_xlim(xs[0], xs[-1] + 1)
        rho_now = rhos[-1]
        rc = RHO_OK if rho_now >= 0.7 else RHO_BAD
        ax_rho.text(0.97, 0.97, f'ρ = {rho_now:.3f}',
                    transform=ax_rho.transAxes, ha='right', va='top',
                    fontsize=13, fontweight='bold', color=rc)
        ax_rho.legend(loc='lower right', fontsize=7.5)
    else:
        ax_rho.legend(loc='lower right', fontsize=7.5)
        ax_rho.text(0.5, 0.5, 'Waiting…', ha='center', va='center',
                    transform=ax_rho.transAxes, color=GREY)

    # ════════════════════════════════════════════════════════════════════════
    # Panel 3 — Switch-to-Controller RTT over Time  (Latency evaluation)
    # ════════════════════════════════════════════════════════════════════════
    ax_rtt.cla()
    ax_rtt.set_facecolor(PANEL)
    ax_rtt.set_title('Switch-to-Controller RTT over Time  (Control-Plane Latency)',
                     pad=8, color=TEXT)
    ax_rtt.set_xlabel('Time (s)', color=TEXT)
    ax_rtt.set_ylabel('RTT (ms)', color=TEXT)
    ax_rtt.tick_params(colors=GREY)
    ax_rtt.grid(True, alpha=0.2, linestyle='--', color=BORDER)
    ax_rtt.set_axisbelow(True)

    if len(hist['elapsed']) >= 2:
        xs    = hist['elapsed']
        rtt_a = hist['rtt_a']
        rtt_b = hist['rtt_b']
        ax_rtt.plot(xs, rtt_a, color=CA, lw=2.0, label='Avg RTT — Controller A',
                    marker='o', markersize=3, markevery=5)
        ax_rtt.plot(xs, rtt_b, color=CB, lw=2.0, label='Avg RTT — Controller B',
                    marker='s', markersize=3, markevery=5)
        ax_rtt.fill_between(xs, rtt_a, alpha=0.12, color=CA)
        ax_rtt.fill_between(xs, rtt_b, alpha=0.12, color=CB)
        top = max(max(rtt_a) if rtt_a else 0, max(rtt_b) if rtt_b else 0) * 1.4
        top = max(top, 10)
        _draw_migrate_lines(ax_rtt, xs, hist['migrate'], top)
        ax_rtt.set_ylim(0, top)
        ax_rtt.set_xlim(xs[0], xs[-1] + 1)
        ax_rtt.legend(loc='upper left')
        if rtt_a:
            ax_rtt.annotate(f' {rtt_a[-1]:.1f}ms', xy=(xs[-1], rtt_a[-1]),
                            fontsize=8, color=CA, fontweight='bold', va='center')
        if rtt_b:
            ax_rtt.annotate(f' {rtt_b[-1]:.1f}ms', xy=(xs[-1], rtt_b[-1]),
                            fontsize=8, color=CB, fontweight='bold', va='center')
    else:
        ax_rtt.text(0.5, 0.5, 'Waiting for RTT data…', ha='center', va='center',
                    transform=ax_rtt.transAxes, color=GREY, fontsize=10)

    # ════════════════════════════════════════════════════════════════════════
    # Panel 4 — Distributed Controllers' Throughput  (like Fig.5 in paper)
    # ════════════════════════════════════════════════════════════════════════
    ax_tput.cla()
    ax_tput.set_facecolor(PANEL)
    ax_tput.set_title("Distributed Controllers' Throughput over Time",
                      pad=8, color=TEXT)
    ax_tput.set_xlabel('Time (s)', color=TEXT)
    ax_tput.set_ylabel('Throughput (Mbps)', color=TEXT)
    ax_tput.tick_params(colors=GREY)
    ax_tput.grid(True, alpha=0.2, linestyle='--', color=BORDER)
    ax_tput.set_axisbelow(True)

    if len(hist['elapsed']) >= 2:
        xs     = hist['elapsed']
        tput_a = hist['tput_a']
        tput_b = hist['tput_b']
        ax_tput.plot(xs, tput_a, color=CA, lw=2.0, label='Controller A',
                     marker='o', markersize=3, markevery=5)
        ax_tput.plot(xs, tput_b, color=CB, lw=2.0, label='Controller B',
                     marker='s', markersize=3, markevery=5)
        ax_tput.fill_between(xs, tput_a, alpha=0.12, color=CA)
        ax_tput.fill_between(xs, tput_b, alpha=0.12, color=CB)
        top = max(max(tput_a) if tput_a else 0, max(tput_b) if tput_b else 0) * 1.3
        top = max(top, 1.0)
        _draw_migrate_lines(ax_tput, xs, hist['migrate'], top)
        ax_tput.set_ylim(0, top)
        ax_tput.set_xlim(xs[0], xs[-1] + 1)
        ax_tput.legend(loc='upper left')
        if tput_a:
            ax_tput.annotate(f' {tput_a[-1]:.2f}', xy=(xs[-1], tput_a[-1]),
                             fontsize=8, color=CA, fontweight='bold', va='center')
        if tput_b:
            ax_tput.annotate(f' {tput_b[-1]:.2f}', xy=(xs[-1], tput_b[-1]),
                             fontsize=8, color=CB, fontweight='bold', va='center')
    else:
        ax_tput.text(0.5, 0.5, 'Waiting for throughput data…', ha='center', va='center',
                     transform=ax_tput.transAxes, color=GREY, fontsize=10)

    # ════════════════════════════════════════════════════════════════════════
    # Panel 5 — Current C_Load per Switch
    # ════════════════════════════════════════════════════════════════════════
    ax_sw.cla()
    ax_sw.set_facecolor(PANEL)
    ax_sw.set_title(
        'Current Load per Switch   (C_Load = w₁·N + w₂·F + w₃·R,  '
        'w₁=0.1  w₂=0.8  w₃=0.1)', pad=8, color=TEXT)
    ax_sw.set_ylabel('C_Load', color=TEXT)
    ax_sw.tick_params(colors=GREY)
    ax_sw.grid(True, axis='y', alpha=0.2, linestyle='--', color=BORDER)
    ax_sw.set_axisbelow(True)

    sw_list = []
    for src, ld, ctrl in [(sa, la, 'A'), (sb, lb, 'B')]:
        if ld and 'switches' in ld:
            for sw in ld['switches']:
                sw_list.append({**sw, 'ctrl': ctrl,
                                'ct': src.get('ct', 1000) if src else 1000})

    if sw_list:
        names  = [s['name']        for s in sw_list]
        loads  = [s.get('load', 0) for s in sw_list]
        flows  = [s.get('flows', 0) for s in sw_list]
        colors = [CA if s['ctrl'] == 'A' else CB for s in sw_list]
        bars   = ax_sw.bar(names, loads, color=colors, width=0.5,
                           edgecolor='white', linewidth=0.4, alpha=0.88)
        for bar, load, flow in zip(bars, loads, flows):
            x = bar.get_x() + bar.get_width() / 2
            ax_sw.text(x, bar.get_height() + 2, f'{load:.0f}',
                       ha='center', va='bottom', fontsize=9.5, color=TEXT, fontweight='bold')
            if bar.get_height() > 30:
                ax_sw.text(x, bar.get_height() / 2, f'{flow}F',
                           ha='center', va='center', fontsize=7.5, color='white', alpha=0.9)
        ax_sw.axhline(ct, color=CT_CLR, lw=1.5, ls='--', alpha=0.85, label=f'CT = {ct:.0f}')
        top_sw = max(max(loads) if loads else 0, ct) * 1.3 + 20
        ax_sw.set_ylim(0, top_sw)
        legend_els = [
            mpatches.Patch(facecolor=CA, label='Domain A (Controller A)'),
            mpatches.Patch(facecolor=CB, label='Domain B (Controller B)'),
            mlines.Line2D([], [], color=CT_CLR, ls='--', lw=1.5, label=f'CT = {ct:.0f}'),
        ]
        ax_sw.legend(handles=legend_els, loc='upper right', fontsize=8)
    else:
        ax_sw.text(0.5, 0.5,
                   'No switches connected\nStart topology.py and wait a few seconds',
                   ha='center', va='center',
                   transform=ax_sw.transAxes, color=GREY, fontsize=10)

    # ════════════════════════════════════════════════════════════════════════
    # Panel 6 — System Status + Migration Log
    # ════════════════════════════════════════════════════════════════════════
    ax_info.cla()
    ax_info.set_facecolor(PANEL)
    ax_info.set_title('System Status', pad=8, color=TEXT)
    ax_info.axis('off')

    y = 0.97
    def _t(s, color=TEXT, size=9, bold=False, dy=0.068):
        nonlocal y
        ax_info.text(0.05, y, s, transform=ax_info.transAxes,
                     fontsize=size, color=color, va='top',
                     fontweight='bold' if bold else 'normal',
                     family='monospace')
        y -= dy

    _t(f'Updated: {time.strftime("%H:%M:%S")}', GREY, 8, dy=0.06)
    _t('', dy=0.04)

    for label, src, col in [('Controller A', sa, CA), ('Controller B', sb, CB)]:
        if src:
            st   = src.get('status', 'NORMAL')
            load = src.get('total_load', 0)
            ct_v = src.get('ct', 1000)
            mc   = src.get('migration_count', 0)
            up   = src.get('uptime_seconds', 0)
            sws  = ', '.join(src.get('managed_switches') or ['—'])
            sc   = RHO_OK if st == 'NORMAL' else RHO_BAD
            _t(f'● {label}  [{st}]', sc, 9, True)
            _t(f'  Load : {load:7.1f} / CT {ct_v:.0f}', TEXT, 8.5)
            _t(f'  SW   : {sws}',  TEXT, 8)
            _t(f'  Mig  : {mc}   Up: {up//60}m{up%60:02d}s', GREY, 8)
            mlog = src.get('migration_log') or []
            if mlog:
                last = mlog[-1]
                _t(f'  ↳ {last["switch"]} → peer  @ {last["time"]}',
                   MIG_CLR, 8)
        else:
            port = 8080 if label.endswith('A') else 8081
            _t(f'● {label}  [OFFLINE]', RHO_BAD, 9, True)
            _t(f'  Port {port} not responding', GREY, 8)
        _t('', dy=0.04)

    rho_now = hist['rho'][-1] if hist['rho'] else 1.0
    rc      = RHO_OK if rho_now >= 0.7 else RHO_BAD
    _t(f'ρ = {rho_now:.3f}  '
       f'{"✓ Balanced" if rho_now >= 0.7 else "✗ Imbalanced"}',
       rc, 10, True)

    mc_total = ((sa.get('migration_count', 0) if sa else 0) +
                (sb.get('migration_count', 0) if sb else 0))
    _t(f'Total migrations: {mc_total}', TEXT, 9)

    # ── Supertitle ────────────────────────────────────────────────────────────
    fig.suptitle(
        'SDN DALB — Dynamic Adaptive Load Balancing   |   Refresh: 3 s',
        color=TEXT, fontsize=11, fontweight='bold', y=0.97)


# ── Run ───────────────────────────────────────────────────────────────────────
print('SDN DALB Visualization')
print(f'  Controller A: {API_A}')
print(f'  Controller B: {API_B}')
print('Close window to exit.\n')

time.sleep(1.2)   # wait for first fetch

ani = FuncAnimation(fig, update, interval=INTERVAL * 1000,
                    cache_frame_data=False)
plt.show()
