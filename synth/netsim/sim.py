"""Discrete-event core: event loop, 802.11 EDCA medium with A-MPDU / BlockAck, ideal sniffer.

Model (see docs/netsim_model.md for the full assumption list):
  * one shared 2.4 GHz channel; stations = the device under test, the AP, optionally a wireless server
  * EDCA best-effort access: AIFS + uniform backoff in [0, CW] slots, counters freeze while another
    station transmits; equal completion slot -> collision (no frame decoded, CW doubles, retry bit set)
  * no channel errors other than collisions; MCS fixed per run, same in both directions
  * frames queued for the same receiver are sent as one A-MPDU (<= BA window, <= 65535 B,
    <= PPDU max time) answered by a BlockAck; a single MPDU is answered by an ACK
  * sniffer = ideal monitor interface: sees every successfully exchanged frame, timestamp = the time
    the frame's last bit is on air; only frames whose addr1/addr2 is in the allowlist are kept
"""
from __future__ import annotations

import heapq
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from synth.netsim.phy import Phy

EPS = 1e-12


class EventLoop:
    def __init__(self):
        self.t = 0.0
        self._h: list = []
        self._n = 0

    def at(self, t: float, fn: Callable, *args) -> None:
        heapq.heappush(self._h, (t, self._n, fn, args))
        self._n += 1

    def after(self, dt: float, fn: Callable, *args) -> None:
        self.at(self.t + dt, fn, *args)

    def run(self, t_end: float = math.inf) -> None:
        while self._h and self._h[0][0] <= t_end:
            t, _, fn, args = heapq.heappop(self._h)
            self.t = t
            fn(*args)


@dataclass
class Mpdu:
    dst: "Station"
    frame_bytes: int                 # 802.11 frame length without FCS (what a capture reports)
    deliver: Callable[[], None]      # called at the receiver when the PPDU ends
    retry: bool = False
    seq: int = 0


@dataclass
class Station:
    name: str
    mac: str
    is_ap: bool
    aifsn: int
    cwmin: int
    cwmax: int
    ba_window: int
    queue: deque = field(default_factory=deque)
    backoff: Optional[int] = None
    cw: int = 0
    ready: float = 0.0
    start: float = 0.0
    done: float = 0.0
    seq: int = 0
    tx_ppdus: int = 0
    collisions: int = 0

    def __post_init__(self):
        self.cw = self.cwmin


@dataclass
class CapturedFrame:
    ts: float
    ftype: int
    subtype: int
    addr1: str
    addr2: Optional[str]
    addr3: Optional[str]
    to_ds: bool
    from_ds: bool
    retry: bool
    protected: bool
    frame_len: int
    seq: int = 0


class Sniffer:
    def __init__(self, allow: set[str]):
        self.allow = {a.lower() for a in allow}
        self.frames: list[CapturedFrame] = []

    def offer(self, f: CapturedFrame) -> None:
        if f.addr1 in self.allow or (f.addr2 is not None and f.addr2 in self.allow):
            self.frames.append(f)


class Medium:
    """Single shared channel with EDCA contention."""

    def __init__(self, loop: EventLoop, phy: Phy, mcs: int, rng: np.random.Generator, sniffer: Sniffer):
        self.loop, self.phy, self.mcs, self.rng, self.sniffer = loop, phy, mcs, rng, sniffer
        self.stations: list[Station] = []
        self.busy_until = 0.0
        self.idle_since = 0.0
        self._token = 0
        self.airtime_us = 0.0

    def add(self, st: Station) -> Station:
        self.stations.append(st)
        return st

    def bssid(self) -> str:
        return next(s.mac for s in self.stations if s.is_ap)

    # --- queueing ------------------------------------------------------------------------------
    def send(self, src: Station, dst: Station, frame_bytes: int, deliver: Callable[[], None]) -> None:
        m = Mpdu(dst=dst, frame_bytes=frame_bytes, deliver=deliver, seq=src.seq)
        src.seq = (src.seq + 1) % 4096
        if not src.queue:
            src.ready = self.loop.t
        src.queue.append(m)
        self._schedule()

    def _schedule(self) -> None:
        if self.loop.t < self.busy_until - EPS:
            return                       # the end-of-exchange event reschedules
        cont = [s for s in self.stations if s.queue]
        if not cont:
            return
        slot = self.phy.slot_us * 1e-6
        best = math.inf
        for s in cont:
            if s.backoff is None:
                s.backoff = int(self.rng.integers(0, s.cw + 1))
            s.start = max(self.idle_since + self.phy.aifs_us(s.aifsn) * 1e-6, s.ready)
            s.done = s.start + s.backoff * slot
            best = min(best, s.done)
        self._token += 1
        self.loop.at(best, self._attempt, self._token)

    # --- one channel access ----------------------------------------------------------------------
    def _take(self, s: Station) -> list[Mpdu]:
        first = s.queue[0]
        batch = [first]
        if s.ba_window > 1:
            for m in list(s.queue)[1:]:
                if m.dst is not first.dst or len(batch) >= s.ba_window:
                    break
                cand = [x.frame_bytes + self.phy.fcs_bytes for x in batch + [m]]
                if not self.phy.fits_ampdu(cand, self.mcs):
                    break
                batch.append(m)
        return batch

    def _ppdu_us(self, batch: list[Mpdu]) -> tuple[float, list[float]]:
        sizes = [m.frame_bytes + self.phy.fcs_bytes for m in batch]
        if len(batch) == 1:
            d = self.phy.ht_ppdu_us(sizes[0], self.mcs)
            return d, [d]
        psdu, ends = self.phy.ampdu_layout(sizes)
        return self.phy.ht_ppdu_us(psdu, self.mcs), [self.phy.subframe_end_us(e, self.mcs) for e in ends]

    def _attempt(self, token: int) -> None:
        if token != self._token:
            return
        t = self.loop.t
        slot = self.phy.slot_us * 1e-6
        cont = [s for s in self.stations if s.queue]
        winners = [s for s in cont if s.done <= t + 1e-9]
        for s in cont:
            if s not in winners and s.start < t:
                s.backoff = max(0, s.backoff - int((t - s.start) / slot + 1e-6))
        sifs = self.phy.sifs_us * 1e-6
        if len(winners) == 1:
            w = winners[0]
            batch = self._take(w)
            d_us, ends_us = self._ppdu_us(batch)
            single = len(batch) == 1
            resp_us = self.phy.ack_us(self.mcs) if single else self.phy.blockack_us(self.mcs)
            t_ppdu_end = t + d_us * 1e-6
            dst = batch[0].dst
            for m, e in zip(batch, ends_us):
                self.sniffer.offer(self._data_frame(t + e * 1e-6, w, m))
            t_resp_end = t_ppdu_end + sifs + resp_us * 1e-6
            if single:   # ACK: RA only
                self.sniffer.offer(CapturedFrame(t_resp_end, 1, 13, w.mac, None, None, False, False, False, False,
                                                 self.phy.ack_bytes - self.phy.fcs_bytes))
            else:        # BlockAck: RA = originator, TA = recipient
                self.sniffer.offer(CapturedFrame(t_resp_end, 1, 9, w.mac, dst.mac, None, False, False, False, False,
                                                 self.phy.blockack_bytes - self.phy.fcs_bytes))
            for _ in batch:
                w.queue.popleft()
            for m in batch:
                self.loop.at(t_ppdu_end, m.deliver)
            w.cw, w.backoff = w.cwmin, None
            w.tx_ppdus += 1
            if w.queue:
                w.ready = t_resp_end
            busy = t_resp_end
            self.airtime_us += (busy - t) * 1e6
        else:   # collision: nobody decodes anything; each winner retries with a doubled CW
            longest = 0.0
            for w in winners:
                batch = self._take(w)
                longest = max(longest, self._ppdu_us(batch)[0])
                for m in batch:
                    m.retry = True
                w.cw = min(2 * w.cw + 1, w.cwmax)
                w.backoff = None
                w.collisions += 1
            # receiver stays silent; transmitters wait SIFS + the expected response time (ACK timeout)
            busy = t + longest * 1e-6 + sifs + self.phy.blockack_us(self.mcs) * 1e-6
            self.airtime_us += (busy - t) * 1e6
        self.busy_until = busy
        self.idle_since = busy
        self.loop.at(busy, self._schedule)

    def _data_frame(self, ts: float, src: Station, m: Mpdu) -> CapturedFrame:
        to_ds = not src.is_ap
        bssid = self.bssid()
        return CapturedFrame(ts, 2, 8, m.dst.mac, src.mac, bssid, to_ds, not to_ds, m.retry, True,
                             m.frame_bytes, m.seq)
