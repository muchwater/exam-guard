#!/usr/bin/env bash
# Labelled calib session captured by the board's own RTL8192EU (vendor driver 8192eu loaded): the board uploads over its
# Wi-Fi (tools/ota_netns.sh path) while its dongle sniffs, so sniffer and uploader share one clock (offset 0).
#   bash tools/dongle_session.sh <name> <seed>      # -> data/raw/real/<name>/ (capture.pcap + ota_session logs), ~16 min
set -u
cd ~/exam-guard
O=data/raw/real/${1:-dongle_calib1}; SEED=${2:-1}; mkdir -p $O
MAC=$(grep -oE '"[0-9a-f:]{17}"' configs/site_rubik.yaml | head -1 | tr -d '"')
rm -f /tmp/eg_dongle_stop $O/DONE
sudo -n bash tools/linux_monitor_capture.sh wlxa047d72040aa 2 $PWD/$O/capture.pcap /tmp/eg_dongle_stop 1500 $MAC > $O/capture.log 2>&1 &
sleep 5
SRV=$(bash tools/ota_netns.sh up enxf074e4884cd9 $(.venv/bin/python -m tools.ota_session --print-blob-sizes) | tail -1)
.venv/bin/python -m tools.ota_session --server "$SRV" --plan calib --seed $SEED --out $O > $O/session.log 2>&1
bash tools/ota_netns.sh down
touch /tmp/eg_dongle_stop
sleep 6
echo DONE > $O/DONE
