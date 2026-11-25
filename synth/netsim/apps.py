"""Application behaviour on top of the TCP endpoints.

http_upload_lan   capture/scenario_upload.py -> capture/upload_server.py (HTTP/1.1 POST, server answers
                  HTTP/1.0 and closes). Byte counts measured from the real code (tools.measure_app_bytes).
https_upload_wan  image sent to a public HTTPS API: TLS 1.3 handshake flights measured against real
                  hosts, body = base64(JPEG) (RFC 4648) in TLS records (RFC 8446). The API's answer is
                  not modelled (model latency unknown); the upload ends when the last byte is ACKed.
page_load         one HTTPS GET whose response carries a whole page weight (HTTP Archive Web Almanac
                  2024); other sub-requests are not modelled (lower bound on uplink request bytes).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

from synth.netsim.net import Topology


@dataclass
class AppBytes:
    post_header_base: int
    get_header: int
    response: int
    tls_record_max: int
    tls_record_overhead: int

    @classmethod
    def from_measured(cls, measured: dict, tls: dict) -> "AppBytes":
        return cls(int(measured["http_post_header_bytes_base"]), int(measured["http_get_header_bytes"]),
                   int(measured["upload_server_response"]["response_bytes"]), int(tls["record_max_plaintext"]),
                   int(tls["record_overhead_bytes"]))

    def post_header(self, body: int) -> int:
        return self.post_header_base + len(str(body))

    def tls_wrap(self, plaintext: int) -> int:
        return plaintext + math.ceil(plaintext / self.tls_record_max) * self.tls_record_overhead


def base64_len(n: int) -> int:
    return 4 * math.ceil(n / 3)


@dataclass
class Host:
    name: str
    rtt_s: float
    client_hello: int
    server_flight: int
    client_finished: int


def http_upload_lan(topo: Topology, server_kind: str, t0: float, body: int, ab: AppBytes,
                    done: Callable[[dict], None]) -> None:
    def start():
        c, s = topo.flow(server_kind)
        req = ab.post_header(body) + body
        lab = {"t_start": topo.loop.t, "bytes": body, "request_bytes": req, "app": "http_upload_lan",
               "server": server_kind}
        c.on_established = lambda: c.write(req)

        def srv_data(total):
            if total >= req and not s.fin_queued:
                s.write(ab.response)
                s.close()                                 # http.server default protocol HTTP/1.0
        s.on_data = srv_data

        def cli_data(total):
            if total >= ab.response and "t_end" not in lab:
                lab["t_end"] = topo.loop.t                # uploader stamps t_end after reading the status
                done(lab)
                c.close()
        c.on_data = cli_data
        c.connect(s)
    topo.loop.at(t0, start)


def https_upload_wan(topo: Topology, host: Host, t0: float, body: int, ab: AppBytes,
                     done: Callable[[dict], None]) -> None:
    def start():
        c, s = topo.flow("wan", host.rtt_s)
        b64 = base64_len(body)
        req_plain = ab.post_header(b64) + b64
        tail = host.client_finished + ab.tls_wrap(req_plain)
        lab = {"t_start": topo.loop.t, "bytes": body, "request_bytes": host.client_hello + tail,
               "app": "https_upload_wan", "server": f"wan:{host.name}"}
        state = {"sf_sent": False, "tail_written": False, "end": None}
        c.on_established = lambda: c.write(host.client_hello)

        def srv_data(total):
            if total >= host.client_hello and not state["sf_sent"]:
                state["sf_sent"] = True
                s.write(host.server_flight)
        s.on_data = srv_data

        def cli_data(total):
            if total >= host.server_flight and not state["tail_written"]:
                state["tail_written"] = True
                state["end"] = c.app_end + tail
                c.write(tail)
        c.on_data = cli_data

        def acked(una):
            if state["end"] is not None and una >= state["end"] and "t_end" not in lab:
                lab["t_end"] = topo.loop.t
                done(lab)
        c.on_acked = acked
        c.connect(s)
    topo.loop.at(t0, start)


def page_load(topo: Topology, host: Host, t0: float, page_bytes: int, ab: AppBytes,
              done: Callable[[dict], None], close: bool = True) -> None:
    def start():
        c, s = topo.flow("wan", host.rtt_s)
        resp = ab.tls_wrap(page_bytes)
        get = host.client_finished + ab.tls_wrap(ab.get_header)
        lab = {"t_start": topo.loop.t, "page_bytes": page_bytes, "app": "page_load", "server": f"wan:{host.name}"}
        st = {"sf": False, "get": False, "resp": False}
        c.on_established = lambda: c.write(host.client_hello)

        def srv_data(total):
            if total >= host.client_hello and not st["sf"]:
                st["sf"] = True
                s.write(host.server_flight)
            if total >= host.client_hello + get and not st["resp"]:
                st["resp"] = True
                s.write(resp)
        s.on_data = srv_data

        def cli_data(total):
            if total >= host.server_flight and not st["get"]:
                st["get"] = True
                c.write(get)
            if total >= host.server_flight + resp and "t_end" not in lab:
                lab["t_end"] = topo.loop.t
                if close:
                    c.close()
                done(lab)
        c.on_data = cli_data
        s.on_fin = s.close
        c.connect(s)
    topo.loop.at(t0, start)


def hosts_from_measured(rtt: dict, app: dict, names: Optional[list[str]] = None) -> list[Host]:
    """Pair each probed host's median RTT with its measured TLS flight sizes."""
    tls = {t["host"]: t for t in app.get("tls", []) if "flights" in t}
    out = []
    for h in rtt["hosts"]:
        if names and h["host"] not in names:
            continue
        t = tls.get(h["host"])
        if t is None:
            continue
        fl = t["flights"]
        if [d for d, _ in fl[:3]] != ["c2s", "s2c", "c2s"]:
            raise ValueError(f"unexpected TLS flight pattern for {h['host']}: {fl}")
        out.append(Host(h["host"], float(h["median_s"]), int(fl[0][1]), int(fl[1][1]), int(fl[2][1])))
    if not out:
        raise ValueError("no host has both an RTT and a TLS measurement")
    return out
