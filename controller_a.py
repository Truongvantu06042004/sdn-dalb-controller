"""
Controller A — Ryu SDN application implementing:
  - OpenFlow 1.3 L2 learning switch
  - ARP proxy: answers cross-domain ARP requests so pingall works
  - IP-based inter-domain forwarding (10.0.1.x <-> 10.0.2.x via S2-S4 link)
  - Per-switch traffic monitoring (flow stats, port stats, RTT echo)
  - DALB (Dynamic and Adaptive Load Balancing) decision logic
  - REST API: GET /load  GET /status  GET /arp  POST /migrate

Run with:
    ryu-manager controller_a.py --ofp-tcp-listen-port 6633
"""

import json
import time
import threading
import requests

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, arp, ipv4, ether_types
from ryu.lib import hub
from ryu.app.wsgi import WSGIApplication, ControllerBase, route

from dalb_module import DALBMetrics

# ── Configuration ─────────────────────────────────────────────────────────────
CONFIG = {
    'OF_PORT'           : 6633,
    'REST_PORT'         : 8080,
    'PEER_REST_URL'     : 'http://127.0.0.1:8081',
    'CT_INITIAL'        : 1000,
    'RHO_THRESHOLD'     : 0.7,
    'MONITOR_INTERVAL'  : 10,
    'CONTROLLER_NAME'   : 'A',
    'DOMAIN_PREFIX'     : '10.0.1',
    'PEER_PREFIX'       : '10.0.2',
    # dpid -> inter-domain port number on that switch (S2 port4 = inter-domain)
    'INTER_DOMAIN_PORTS': {2: 4},
    # dpids this controller owns at startup; updated on migration
    'INITIAL_SWITCHES'  : [1, 2],
}
# ──────────────────────────────────────────────────────────────────────────────

NAME = CONFIG['CONTROLLER_NAME']

# Force Ryu WSGI to bind on our configured port instead of the default 8080
try:
    from ryu import cfg as _ryu_cfg
    _ryu_cfg.CONF.ofp_tcp_listen_port = CONFIG['OF_PORT']   # 6633
    _ryu_cfg.CONF.wsapi_port = CONFIG['REST_PORT']          # 8080
    _ryu_cfg.CONF.wsapi_host = '0.0.0.0'
except Exception:
    pass


def _ts():
    return time.strftime('%H:%M:%S')


class DALBController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'wsgi': WSGIApplication}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        kwargs['wsgi'].register(DALBRestAPI, {'ctrl': self})

        self.dalb       = DALBMetrics()
        self.lock       = threading.Lock()
        self.start_time = time.time()
        self.migration_count = 0

        # OpenFlow state
        self.datapaths    = {}   # dpid -> datapath
        self.switch_names = {}   # dpid -> 'S1' / 'S2' ...

        # L2 learning: dpid -> {mac -> port}
        self.mac_to_port  = {}

        # ARP / IP learning: ip -> mac, dpid -> {ip -> port}
        self.ip_to_mac    = {}
        self.ip_to_port   = {}   # dpid -> {ip -> port}

        # Per-switch metrics
        self.flow_count       = {}
        self.packet_in_rate   = {}
        self.rtt              = {}
        self.port_stats_prev  = {}
        self.prev_sample_time = {}

        # DALB state
        self.ct = float(CONFIG['CT_INITIAL'])
        self.inter_domain_ports = dict(CONFIG['INTER_DOMAIN_PORTS'])
        self.migration_log = []   # list of {time, switch, from, to}
        self.owned_switches = set(CONFIG['INITIAL_SWITCHES'])

        self.monitor_thread = hub.spawn(self._monitor_loop)

    # ── OpenFlow: switch connect ───────────────────────────────────────────────

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp   = ev.msg.datapath
        dpid = dp.id
        ofp  = dp.ofproto
        parser = dp.ofproto_parser

        with self.lock:
            self.datapaths[dpid]     = dp
            self.switch_names[dpid]  = f'S{dpid}'
            self.mac_to_port.setdefault(dpid, {})
            self.ip_to_port.setdefault(dpid, {})
            self.flow_count[dpid]       = 0
            self.packet_in_rate[dpid]   = 0.0
            self.rtt[dpid]              = 1.0
            self.port_stats_prev[dpid]  = {}
            self.prev_sample_time[dpid] = time.time()

        if dpid not in self.owned_switches:
            # Not our domain — be SLAVE (read-only, no Packet-In)
            dp.send_msg(parser.OFPRoleRequest(
                dp, role=ofp.OFPCR_ROLE_SLAVE, generation_id=0))
            self.logger.info(f'[{_ts()}][SWITCH] S{dpid} → SLAVE on ctrl={NAME}')
            return

        # Table-miss: send all unmatched packets to controller
        self._add_flow(dp, priority=0,
                       match=parser.OFPMatch(),
                       actions=[parser.OFPActionOutput(ofp.OFPP_CONTROLLER,
                                                       ofp.OFPCML_NO_BUFFER)])
        self.logger.info(f'[{_ts()}][SWITCH] S{dpid} connected — OF1.3 | ctrl={NAME}')

    # ── OpenFlow: packet-in ────────────────────────────────────────────────────

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg    = ev.msg
        dp     = msg.datapath
        dpid   = dp.id
        if dpid not in self.owned_switches:
            return
        ofp    = dp.ofproto
        parser = dp.ofproto_parser

        in_port = msg.match['in_port']
        pkt     = packet.Packet(msg.data)
        eth     = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        src_mac = eth.src
        dst_mac = eth.dst

        with self.lock:
            self.mac_to_port.setdefault(dpid, {})[src_mac] = in_port

        # ── ARP handling (proxy + learning) ─────────────────────────────────
        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            if self._handle_arp(dp, msg, in_port, pkt, src_mac):
                return
            # ARP reply or unknown — fall through to L2 forward

        # ── IPv4 inter-domain forwarding ────────────────────────────────────
        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        if ip_pkt:
            src_ip = ip_pkt.src
            dst_ip = ip_pkt.dst

            # Learn IP→MAC from every IP packet
            with self.lock:
                self.ip_to_mac[src_ip] = src_mac
                self.ip_to_port.setdefault(dpid, {})[src_ip] = in_port

            my_pfx   = CONFIG['DOMAIN_PREFIX']
            peer_pfx = CONFIG['PEER_PREFIX']

            if src_ip.startswith(my_pfx) and dst_ip.startswith(peer_pfx):
                self._fwd_interdomain_out(dp, msg, in_port, dst_ip)
                return

            if src_ip.startswith(peer_pfx) and dst_ip.startswith(my_pfx):
                self._fwd_interdomain_in(dp, msg, in_port, dst_ip, dst_mac)
                return

        # ── L2 learning switch ────────────────────────────────────────────
        with self.lock:
            out_port = self.mac_to_port[dpid].get(dst_mac, ofp.OFPP_FLOOD)

        actions = [parser.OFPActionOutput(out_port)]
        if out_port != ofp.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst_mac, eth_src=src_mac)
            self._add_flow(dp, priority=10, match=match,
                           actions=actions, idle_timeout=30)

        self._packet_out(dp, msg, in_port, actions)

    # ── ARP proxy ─────────────────────────────────────────────────────────────

    def _handle_arp(self, dp, msg, in_port, pkt, src_mac):
        """
        Process ARP packets. Returns True if the packet was fully handled
        (no further processing needed), False to fall through to L2 forward.
        """
        arp_pkt = pkt.get_protocol(arp.arp)
        if arp_pkt is None:
            return False

        src_ip = arp_pkt.src_ip
        dst_ip = arp_pkt.dst_ip

        # Always learn sender IP→MAC
        with self.lock:
            self.ip_to_mac[src_ip] = src_mac
            self.ip_to_port.setdefault(dp.id, {})[src_ip] = in_port

        if arp_pkt.opcode != arp.ARP_REQUEST:
            return False  # ARP reply — let L2 forwarding deliver it

        my_pfx   = CONFIG['DOMAIN_PREFIX']
        peer_pfx = CONFIG['PEER_PREFIX']

        if dst_ip.startswith(my_pfx):
            # Intra-domain ARP request
            with self.lock:
                target_mac = self.ip_to_mac.get(dst_ip)
            if target_mac:
                self._send_arp_reply(dp, in_port, src_mac, src_ip, target_mac, dst_ip)
                return True
            # Unknown — fall through to flood

        elif dst_ip.startswith(peer_pfx):
            # Cross-domain ARP request — ask peer controller
            target_mac = self._query_peer_arp(dst_ip)
            if target_mac:
                self._send_arp_reply(dp, in_port, src_mac, src_ip, target_mac, dst_ip)
                # Pre-install flow on border switch: eth_dst=target_mac → inter port
                inter_port = self.inter_domain_ports.get(dp.id)
                if inter_port and inter_port != in_port:
                    parser = dp.ofproto_parser
                    match  = parser.OFPMatch(eth_dst=target_mac)
                    self._add_flow(dp, priority=15,
                                   match=match,
                                   actions=[parser.OFPActionOutput(inter_port)],
                                   idle_timeout=120)
                return True
            # Peer doesn't know yet — flood so ARP reaches domain B naturally
            # (safe: OFPP_FLOOD excludes in_port, no loop on single inter-domain link)

        return False

    def _query_peer_arp(self, ip):
        """Ask peer controller REST /arp?ip=... for a MAC. Returns MAC or None."""
        try:
            r = requests.get(f'{CONFIG["PEER_REST_URL"]}/arp',
                             params={'ip': ip}, timeout=1)
            if r.status_code == 200:
                return r.json().get('mac')
        except requests.exceptions.RequestException:
            pass
        return None

    def _send_arp_reply(self, dp, out_port, dst_mac, dst_ip, src_mac, src_ip):
        """Craft and send an ARP reply on behalf of src_ip/src_mac."""
        e = ethernet.ethernet(dst=dst_mac, src=src_mac,
                              ethertype=ether_types.ETH_TYPE_ARP)
        a = arp.arp(opcode=arp.ARP_REPLY,
                    src_mac=src_mac, src_ip=src_ip,
                    dst_mac=dst_mac, dst_ip=dst_ip)
        p = packet.Packet()
        p.add_protocol(e)
        p.add_protocol(a)
        p.serialize()

        ofp    = dp.ofproto
        parser = dp.ofproto_parser
        out    = parser.OFPPacketOut(
            datapath=dp, buffer_id=ofp.OFP_NO_BUFFER,
            in_port=ofp.OFPP_CONTROLLER,
            actions=[parser.OFPActionOutput(out_port)],
            data=p.data)
        dp.send_msg(out)
        self.logger.debug(
            f'[{_ts()}][ARP] Proxy reply {src_ip}={src_mac} → port {out_port} on S{dp.id}')

    # ── Inter-domain forwarding ────────────────────────────────────────────────

    def _fwd_interdomain_out(self, dp, msg, in_port, dst_ip):
        """Forward outbound traffic (my domain → peer domain) to inter-domain port."""
        dpid       = dp.id
        parser     = dp.ofproto_parser
        ofp        = dp.ofproto
        inter_port = self.inter_domain_ports.get(dpid)

        if inter_port is None:
            # Not the border switch — flood toward it
            self._packet_out(dp, msg, in_port,
                             [parser.OFPActionOutput(ofp.OFPP_FLOOD)])
            return

        actions = [parser.OFPActionOutput(inter_port)]
        # /24 prefix match covers all hosts in peer domain
        match = parser.OFPMatch(
            eth_type=ether_types.ETH_TYPE_IP,
            ipv4_dst=(CONFIG['PEER_PREFIX'] + '.0', '255.255.255.0'))
        self._add_flow(dp, priority=20, match=match, actions=actions, idle_timeout=60)
        self._packet_out(dp, msg, in_port, actions)
        self.logger.debug(
            f'[{_ts()}][FWD] Out {dst_ip} via inter-domain port {inter_port} on S{dpid}')

    def _fwd_interdomain_in(self, dp, msg, in_port, dst_ip, dst_mac):
        """Forward inbound traffic (peer domain → my domain) to destination host."""
        dpid   = dp.id
        parser = dp.ofproto_parser
        ofp    = dp.ofproto

        # Try exact port from MAC learning table on this switch
        with self.lock:
            out_port = self.mac_to_port[dpid].get(dst_mac)

        if out_port is not None:
            actions = [parser.OFPActionOutput(out_port)]
            match   = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, ipv4_dst=dst_ip)
            self._add_flow(dp, priority=20, match=match, actions=actions, idle_timeout=60)
            self._packet_out(dp, msg, in_port, actions)
            return

        # dst_mac not learned on this switch yet — flood within domain
        # OFPP_FLOOD excludes in_port, so no loop back to inter-domain side
        self._packet_out(dp, msg, in_port, [parser.OFPActionOutput(ofp.OFPP_FLOOD)])

    # ── Stats reply handlers ──────────────────────────────────────────────────

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        with self.lock:
            self.flow_count[dpid] = max(0, len(ev.msg.body) - 1)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        dpid   = ev.msg.datapath.id
        now    = time.time()
        i_port = self.inter_domain_ports.get(dpid, -1)

        total_rx_delta = 0
        with self.lock:
            elapsed = max(now - self.prev_sample_time.get(dpid, now), 1.0)
            for stat in ev.msg.body:
                pno = stat.port_no
                if pno in (ev.msg.datapath.ofproto.OFPP_LOCAL, i_port):
                    continue
                rx_prev = self.port_stats_prev.get(dpid, {}).get(pno, stat.rx_packets)
                total_rx_delta += max(0, stat.rx_packets - rx_prev)
                self.port_stats_prev.setdefault(dpid, {})[pno] = stat.rx_packets
            self.packet_in_rate[dpid] = total_rx_delta / elapsed
            self.prev_sample_time[dpid] = now

    @set_ev_cls(ofp_event.EventOFPEchoReply, MAIN_DISPATCHER)
    def echo_reply_handler(self, ev):
        try:
            rtt_ms = (time.time() - float(ev.msg.data.decode())) * 1000.0
        except Exception:
            rtt_ms = 1.0
        with self.lock:
            self.rtt[ev.msg.datapath.id] = rtt_ms

    # ── OpenFlow helpers ──────────────────────────────────────────────────────

    def _add_flow(self, dp, priority, match, actions,
                  idle_timeout=0, hard_timeout=0):
        parser = dp.ofproto_parser
        ofp    = dp.ofproto
        inst   = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(
            datapath=dp, priority=priority, match=match,
            instructions=inst,
            idle_timeout=idle_timeout, hard_timeout=hard_timeout))

    def _packet_out(self, dp, msg, in_port, actions):
        ofp    = dp.ofproto
        parser = dp.ofproto_parser
        data   = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        dp.send_msg(parser.OFPPacketOut(
            datapath=dp, buffer_id=msg.buffer_id,
            in_port=in_port, actions=actions, data=data))

    def _req_flow_stats(self, dp):
        ofp = dp.ofproto
        dp.send_msg(dp.ofproto_parser.OFPFlowStatsRequest(dp, table_id=ofp.OFPTT_ALL))

    def _req_port_stats(self, dp):
        dp.send_msg(dp.ofproto_parser.OFPPortStatsRequest(
            dp, port_no=dp.ofproto.OFPP_ANY))

    def _req_echo(self, dp):
        dp.send_msg(dp.ofproto_parser.OFPEchoRequest(
            dp, data=str(time.time()).encode()))

    # ── Monitor loop ──────────────────────────────────────────────────────────

    def _monitor_loop(self):
        hub.sleep(CONFIG['MONITOR_INTERVAL'])
        while True:
            with self.lock:
                dps = dict(self.datapaths)
            for dpid, dp in dps.items():
                self._req_flow_stats(dp)
                self._req_port_stats(dp)
                self._req_echo(dp)
            hub.sleep(2)
            self._log_load_table()
            self._run_dalb()
            hub.sleep(max(1, CONFIG['MONITOR_INTERVAL'] - 2))

    def _switch_metrics(self):
        """Return per-switch metric dicts for all managed switches."""
        rows = []
        with self.lock:
            dpids = [d for d in self.datapaths if d in self.owned_switches]
        for dpid in dpids:
            with self.lock:
                n    = self.flow_count.get(dpid, 0)
                f    = self.packet_in_rate.get(dpid, 0.0)
                r    = self.rtt.get(dpid, 1.0)
                name = self.switch_names.get(dpid, f'S{dpid}')
            rows.append({'dpid': dpid, 'name': name,
                         'load': self.dalb.calculate_switch_load(n, f, r),
                         'flows': n, 'rate': f, 'rtt_ms': r})
        return rows

    def get_total_load(self):
        return self.dalb.calculate_controller_load(
            [m['load'] for m in self._switch_metrics()])

    def _log_load_table(self):
        metrics = self._switch_metrics()
        total   = self.dalb.calculate_controller_load([m['load'] for m in metrics])
        ct      = self.ct
        status  = 'NORMAL' if total < ct else 'EXCEEDED'
        t       = _ts()
        W  = 64
        cw = W - 40   # last column width — pre-computed to avoid Python 3.9 set-literal bug
        sep_h = '─' * 9 + '┬' + '─' * 7 + '┬' + '─' * 13 + '┬' + '─' * 8 + '┬' + '─' * cw
        sep_m = '─' * 9 + '┼' + '─' * 7 + '┼' + '─' * 13 + '┼' + '─' * 8 + '┼' + '─' * cw
        sep_f = '─' * 9 + '┴' + '─' * 7 + '┴' + '─' * 13 + '┴' + '─' * 8 + '┴' + '─' * cw
        print(f'┌{"─"*W}┐')
        print(f'│ [{NAME}][LOAD] Controller {NAME} | {t:<{W-26}}│')
        print(f'├{sep_h}┤')
        print(f'│ {"Switch":<7} │{"Flows":^7}│{"Pkt-in/s":^13}│{"RTT":^8}│{"C_Load":^{cw}}│')
        print(f'├{sep_m}┤')
        for m in metrics:
            print(f'│ {m["name"]:<7} │{m["flows"]:^7}│{m["rate"]:^11.1f}/s│'
                  f'{m["rtt_ms"]:^6.1f}ms│{m["load"]:^{cw}.2f}│')
        print(f'├{sep_f}┤')
        print(f'│ Total: {total:.1f}/s | CT={ct:.0f} | {status:<{W-30}}│')
        print(f'└{"─"*W}┘')

    # ── DALB decision ─────────────────────────────────────────────────────────

    def _run_dalb(self):
        metrics = self._switch_metrics()
        my_load = self.dalb.calculate_controller_load([m['load'] for m in metrics])
        ct      = self.ct

        if my_load < ct:
            self.logger.info(
                f'[{_ts()}][DALB] Load={my_load:.1f}/s < CT={ct:.0f} | NORMAL ✅')
            return

        self.logger.info(
            f'[{_ts()}][DALB] Load={my_load:.1f}/s >= CT={ct:.0f} | EXCEEDED ⚠️')

        try:
            r = requests.get(f'{CONFIG["PEER_REST_URL"]}/load', timeout=3)
            r.raise_for_status()
            peer_data = r.json()
            peer_load = peer_data.get('total_load', 0.0)
            peer_name = peer_data.get('controller', 'peer')
        except requests.exceptions.RequestException as e:
            self.logger.warning(
                f'[{_ts()}][DALB] Peer unreachable: {e} — skipping migration')
            return

        self.logger.info(
            f'[{_ts()}][DALB] Peer Controller {peer_name} Load: {peer_load:.1f}/s')

        all_loads   = {NAME: my_load, peer_name: peer_load}
        load_values = [my_load, peer_load]
        rho         = self.dalb.calculate_rho(load_values)

        can_migrate, reason = self.dalb.should_migrate(my_load, all_loads, ct)
        self.logger.info(f'[{_ts()}][DALB] ρ={rho:.3f} | {reason}')

        if not can_migrate:
            return

        self.logger.info(f'[{_ts()}][DALB] → MIGRATION TRIGGERED!')

        chosen = self.dalb.select_switch_to_migrate(metrics, my_load, peer_load)
        if chosen is None:
            self.logger.info(f'[{_ts()}][DALB] No suitable switch found')
            return

        self._do_migrate(chosen, peer_name)

        new_ct = self.dalb.adaptive_ct(load_values, ict=CONFIG['CT_INITIAL'])
        with self.lock:
            self.ct = new_ct
        exp_load = my_load - chosen['load']
        new_rho  = self.dalb.calculate_rho([exp_load, peer_load + chosen['load']])
        self.logger.info(
            f'[{_ts()}][MIGRATE] New CT={new_ct:.1f} | '
            f'Expected load ~{exp_load:.0f}/s | ρ→{new_rho:.3f}')

    def _do_migrate(self, sw, target_name):
        dpid, name, load = sw['dpid'], sw['name'], sw['load']
        self.logger.info(
            f'[{_ts()}][MIGRATE] {name} (load={load:.1f}) '
            f'Controller {NAME} → {target_name}')

        with self.lock:
            dp = self.datapaths.get(dpid)
        if dp:
            ofp = dp.ofproto
            dp.send_msg(dp.ofproto_parser.OFPRoleRequest(
                dp, role=ofp.OFPCR_ROLE_SLAVE, generation_id=0))

        try:
            r = requests.post(
                f'{CONFIG["PEER_REST_URL"]}/migrate',
                json={'dpid': dpid, 'name': name}, timeout=5)
            r.raise_for_status()
            self.logger.info(
                f'[{_ts()}][MIGRATE] ✅ SUCCESS: {name} → {target_name} | {r.json()}')
        except requests.exceptions.RequestException as e:
            self.logger.error(f'[{_ts()}][MIGRATE] ❌ FAILED: {e}')
            if dp:
                ofp = dp.ofproto
                dp.send_msg(dp.ofproto_parser.OFPRoleRequest(
                    dp, role=ofp.OFPCR_ROLE_MASTER, generation_id=0))
            return

        with self.lock:
            self.owned_switches.discard(dpid)   # release ownership; TCP conn stays
            self.switch_names.pop(dpid, None)
            self.migration_count += 1
            self.migration_log.append({
                'time': _ts(), 'switch': name,
                'from': NAME, 'to': target_name,
            })

    def accept_switch(self, dpid, name):
        """Promote an incoming switch to MASTER (called from REST /migrate)."""
        with self.lock:
            dp = self.datapaths.get(dpid)
        if dp is None:
            for _ in range(10):
                hub.sleep(1)
                with self.lock:
                    dp = self.datapaths.get(dpid)
                if dp:
                    break
        if dp is None:
            self.logger.warning(
                f'[{_ts()}][MIGRATE] {name} not connected after wait')
            return False

        ofp    = dp.ofproto
        parser = dp.ofproto_parser
        # Use a time-based gen_id (larger than the initial 0) so OVS accepts
        gen_id = int(time.time()) & 0xFFFFFFFF
        dp.send_msg(parser.OFPRoleRequest(
            dp, role=ofp.OFPCR_ROLE_MASTER, generation_id=gen_id))

        with self.lock:
            self.owned_switches.add(dpid)
            self.switch_names[dpid] = name
            self.flow_count.setdefault(dpid, 0)
            self.packet_in_rate.setdefault(dpid, 0.0)
            self.rtt.setdefault(dpid, 1.0)

        # (Re)install table-miss so the switch sends Packet-In to us
        self._add_flow(dp, priority=0,
                       match=parser.OFPMatch(),
                       actions=[parser.OFPActionOutput(ofp.OFPP_CONTROLLER,
                                                       ofp.OFPCML_NO_BUFFER)])
        self.logger.info(
            f'[{_ts()}][MIGRATE] {name} → MASTER on Controller {NAME}')
        return True

    # ── REST data helpers ─────────────────────────────────────────────────────

    def get_load_data(self):
        metrics = self._switch_metrics()
        total   = self.dalb.calculate_controller_load([m['load'] for m in metrics])
        return {
            'controller': NAME,
            'total_load': round(total, 2),
            'ct'        : self.ct,
            'switches'  : [{'name': m['name'], 'dpid': m['dpid'],
                             'load': round(m['load'], 2),
                             'flows': m['flows'],
                             'rate' : round(m.get('rate', 0.0), 2),
                             'rtt_ms': round(m['rtt_ms'], 2)} for m in metrics],
            'timestamp' : _ts(),
        }

    def get_status_data(self):
        metrics = self._switch_metrics()
        total   = self.dalb.calculate_controller_load([m['load'] for m in metrics])
        # Try to get peer load for accurate cross-controller rho
        peer_load = None
        peer_status = 'OFFLINE'
        try:
            r = requests.get(f'{CONFIG["PEER_REST_URL"]}/load', timeout=2)
            if r.status_code == 200:
                resp = r.json()
                peer_load   = resp.get('total_load', 0.0)
                peer_status = 'ONLINE'
        except requests.exceptions.RequestException:
            pass
        load_values = [total, peer_load] if peer_load is not None else [total]
        rho = self.dalb.calculate_rho(load_values)
        with self.lock:
            managed = sorted(self.switch_names.get(d, f'S{d}')
                             for d in self.datapaths if d in self.owned_switches)
            mc  = self.migration_count
            log = list(self.migration_log[-20:])   # last 20 entries
        return {
            'controller'      : NAME,
            'total_load'      : round(total, 2),
            'peer_load'       : round(peer_load, 2) if peer_load is not None else None,
            'peer_status'     : peer_status,
            'ct'              : self.ct,
            'rho'             : round(rho, 3),
            'status'          : 'NORMAL' if total < self.ct else 'OVERLOADED',
            'managed_switches': managed,
            'migration_count' : mc,
            'migration_log'   : log,
            'uptime_seconds'  : int(time.time() - self.start_time),
        }


# ── REST API ──────────────────────────────────────────────────────────────────

class DALBRestAPI(ControllerBase):

    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.ctrl: DALBController = data['ctrl']

    # GET /load
    @route('load', '/load', methods=['GET'])
    def get_load(self, req, **_):
        return _json(self.ctrl.get_load_data())

    # GET /status
    @route('status', '/status', methods=['GET'])
    def get_status(self, req, **_):
        return _json(self.ctrl.get_status_data())

    # GET /arp?ip=x.x.x.x
    @route('arp_lookup', '/arp', methods=['GET'])
    def get_arp(self, req, **_):
        ip = req.GET.get('ip', '')
        with self.ctrl.lock:
            mac = self.ctrl.ip_to_mac.get(ip)
        if mac:
            return _json({'ip': ip, 'mac': mac})
        return _json({'ip': ip, 'mac': None}, code=404)

    # POST /migrate  body: {"dpid": 3, "name": "S3"}
    @route('migrate', '/migrate', methods=['POST'])
    def post_migrate(self, req, **_):
        try:
            body = json.loads(req.body)
            dpid = int(body['dpid'])
            name = str(body['name'])
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return _json({'status': 'error', 'message': str(e)}, code=400)

        ok = self.ctrl.accept_switch(dpid, name)
        if ok:
            return _json({'status': 'ok',
                          'message': f'{name} is now MASTER on Controller {NAME}'})
        return _json({'status': 'error',
                      'message': f'{name} (dpid={dpid}) not reachable'}, code=503)


def _json(data, code=200):
    from webob import Response
    body = json.dumps(data, indent=2).encode('utf-8')
    r = Response(content_type='application/json', body=body, status=code)
    r.headers['Access-Control-Allow-Origin'] = '*'   # allow dashboard CORS
    return r
