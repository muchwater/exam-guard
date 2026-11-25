"""Topology: device station <-> AP <-> server (wired LAN host, wireless LAN station, or WAN host)."""
from __future__ import annotations

from typing import Optional

import numpy as np

from synth.netsim.phy import Phy
from synth.netsim.sim import EventLoop, Medium, Sniffer, Station
from synth.netsim.tcp import Seg, TcpEndpoint

SERVER_KINDS = ("lan_wired", "lan_wireless", "wan")


class Topology:
    def __init__(self, ns: dict, mcs: int, device: str, rng: np.random.Generator, macs: dict):
        self.ns, self.rng, self.macs = ns, rng, macs
        self.loop = EventLoop()
        self.phy = Phy.from_config(ns["phy"])
        self.sniffer = Sniffer({macs["device"]})
        self.medium = Medium(self.loop, self.phy, mcs, rng, self.sniffer)
        self.device_profile = ns["devices"][device]
        self.dev = self._station("device", macs["device"], False, self.device_profile)
        ap = ns["edca_be_ap"]
        self.ap = self.medium.add(Station("ap", macs["ap"].lower(), True, int(ap["aifsn"]), int(ap["cwmin"]),
                                          int(ap["cwmax"]), int(ns["ap"]["ba_window"])))
        self.srv_sta: Optional[Station] = None
        e = ns["encap"]
        self._encap = (int(e["qos_data_header_bytes"]) + int(e["ccmp_header_bytes"]) + int(e["llc_snap_bytes"])
                       + int(e["ccmp_mic_bytes"]))
        self._eth_overhead = int(ns["wired"]["ethernet_overhead_bytes"])
        self._eth_bps = float(ns["wired"]["lan_link_bps"])

    def _station(self, name: str, mac: str, is_ap: bool, prof: dict) -> Station:
        sta = self.ns["edca_be_station"]
        return self.medium.add(Station(name, mac.lower(), is_ap, int(sta["aifsn"]), int(sta["cwmin"]),
                                       int(sta["cwmax"]), int(prof["ba_window"])))

    def frame_bytes(self, seg: Seg) -> int:
        return self._encap + seg.ip_len

    def endpoint(self, tcp_profile: str, name: str, nagle: Optional[bool] = None) -> TcpEndpoint:
        prof = dict(self.ns["tcp"][tcp_profile])
        if nagle is not None:
            prof["nagle"] = nagle
        return TcpEndpoint(self.loop, prof, name, self.rng, self.ns["encap"])

    def flow(self, server_kind: str, wan_rtt_s: Optional[float] = None) -> tuple[TcpEndpoint, TcpEndpoint]:
        """Client endpoint on the device and a server endpoint reached through the AP."""
        if server_kind not in SERVER_KINDS:
            raise ValueError(f"server_kind must be one of {SERVER_KINDS}")
        if server_kind == "wan" and wan_rtt_s is None:
            raise ValueError("a wan server needs wan_rtt_s (data/measured/wan_rtt_*.json)")
        dp = self.device_profile
        c = self.endpoint(dp["tcp"], "client", nagle=bool(dp["app_nagle"]))
        sk = self.ns["servers"][server_kind]
        s = self.endpoint(sk["tcp"], "server")
        m, loop = self.medium, self.loop
        if server_kind == "lan_wireless" and self.srv_sta is None:
            self.srv_sta = self._station("server", self.macs["server"], False, self.ns["devices"][sk["device"]])

        def wire(seg: Seg) -> float:
            if server_kind == "wan":
                return wan_rtt_s / 2.0
            return (seg.ip_len + self._eth_overhead) * 8.0 / self._eth_bps     # serialization only

        def up(seg: Seg) -> None:
            if server_kind == "lan_wireless":
                def at_ap():
                    m.send(self.ap, self.srv_sta, self.frame_bytes(seg), lambda: s.recv(seg))
            else:
                def at_ap():
                    loop.after(wire(seg), s.recv, seg)
            m.send(self.dev, self.ap, self.frame_bytes(seg), at_ap)

        def down(seg: Seg) -> None:
            def to_dev():
                m.send(self.ap, self.dev, self.frame_bytes(seg), lambda: c.recv(seg))
            if server_kind == "lan_wireless":
                m.send(self.srv_sta, self.ap, self.frame_bytes(seg), to_dev)
            else:
                loop.after(wire(seg), to_dev)

        c.out, s.out = up, down
        return c, s
