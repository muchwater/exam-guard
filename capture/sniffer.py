"""Live 802.11 sniffer: tcpdump (kernel BPF own-MAC filter) -> pcap file + PacketRecord stream.

tcpdump writes the filtered pcap stream to stdout (`-U -w -`); this module tees the
bytes to disk (so every live run can be replayed) and decodes them on the fly.
Frames of non-team devices are dropped by the kernel BPF filter before user space.

    sudo python -m capture.sniffer --out data/raw/run1.pcap --duration 60 [--config configs/site_x.yaml]
"""
from __future__ import annotations

import argparse
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import BinaryIO, Callable, Optional

from capture.macfilter import MacAllowlist
from capture.pcap_io import iter_packets
from common.config import load_config, require
from common.events import PacketRecord


class _Tee:
    """File-like reader that copies everything it reads to a second file."""

    def __init__(self, src: BinaryIO, dst: BinaryIO):
        self.src, self.dst = src, dst

    def read(self, n: int) -> bytes:
        b = self.src.read(n)
        if b:
            self.dst.write(b)
            self.dst.flush()
        return b


def tcpdump_cmd(iface: str, allow: MacAllowlist, snaplen: int) -> list[str]:
    cmd = ["tcpdump", "-i", iface, "-y", "IEEE802_11_RADIO", "-U", "-w", "-", "-s", str(snaplen),
           "--time-stamp-precision=micro", allow.bpf()]
    if os.geteuid() != 0:
        cmd = ["sudo", "-n"] + cmd
    return cmd


class LiveSniffer:
    """Runs tcpdump in a thread; decoded frames go to `self.queue` (or `on_packet`)."""

    def __init__(self, iface: str, allow: MacAllowlist, out_path: str | Path, snaplen: int,
                 on_packet: Optional[Callable[[PacketRecord], None]] = None):
        self.iface, self.allow, self.out_path, self.snaplen = iface, allow, Path(out_path), snaplen
        self.queue: "queue.Queue[PacketRecord]" = queue.Queue()
        self.on_packet = on_packet or self.queue.put
        self.proc: Optional[subprocess.Popen] = None
        self.thread: Optional[threading.Thread] = None
        self.n = 0
        self.error: Optional[str] = None

    def start(self) -> "LiveSniffer":
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(tcpdump_cmd(self.iface, self.allow, self.snaplen),
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return self

    def _loop(self) -> None:
        assert self.proc and self.proc.stdout
        with open(self.out_path, "wb") as f:
            for pkt in iter_packets(_Tee(self.proc.stdout, f), self.allow):  # 2nd allowlist check
                self.n += 1
                self.on_packet(pkt)
        rc = self.proc.wait()
        if rc not in (0, -15):
            self.error = (self.proc.stderr.read() or b"").decode(errors="replace").strip()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        if self.thread:
            self.thread.join(timeout=5)


def from_config(out_path: str | Path, cfg: dict | None = None, **kw) -> LiveSniffer:
    cfg = cfg or load_config()
    iface, snaplen = require(cfg, "capture.iface", "capture.snaplen")
    return LiveSniffer(iface, MacAllowlist.from_config(cfg), out_path, snaplen, **kw)


def main() -> None:
    ap = argparse.ArgumentParser(description="Capture own-device 802.11 frames to pcap.")
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration", type=float, default=None, help="seconds; default: until Ctrl-C")
    ap.add_argument("--config", action="append", default=[], help="override yaml (repeatable)")
    a = ap.parse_args()
    s = from_config(a.out, load_config(*a.config)).start()
    t0 = time.time()
    try:
        while a.duration is None or time.time() - t0 < a.duration:
            time.sleep(1.0)
            print(f"\r{s.n} frames", end="", flush=True)
            if s.proc and s.proc.poll() is not None:
                break
    except KeyboardInterrupt:
        pass
    s.stop()
    print(f"\nsaved {s.n} frames to {a.out}" + (f"\ntcpdump error: {s.error}" if s.error else ""))


if __name__ == "__main__":
    main()
