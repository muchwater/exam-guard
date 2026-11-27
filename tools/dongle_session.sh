#!/usr/bin/env bash
# Labelled calib session captured by the board's own RTL8192EU (vendor driver 8192eu loaded): the board uploads over its
# Wi-Fi (tools/ota_netns.sh path) while its dongle sniffs, so sniffer and uploader share one clock (offset 0).
#   bash tools/dongle_session.sh <name> <seed> <eth_if> [site config]   # -> data/raw/real/<name>/, ~15 min
# Monitor interface, channel and the team MAC come from the site config (capture.iface, capture.channel,
# capture.allowed_macs[0]; default configs/site_rubik.yaml, not committed). <eth_if> is the board's wired port that
# hosts the upload server netns (`ip -br link`).
set -eu
cd ~/exam-guard
O=data/raw/real/$1; SEED=$2; ETH=$3; SITE=${4:-configs/site_rubik.yaml}
read -r IFACE CHAN MAC < <(.venv/bin/python -c "
import sys; from common.config import load_config
c = load_config(sys.argv[1])['capture']; print(c['iface'], c['channel'], c['allowed_macs'][0])" "$SITE")
mkdir -p "$O"
rm -f /tmp/eg_dongle_stop "$O/DONE"
sudo -n bash tools/linux_monitor_capture.sh "$IFACE" "$CHAN" "$PWD/$O/capture.pcap" /tmp/eg_dongle_stop 1500 "$MAC" \
    > "$O/capture.log" 2>&1 &
sleep 5        # capture script: monitor mode + tcpdump start before the first marker
SRV=$(bash tools/ota_netns.sh up "$ETH" $(.venv/bin/python -m tools.ota_session --print-blob-sizes) | tail -1)
.venv/bin/python -m tools.ota_session --server "$SRV" --plan calib --seed "$SEED" --out "$O" > "$O/session.log" 2>&1
bash tools/ota_netns.sh down
touch /tmp/eg_dongle_stop
sleep 6        # capture script polls the stop file, then restores managed mode
echo DONE > "$O/DONE"
