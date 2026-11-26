"""Put a USB Wi-Fi adapter into monitor mode (run on the Rubik Pi 3).

monitor mode: the NIC passes up every 802.11 frame it hears on the tuned channel
(not only frames addressed to it), with a radiotap header per frame.

    python -m capture.monitor_setup --check            # report support only, change nothing
    python -m capture.monitor_setup --iface wlan1 --channel 6
    python -m capture.monitor_setup --iface wlan1 --restore

Safety: refuses to touch an interface that holds an IPv4 address or the default
route, so the board's own link (e.g. the SSH session over the built-in AP6256)
is never taken down.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

from common.config import load_config, require


def _run(cmd: list[str], check: bool = True, sudo: bool = False) -> str:
    if sudo and os.geteuid() != 0:
        cmd = ["sudo", "-n"] + cmd
    p = subprocess.run(cmd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed ({p.returncode}): {p.stderr.strip()}")
    return p.stdout


def list_wifi_ifaces() -> dict[str, str]:
    """iface -> phy name, from `iw dev`."""
    out = _run(["iw", "dev"])
    res, phy = {}, None
    for line in out.splitlines():
        m = re.match(r"^phy#(\d+)", line.strip())
        if m:
            phy = f"phy{m.group(1)}"
        m = re.match(r"^Interface\s+(\S+)", line.strip())
        if m and phy:
            res[m.group(1)] = phy
    return res


def supports_monitor(phy: str) -> bool:
    out = _run(["iw", "phy", phy, "info"])
    block = re.search(r"Supported interface modes:\n((?:\s+\* .*\n)+)", out)
    return bool(block and re.search(r"\*\s+monitor\b", block.group(1)))


def iface_in_use(iface: str) -> str | None:
    """Return a reason string if the interface carries the host's own connectivity."""
    if re.search(r"\binet\s", _run(["ip", "-4", "addr", "show", "dev", iface], check=False)):
        return "it has an IPv4 address"
    if re.search(rf"\bdev {re.escape(iface)}\b", _run(["ip", "route", "show", "default"], check=False)):
        return "it carries the default route"
    return None


def iface_info(iface: str) -> str:
    return _run(["iw", "dev", iface, "info"], check=False)


def set_monitor(iface: str, channel: int) -> None:
    reason = iface_in_use(iface)
    if reason:
        raise SystemExit(f"refusing to reconfigure {iface}: {reason}")
    if shutil.which("nmcli"):
        _run(["nmcli", "device", "set", iface, "managed", "no"], check=False, sudo=True)
    _run(["ip", "link", "set", iface, "down"], sudo=True)
    _run(["iw", "dev", iface, "set", "type", "monitor"], sudo=True)
    _run(["ip", "link", "set", iface, "up"], sudo=True)
    _run(["iw", "dev", iface, "set", "channel", str(channel)], sudo=True)
    info = iface_info(iface)
    if "type monitor" not in info:
        raise RuntimeError(f"{iface} did not enter monitor mode:\n{info}")
    print(info)


def restore_managed(iface: str) -> None:
    _run(["ip", "link", "set", iface, "down"], sudo=True)
    _run(["iw", "dev", iface, "set", "type", "managed"], sudo=True)
    _run(["ip", "link", "set", iface, "up"], sudo=True)
    if shutil.which("nmcli"):
        _run(["nmcli", "device", "set", iface, "managed", "yes"], check=False, sudo=True)
    print(iface_info(iface))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iface", help="default: config capture.iface")
    ap.add_argument("--channel", type=int, help="default: config capture.channel")
    ap.add_argument("--check", action="store_true", help="only report monitor-mode support per interface")
    ap.add_argument("--restore", action="store_true", help="switch the interface back to managed mode")
    ap.add_argument("--config", action="append", default=[], help="override yaml (repeatable)")
    a = ap.parse_args()

    ifaces = list_wifi_ifaces()
    if a.check:
        for iface, phy in ifaces.items():
            used = iface_in_use(iface)
            print(f"{iface} ({phy}): monitor={'yes' if supports_monitor(phy) else 'NO'}"
                  + (f"  [in use: {used}]" if used else ""))
        return
    cfg = load_config(*a.config)
    iface = a.iface or require(cfg, "capture.iface")
    if iface not in ifaces:
        sys.exit(f"{iface} not found; wifi interfaces: {', '.join(ifaces) or 'none'}")
    if a.restore:
        restore_managed(iface)
        return
    if not supports_monitor(ifaces[iface]):
        sys.exit(f"{iface} ({ifaces[iface]}) does not list monitor in its supported interface modes")
    set_monitor(iface, a.channel or require(cfg, "capture.channel"))


if __name__ == "__main__":
    main()
