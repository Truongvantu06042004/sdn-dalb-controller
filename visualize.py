#!/usr/bin/env python3
"""
visualize.py — Biểu đồ trực quan thời gian thực cho hệ thống SDN DALB

Hiển thị 6 biểu đồ tự động cập nhật mỗi 3 giây:
  1. So sánh tổng tải 2 controller (bar chart)
  2. Tải từng switch trên cả 2 controller (bar chart)
  3. Chỉ số ρ (rho) theo thời gian (line chart)
  4. Phân bổ switch theo controller (pie chart)
  5. Tải từng switch dạng ngang (horizontal bar)
  6. Bản đồ trạng thái hệ thống (text overlay)

Chạy: python3 visualize.py
Yêu cầu: pip install matplotlib numpy requests
"""

import time
import threading
import requests
import matplotlib
matplotlib.use('TkAgg')          # thử TkAgg, nếu lỗi đổi thành 'Agg'
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec
import numpy as np

# ── Config ────────────────────────────────────────────────────────────────────
API_A      = 'http://localhost:8080'
API_B      = 'http://localhost:8081'
INTERVAL   = 3          # giây giữa mỗi lần cập nhật
MAX_HIST   = 60         # số điểm lịch sử tối đa

# ── Màu ──────────────────────────────────────────────────────────────────────
BG      = '#0d1117'
PANEL   = '#161b22'
BORDER  = '#30363d'
TEXT    = '#c9d1d9'
GREY    = '#8b949e'
BLUE    = '#58a6ff'
GREEN   = '#3fb950'
RED     = '#f85149'
ORANGE  = '#f0883e'
YELLOW  = '#e3b341'

# ── Shared state (thread-safe via lock) ───────────────────────────────────────
lock     = threading.Lock()
state    = {
    'a': None,   # /status response từ Controller A
    'b': None,   # /status response từ Controller B
    'la': None,  # /load  response từ Controller A
    'lb': None,  # /load  response từ Controller B
}
# Lịch sử ρ và loads theo thời gian
history = {
    'time':  [],
    'rho':   [],
    'load_a': [],
    'load_b': [],
}


# ── Fetch thread ──────────────────────────────────────────────────────────────
def fetch_loop():
    while True:
        sa = lb = la = sb = None
        try: sa = requests.get(f'{API_A}/status', timeout=2).json()
        except: pass
        try: la = requests.get(f'{API_A}/load',   timeout=2).json()
        except: pass
        try: sb = requests.get(f'{API_B}/status', timeout=2).json()
        except: pass
        try: lb = requests.get(f'{API_B}/load',   timeout=2).json()
        except: pass

        now   = time.strftime('%H:%M:%S')
        load_a = sa['total_load'] if sa else 0.0
        load_b = sb['total_load'] if sb else 0.0
        mx     = max(load_a, load_b)
        rho    = ((load_a + load_b) / 2) / mx if mx > 0 else 1.0

        with lock:
            state['a']  = sa
            state['b']  = sb
            state['la'] = la
            state['lb'] = lb
            history['time'].append(now)
            history['rho'].append(rho)
            history['load_a'].append(load_a)
            history['load_b'].append(load_b)
            if len(history['time']) > MAX_HIST:
                for k in history: history[k].pop(0)

        time.sleep(INTERVAL)

fetch_thread = threading.Thread(target=fetch_loop, daemon=True)
fetch_thread.start()


# ── Figure setup ──────────────────────────────────────────────────────────────
plt.rcParams.update({
    'figure.facecolor'  : BG,
    'axes.facecolor'    : PANEL,
    'axes.edgecolor'    : BORDER,
    'axes.labelcolor'   : TEXT,
    'xtick.color'       : GREY,
    'ytick.color'       : GREY,
    'text.color'        : TEXT,
    'grid.color'        : BORDER,
    'grid.linestyle'    : '--',
    'grid.linewidth'    : 0.5,
    'font.size'         : 9,
    'axes.titlesize'    : 10,
    'axes.titlecolor'   : TEXT,
    'legend.facecolor'  : PANEL,
    'legend.edgecolor'  : BORDER,
    'legend.fontsize'   : 8,
})

fig = plt.figure(figsize=(16, 9), facecolor=BG)
fig.canvas.manager.set_window_title('SDN DALB — Real-time Visualization')

gs = gridspec.GridSpec(3, 3, figure=fig,
                       hspace=0.55, wspace=0.38,
                       left=0.06, right=0.97, top=0.93, bottom=0.07)

ax_bar    = fig.add_subplot(gs[0, 0])    # Controller total load (bar)
ax_sw     = fig.add_subplot(gs[0, 1])    # Per-switch load (bar)
ax_rho    = fig.add_subplot(gs[0, 2])    # Rho gauge (bar)
ax_hist   = fig.add_subplot(gs[1, :])    # Load history (line, wide)
ax_pie    = fig.add_subplot(gs[2, 0])    # Switch distribution (pie)
ax_hbar   = fig.add_subplot(gs[2, 1])    # Per-switch horizontal bar
ax_info   = fig.add_subplot(gs[2, 2])    # System status text


def bar_color(val, ct=1000):
    p = val / ct if ct else 0
    if p >= 0.8: return RED
    if p >= 0.5: return ORANGE
    return GREEN


def update(_):
    with lock:
        sa   = state['a']
        sb   = state['b']
        la   = state['la']
        lb   = state['lb']
        hist = {k: list(v) for k, v in history.items()}

    # ── 1. Controller total load bar chart ────────────────────────────────────
    ax_bar.cla()
    ax_bar.set_facecolor(PANEL)
    ax_bar.set_title('Controller Total Load', pad=6)
    ax_bar.set_ylabel('C_Load')
    ax_bar.yaxis.grid(True)
    ax_bar.set_axisbelow(True)

    load_a = sa['total_load'] if sa else 0
    load_b = sb['total_load'] if sb else 0
    ct_a   = sa['ct'] if sa else 1000
    ct_b   = sb['ct'] if sb else 1000

    bars = ax_bar.bar(['Ctrl A', 'Ctrl B'],
                      [load_a, load_b],
                      color=[bar_color(load_a, ct_a), bar_color(load_b, ct_b)],
                      width=0.5, edgecolor=BORDER, linewidth=0.8)
    for bar, val in zip(bars, [load_a, load_b]):
        ax_bar.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 5,
                    f'{val:.0f}', ha='center', va='bottom', fontsize=9, color=TEXT)

    # CT threshold lines
    if sa: ax_bar.axhline(ct_a, color=YELLOW, lw=1, ls='--', alpha=0.7, label=f'CT={ct_a:.0f}')
    if sa: ax_bar.legend(loc='upper right', fontsize=7)
    ax_bar.set_ylim(0, max(load_a, load_b, ct_a, ct_b) * 1.25 + 50)
    ax_bar.tick_params(axis='x', labelsize=9)

    # Offline text
    if not sa: ax_bar.text(0, max(load_b, 100)/2, 'OFFLINE', ha='center', color=RED, fontsize=11, fontweight='bold')
    if not sb: ax_bar.text(1, max(load_a, 100)/2, 'OFFLINE', ha='center', color=RED, fontsize=11, fontweight='bold')

    # ── 2. Per-switch load bar chart ──────────────────────────────────────────
    ax_sw.cla()
    ax_sw.set_facecolor(PANEL)
    ax_sw.set_title('Per-Switch Load (C_Load)', pad=6)
    ax_sw.set_ylabel('C_Load')
    ax_sw.yaxis.grid(True)
    ax_sw.set_axisbelow(True)

    sw_names  = []
    sw_loads  = []
    sw_colors = []
    ct_ref    = max(ct_a, ct_b)

    for src, ld in [(sa, la), (sb, lb)]:
        if ld and 'switches' in ld:
            for sw in ld['switches']:
                sw_names.append(sw['name'])
                sw_loads.append(sw.get('load', 0))
                sw_colors.append(BLUE if (src and src.get('controller') == 'A') else GREEN)

    if sw_names:
        sw_bars = ax_sw.bar(sw_names, sw_loads, color=sw_colors,
                            width=0.6, edgecolor=BORDER, linewidth=0.7)
        for bar, val in zip(sw_bars, sw_loads):
            ax_sw.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 2,
                       f'{val:.0f}', ha='center', va='bottom', fontsize=8, color=TEXT)
        ax_sw.set_ylim(0, max(sw_loads) * 1.3 + 10)
    else:
        ax_sw.text(0.5, 0.5, 'No switches\nconnected', ha='center', va='center',
                   transform=ax_sw.transAxes, color=GREY, fontsize=10)

    legend_patches = [
        mpatches.Patch(color=BLUE, label='Domain A'),
        mpatches.Patch(color=GREEN, label='Domain B'),
    ]
    ax_sw.legend(handles=legend_patches, loc='upper right', fontsize=7)
    ax_sw.tick_params(axis='x', labelsize=8)

    # ── 3. Rho gauge ──────────────────────────────────────────────────────────
    ax_rho.cla()
    ax_rho.set_facecolor(PANEL)
    ax_rho.set_title('Balance Index ρ (rho)', pad=6)
    ax_rho.set_xlim(0, 1)
    ax_rho.set_ylim(0, 1)
    ax_rho.axis('off')

    rho = hist['rho'][-1] if hist['rho'] else 1.0
    rho_color = GREEN if rho >= 0.7 else RED

    # Background bar
    ax_rho.add_patch(mpatches.FancyBboxPatch((0.05, 0.35), 0.9, 0.12,
        boxstyle='round,pad=0.01', facecolor=BORDER, edgecolor='none'))
    # Fill bar
    ax_rho.add_patch(mpatches.FancyBboxPatch((0.05, 0.35), 0.9 * rho, 0.12,
        boxstyle='round,pad=0.01', facecolor=rho_color, edgecolor='none', alpha=0.9))
    # Threshold line at 0.7
    ax_rho.axvline(0.05 + 0.9 * 0.7, ymin=0.3, ymax=0.62, color=RED, lw=2, ls='--', alpha=0.8)

    ax_rho.text(0.5, 0.72, f'ρ = {rho:.3f}', ha='center', va='center',
                fontsize=20, fontweight='bold', color=rho_color, transform=ax_rho.transAxes)
    ax_rho.text(0.5, 0.58, ('BALANCED ✓' if rho >= 0.7 else 'IMBALANCED — migration may trigger'),
                ha='center', va='center', fontsize=9,
                color=GREEN if rho >= 0.7 else ORANGE, transform=ax_rho.transAxes)
    ax_rho.text(0.05 + 0.9 * 0.7, 0.23, '0.7\nthreshold',
                ha='center', va='center', fontsize=7, color=RED)
    ax_rho.text(0.05, 0.23, '0.0', ha='center', fontsize=7, color=GREY)
    ax_rho.text(0.95, 0.23, '1.0', ha='center', fontsize=7, color=GREY)

    # Mini rho history sparkline
    if len(hist['rho']) > 1:
        xs = np.linspace(0.05, 0.95, len(hist['rho']))
        ys = [0.05 + v * 0.15 for v in hist['rho']]
        ax_rho.plot(xs, ys, color=rho_color, lw=1.5, alpha=0.8)
        ax_rho.axhline(0.05 + 0.7 * 0.15, xmin=0.05, xmax=0.95,
                       color=RED, lw=0.8, ls=':', alpha=0.5)
        ax_rho.text(0.5, 0.02, 'ρ history (last 3 min)', ha='center', fontsize=7, color=GREY,
                    transform=ax_rho.transAxes)

    # ── 4. Load history line chart ────────────────────────────────────────────
    ax_hist.cla()
    ax_hist.set_facecolor(PANEL)
    ax_hist.set_title('Load History — Controller A vs B over Time', pad=6)
    ax_hist.set_ylabel('C_Load')
    ax_hist.yaxis.grid(True)
    ax_hist.set_axisbelow(True)

    if hist['time']:
        xs  = range(len(hist['time']))
        la_ = hist['load_a']
        lb_ = hist['load_b']
        ax_hist.plot(xs, la_, color=BLUE,  lw=2,   label='Controller A', alpha=0.9)
        ax_hist.plot(xs, lb_, color=GREEN, lw=2,   label='Controller B', alpha=0.9)
        ax_hist.fill_between(xs, la_, alpha=0.08, color=BLUE)
        ax_hist.fill_between(xs, lb_, alpha=0.08, color=GREEN)

        # Threshold line (use A's ct as reference)
        if ct_a:
            ax_hist.axhline(ct_a, color=YELLOW, lw=1, ls='--', alpha=0.6, label=f'CT={ct_a:.0f}')

        # Migration events: mark where rho dropped below 0.7
        for i, rho_v in enumerate(hist['rho']):
            if rho_v < 0.7 and (i == 0 or hist['rho'][i-1] >= 0.7):
                ax_hist.axvline(i, color=RED, lw=1.5, ls=':', alpha=0.7)
                ax_hist.text(i, max(max(la_), max(lb_)) * 0.95,
                             'migrate?', color=RED, fontsize=7, rotation=90, va='top')

        # X-axis tick labels (show every N-th time)
        n = len(hist['time'])
        step = max(1, n // 8)
        ax_hist.set_xticks(range(0, n, step))
        ax_hist.set_xticklabels(hist['time'][::step], rotation=20, ha='right', fontsize=7)
        ax_hist.set_xlim(0, max(n - 1, 1))
        top = max(max(la_) if la_ else 0, max(lb_) if lb_ else 0, ct_a) * 1.2
        ax_hist.set_ylim(0, max(top, 100))
        ax_hist.legend(loc='upper left', fontsize=8)
    else:
        ax_hist.text(0.5, 0.5, 'Collecting data...', ha='center', va='center',
                     transform=ax_hist.transAxes, color=GREY, fontsize=11)

    # ── 5. Switch distribution pie chart ──────────────────────────────────────
    ax_pie.cla()
    ax_pie.set_facecolor(PANEL)
    ax_pie.set_title('Switch Distribution', pad=6)

    sw_a = (sa.get('managed_switches') or []) if sa else []
    sw_b = (sb.get('managed_switches') or []) if sb else []
    total_sw = len(sw_a) + len(sw_b)

    if total_sw > 0:
        sizes  = [len(sw_a), len(sw_b)]
        labels_ = [f'Ctrl A\n({", ".join(sw_a) or "—"})',
                   f'Ctrl B\n({", ".join(sw_b) or "—"})']
        colors_ = [BLUE, GREEN]
        wedge_props = dict(width=0.55, edgecolor=BG, linewidth=2)
        ax_pie.pie(sizes, labels=labels_, colors=colors_,
                   autopct='%1.0f%%', pctdistance=0.75,
                   wedgeprops=wedge_props, startangle=90,
                   textprops={'fontsize': 8, 'color': TEXT})
        ax_pie.text(0, 0, f'{total_sw}\nswitch', ha='center', va='center',
                    fontsize=10, fontweight='bold', color=TEXT)
    else:
        ax_pie.text(0.5, 0.5, 'No switches\nconnected', ha='center', va='center',
                    transform=ax_pie.transAxes, color=GREY, fontsize=10)

    # ── 6. Per-switch horizontal bar chart ────────────────────────────────────
    ax_hbar.cla()
    ax_hbar.set_facecolor(PANEL)
    ax_hbar.set_title('Per-Switch Load Detail', pad=6)
    ax_hbar.xaxis.grid(True)
    ax_hbar.set_axisbelow(True)

    all_sw = []
    for src, ld in [(sa, la), (sb, lb)]:
        if ld and 'switches' in ld:
            ctrl = src.get('controller', '?') if src else '?'
            ct_v = src.get('ct', 1000) if src else 1000
            for sw in ld['switches']:
                all_sw.append({
                    'name'  : sw['name'],
                    'load'  : sw.get('load', 0),
                    'flows' : sw.get('flows', 0),
                    'rtt'   : sw.get('rtt_ms', 0),
                    'ctrl'  : ctrl,
                    'ct'    : ct_v,
                })

    if all_sw:
        names = [s['name'] for s in all_sw]
        loads = [s['load']  for s in all_sw]
        cols  = [BLUE if s['ctrl'] == 'A' else GREEN for s in all_sw]
        y_pos = range(len(names))

        bars = ax_hbar.barh(y_pos, loads, color=cols,
                            height=0.6, edgecolor=BORDER, linewidth=0.7)
        for i, (bar, sw) in enumerate(zip(bars, all_sw)):
            ax_hbar.text(bar.get_width() + 2, i,
                         f'{sw["load"]:.0f}  (flows:{sw["flows"]} rtt:{sw["rtt"]:.1f}ms)',
                         va='center', fontsize=7.5, color=TEXT)

        ax_hbar.set_yticks(y_pos)
        ax_hbar.set_yticklabels(names, fontsize=9)
        ax_hbar.set_xlabel('C_Load', fontsize=8)
        mx_load = max(loads) if loads else 100
        ax_hbar.set_xlim(0, mx_load * 2.2)
        ax_hbar.invert_yaxis()
        # CT ref line
        ct_ref = all_sw[0]['ct'] if all_sw else 1000
        ax_hbar.axvline(ct_ref, color=YELLOW, lw=1, ls='--', alpha=0.6,
                        label=f'CT={ct_ref:.0f}')
        ax_hbar.legend(loc='lower right', fontsize=7)
    else:
        ax_hbar.text(0.5, 0.5, 'No switches\nconnected', ha='center', va='center',
                     transform=ax_hbar.transAxes, color=GREY, fontsize=10)

    # ── 7. System status info panel ───────────────────────────────────────────
    ax_info.cla()
    ax_info.set_facecolor(PANEL)
    ax_info.set_title('System Status', pad=6)
    ax_info.axis('off')

    lines = []
    ts = time.strftime('%H:%M:%S')
    lines.append(('Update: ' + ts, GREY, 9, False))
    lines.append(('', TEXT, 9, False))

    for label, src in [('Controller A', sa), ('Controller B', sb)]:
        if src:
            st     = src.get('status', 'NORMAL')
            load   = src.get('total_load', 0)
            ct_v   = src.get('ct', 1000)
            mc     = src.get('migration_count', 0)
            up     = src.get('uptime_seconds', 0)
            sw_lst = ', '.join(src.get('managed_switches', []) or ['—'])
            col    = GREEN if st == 'NORMAL' else RED
            lines.append((f'● {label}  [{st}]', col, 10, True))
            lines.append((f'  Load: {load:.1f} / CT: {ct_v:.0f}', TEXT, 9, False))
            lines.append((f'  Switches: {sw_lst}', TEXT, 9, False))
            lines.append((f'  Migrations: {mc}  |  Uptime: {up//60}m{up%60}s', GREY, 8, False))

            mlog = src.get('migration_log', [])
            if mlog:
                last = mlog[-1]
                lines.append((f'  Last migrate: {last["switch"]} ({last["time"]})', ORANGE, 8, False))
        else:
            col = 'A' if label.endswith('A') else 'B'
            port = 8080 if col == 'A' else 8081
            lines.append((f'● {label}  [OFFLINE]', RED, 10, True))
            lines.append((f'  Check: ryu-manager controller_{col.lower()}.py', GREY, 8, False))
            lines.append((f'  Port {port} not responding', GREY, 8, False))
        lines.append(('', TEXT, 8, False))

    # Rho summary
    rho_now = hist['rho'][-1] if hist['rho'] else 1.0
    lines.append((f'ρ = {rho_now:.3f}  →  ' +
                  ('BALANCED ✓' if rho_now >= 0.7 else 'IMBALANCED ✗'),
                  GREEN if rho_now >= 0.7 else RED, 10, True))

    y = 0.97
    for text, color, size, bold in lines:
        ax_info.text(0.04, y, text, transform=ax_info.transAxes,
                     fontsize=size, color=color,
                     fontweight='bold' if bold else 'normal',
                     va='top', family='monospace')
        y -= 0.07 if size >= 10 else 0.055

    # ── Super title ───────────────────────────────────────────────────────────
    fig.suptitle('SDN DALB Real-time Visualization  |  Update every 3s',
                 color=BLUE, fontsize=12, fontweight='bold', y=0.97)


# ── Animation ────────────────────────────────────────────────────────────────
from matplotlib.animation import FuncAnimation

print('Starting DALB Visualization...')
print(f'Controller A: {API_A}')
print(f'Controller B: {API_B}')
print('Close the window to exit.\n')

# Wait for first fetch
time.sleep(1)

ani = FuncAnimation(fig, update, interval=INTERVAL * 1000,
                    cache_frame_data=False)
plt.show()
