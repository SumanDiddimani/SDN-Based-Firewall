from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ipv4, tcp, udp
import logging
import datetime

class SDNFirewall(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(SDNFirewall, self).__init__(*args, **kwargs)
        self.mac_to_port = {}

        # FIREWALL RULES: (src_ip, dst_ip, protocol, dst_port)
        # None means match anything
        self.blocked_rules = [
            ('10.0.0.2', '10.0.0.3', None, None),  # Block ALL h2 -> h3
            ('10.0.0.1', '10.0.0.3', 'tcp', 80),   # Block h1 -> h3 on port 80
        ]

        logging.basicConfig(
            filename='/root/sdn-firewall/firewall_log.txt',
            level=logging.INFO,
            format='%(message)s'
        )
        self.logger.info("SDN Firewall Started")

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    def add_flow(self, datapath, priority, match, actions, idle_timeout=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(
            ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath, priority=priority,
            idle_timeout=idle_timeout,
            match=match, instructions=inst)
        datapath.send_msg(mod)

    def is_blocked(self, src_ip, dst_ip, proto, dst_port):
        for rule in self.blocked_rules:
            r_src, r_dst, r_proto, r_port = rule
            if r_src and r_src != src_ip: continue
            if r_dst and r_dst != dst_ip: continue
            if r_proto and r_proto != proto: continue
            if r_port and r_port != dst_port: continue
            return True
        return False

    def log_blocked(self, src_ip, dst_ip, proto, port):
        msg = f"[{datetime.datetime.now()}] BLOCKED: {src_ip} -> {dst_ip} | proto={proto} port={port}"
        logging.getLogger().info(msg)
        print(msg)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        ip_pkt = pkt.get_protocol(ipv4.ipv4)

        dst_mac = eth.dst
        src_mac = eth.src
        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src_mac] = in_port
        out_port = self.mac_to_port[dpid].get(dst_mac, ofproto.OFPP_FLOOD)

        if ip_pkt:
            src_ip = ip_pkt.src
            dst_ip = ip_pkt.dst
            proto = None
            dst_port = None

            tcp_pkt = pkt.get_protocol(tcp.tcp)
            udp_pkt = pkt.get_protocol(udp.udp)
            if tcp_pkt:
                proto = 'tcp'
                dst_port = tcp_pkt.dst_port
            elif udp_pkt:
                proto = 'udp'
                dst_port = udp_pkt.dst_port

            if self.is_blocked(src_ip, dst_ip, proto, dst_port):
                self.log_blocked(src_ip, dst_ip, proto, dst_port)
                match = parser.OFPMatch(
                    eth_type=0x0800,
                    ipv4_src=src_ip,
                    ipv4_dst=dst_ip)
                self.add_flow(datapath, 10, match, [], idle_timeout=60)
                return

        actions = [parser.OFPActionOutput(out_port)]
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst_mac)
            self.add_flow(datapath, 1, match, actions)

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=msg.data)
        datapath.send_msg(out)
