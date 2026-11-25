"""TCP endpoints with the behaviours that shape packet timing: handshake, MSS, initial window,
slow start / congestion avoidance, send-buffer and receive-window limits, Nagle, delayed ACK.

Two stack profiles (configs synth.netsim.tcp.*):
  lwip   ESP-IDF lwIP defaults (ESP32-CAM class device)
  linux  Linux defaults (phone / laptop / server)
No loss model: the MAC layer retries until delivery (sim.py), so TCP never retransmits.
Sequence numbers count payload bytes only; a FIN occupies one number after the last byte.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from synth.netsim.sim import EventLoop


@dataclass
class Seg:
    src: "TcpEndpoint"
    seq: int
    length: int
    ack: Optional[int]
    syn: bool = False
    fin: bool = False
    opt_bytes: int = 0
    ip_header_bytes: int = 20
    tcp_header_bytes: int = 20

    @property
    def ip_len(self) -> int:
        return self.ip_header_bytes + self.tcp_header_bytes + self.opt_bytes + self.length


class TcpEndpoint:
    def __init__(self, loop: EventLoop, prof: dict, name: str, rng: np.random.Generator, hdr: dict):
        self.loop, self.p, self.name, self.rng = loop, prof, name, rng
        self.ip_hdr, self.tcp_hdr = int(hdr["ipv4_header_bytes"]), int(hdr["tcp_header_bytes"])
        self.out: Callable[[Seg], None] = lambda seg: None
        self.peer: Optional["TcpEndpoint"] = None
        self.established = False
        # sender
        self.mss = 0
        self.opt_bytes = 0
        self.snd_una = self.snd_nxt = self.app_end = 0
        self.cwnd = 0.0
        self.ssthresh = math.inf
        self.peer_wnd = 0
        self.fin_queued = self.fin_sent = self.fin_acked = False
        self.nagle = bool(prof["nagle"])
        # receiver
        self.rcv_nxt = 0
        self.rcv_wup = 0
        self.fin_received = False
        self.quick = 0
        self._delack_pending = False
        self._delack_gen = 0
        self._lwip_phase = float(rng.random()) * float(prof.get("delack_timer_period_s") or 0.0)
        # app callbacks
        self.on_established: Callable[[], None] = lambda: None
        self.on_data: Callable[[int], None] = lambda total: None
        self.on_acked: Callable[[int], None] = lambda una: None
        self.on_fin: Callable[[], None] = lambda: None

    # ---------------------------------------------------------------- negotiation helpers
    def _uses_ts(self, other: "TcpEndpoint") -> bool:
        return bool(self.p["timestamps"]) and bool(other.p["timestamps"])

    def _setup(self, other: "TcpEndpoint") -> None:
        """Effective MSS / options once both SYNs are known (RFC 9293 MSS option, RFC 7323 timestamps)."""
        self.peer = other
        ts = self._uses_ts(other)
        self.opt_bytes = int(self.p["ts_option_bytes"]) if ts else 0
        self.mss = min(int(self.p["mss_offer"]), int(other.p["mss_offer"])) - self.opt_bytes
        self.peer_wnd = int(other.p["rcv_wnd_bytes"])
        if self.p["initial_cwnd"] == "lwip":     # lwIP tcp_in.c LWIP_TCP_CALC_INITIAL_CWND (RFC 3390)
            self.cwnd = float(min(4 * self.mss, max(2 * self.mss, 4380)))
        else:
            self.cwnd = float(int(self.p["initial_cwnd"]) * self.mss)
        st = self.p.get("ssthresh_init_bytes")
        self.ssthresh = math.inf if st is None else float(st)
        if self.p["ack_policy"] == "linux":      # tcp_input.c tcp_incr_quickack
            q = int(self.p["rcv_wnd_bytes"]) // (2 * self.mss)
            self.quick = min(q if q else 2, int(self.p["quickack_max"]))

    def _syn_opts(self, other: Optional["TcpEndpoint"]) -> int:
        mine = int(self.p["syn_option_bytes"])
        return mine if other is None else min(mine, int(other.p["syn_option_bytes"]))

    def _seg(self, seq: int, length: int, ack: Optional[int], syn=False, fin=False, opt=None) -> Seg:
        return Seg(self, seq, length, ack, syn, fin, self.opt_bytes if opt is None else opt, self.ip_hdr,
                   self.tcp_hdr)

    def _ack_value(self) -> int:
        return self.rcv_nxt

    # ---------------------------------------------------------------- application API
    def connect(self, server: "TcpEndpoint") -> None:
        self.peer = server
        self.out(self._seg(0, 0, None, syn=True, opt=self._syn_opts(None)))

    def write(self, nbytes: int) -> None:
        self.app_end += int(nbytes)
        self._try_send()

    def close(self) -> None:
        self.fin_queued = True
        self._try_send()

    # ---------------------------------------------------------------- sending
    def _send_ack(self) -> None:
        self._delack_pending = False
        self._delack_gen += 1
        self.rcv_wup = self.rcv_nxt
        self.out(self._seg(self.snd_nxt, 0, self._ack_value()))

    def _piggyback(self) -> None:
        if self._delack_pending or self.rcv_wup != self.rcv_nxt:
            self._delack_pending = False
            self._delack_gen += 1
            self.rcv_wup = self.rcv_nxt

    def _try_send(self) -> None:
        if not self.established:
            return
        while True:
            inflight = self.snd_nxt - self.snd_una
            limit = min(self.cwnd, self.peer_wnd)
            if self.p.get("snd_buf_bytes") is not None:
                limit = min(limit, int(self.p["snd_buf_bytes"]))
            avail = self.app_end - self.snd_nxt
            if avail > 0:
                n = min(self.mss, avail)
                room = int(limit - inflight)
                if room < n:
                    break                                      # window full
                if n < self.mss and inflight > 0 and self.nagle:
                    break                                      # Nagle (RFC 896): hold a small segment
                self._piggyback()
                self.out(self._seg(self.snd_nxt, n, self._ack_value()))
                self.snd_nxt += n
                continue
            if self.fin_queued and not self.fin_sent:
                self._piggyback()
                self.out(self._seg(self.snd_nxt, 0, self._ack_value(), fin=True))
                self.fin_sent = True
            break

    # ---------------------------------------------------------------- receiving
    def recv(self, seg: Seg) -> None:
        if seg.syn and seg.ack is None:                  # passive open: SYN -> SYN/ACK
            self._setup(seg.src)
            self.out(self._seg(0, 0, 0, syn=True, opt=self._syn_opts(seg.src)))
            return
        if seg.syn:                                      # SYN/ACK at the client -> ACK, established
            self._setup(seg.src)
            self.established = True
            self.out(self._seg(0, 0, 0))
            self.on_established()
            self._try_send()
            return
        if not self.established:                         # final handshake ACK at the server
            self.established = True
            self.on_established()
        if seg.ack is not None:
            self._on_ack(seg.ack)
        if seg.length > 0 or seg.fin:
            self._on_data(seg)

    def _on_ack(self, ack: int) -> None:
        data_ack = min(ack, self.snd_nxt)
        if data_ack > self.snd_una:
            acked = data_ack - self.snd_una
            self.snd_una = data_ack
            if self.cwnd < self.ssthresh:                # slow start
                lim = self.p.get("ss_increase_limit_segments")
                inc = acked if lim is None else min(acked, int(lim) * self.mss)
                self.cwnd = min(self.cwnd + inc, self.ssthresh) if self.p["ss_cap_at_ssthresh"] else self.cwnd + inc
            else:                                        # congestion avoidance (RFC 5681 eq. 3)
                self.cwnd += self.mss * self.mss / self.cwnd
            self.on_acked(self.snd_una)
        if self.fin_sent and ack >= self.snd_nxt + 1:
            self.fin_acked = True
        self._try_send()

    def _on_data(self, seg: Seg) -> None:
        if seg.length > 0:
            self.rcv_nxt += seg.length
            self.on_data(self.rcv_nxt)
        if seg.fin:
            self.rcv_nxt += 1
            self.fin_received = True
            self._send_ack()                              # FIN is acknowledged at once
            self.on_fin()
            return
        pol = self.p["ack_policy"]
        if pol == "linux":                               # tcp_input.c __tcp_ack_snd_check
            if self.quick > 0:
                self.quick -= 1
                self._send_ack()
            elif self.rcv_nxt - self.rcv_wup > self.mss:
                self._send_ack()
            elif not self._delack_pending:
                self._delack_pending = True
                gen = self._delack_gen
                self.loop.after(float(self.p["delack_min_s"]), self._delack_fire, gen)
        elif pol == "lwip":                              # lwIP tcp_ack(): every 2nd segment, else fast timer
            if self._delack_pending:
                self._send_ack()
            else:
                self._delack_pending = True
                gen = self._delack_gen
                per = float(self.p["delack_timer_period_s"])
                k = math.floor((self.loop.t - self._lwip_phase) / per) + 1
                self.loop.at(self._lwip_phase + k * per, self._delack_fire, gen)
        else:
            raise ValueError(f"unknown ack_policy {pol}")

    def _delack_fire(self, gen: int) -> None:
        if self._delack_pending and gen == self._delack_gen:
            self._send_ack()
