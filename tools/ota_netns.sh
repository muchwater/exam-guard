#!/usr/bin/env bash
# Over-the-air test path on ONE team device (the board): its Wi-Fi uploads to a server that lives in a
# network namespace on its OWN wired port, so every byte crosses the air (wlan -> AP -> wire -> board).
#
#   bash tools/ota_netns.sh up <eth_iface> [<blob_bytes> ...]   # prints the server's IPv6 link-local address
#   bash tools/ota_netns.sh down
#
# - netns `egsrv` holds a macvlan on <eth_iface>; IPv6 link-local only (no DHCP lease, no address clash).
#   Clients use "<addr>%<wlan_iface>", which pins the outgoing interface to Wi-Fi without touching routes.
# - :8080 capture.upload_server (POST sink), :8081 static files /tmp/eg_dl/<bytes>.bin (download sources).
# - needs passwordless sudo for ip/kill (the Rubik Pi image has it); `down` removes everything it created.
set -euo pipefail
cd "$(dirname "$0")/.."
NS=egsrv
case "${1:-}" in
  up)
    ETH=$2; shift 2
    sudo -n ip netns add $NS
    sudo -n ip link add mv0 link "$ETH" type macvlan mode bridge
    sudo -n ip link set mv0 netns $NS
    sudo -n ip netns exec $NS ip link set lo up
    sudo -n ip netns exec $NS ip link set mv0 up
    for _ in $(seq 20); do                                  # wait for IPv6 DAD on the link-local address
      sudo -n ip netns exec $NS ip -6 -o addr show mv0 scope link | grep -q -v tentative && break
      sleep 0.5
    done
    mkdir -p /tmp/eg_dl
    for n in "$@"; do [ -f /tmp/eg_dl/$n.bin ] || head -c "$n" /dev/urandom > /tmp/eg_dl/$n.bin; done
    U=$(id -un)
    sudo -n ip netns exec $NS sudo -u "$U" nohup .venv/bin/python -m capture.upload_server --host :: --port 8080 \
        --log /tmp/eg_dl/server_log.jsonl > /tmp/eg_upload_server.log 2>&1 &
    sudo -n ip netns exec $NS sudo -u "$U" nohup python3 -m http.server 8081 --bind :: --directory /tmp/eg_dl \
        > /tmp/eg_http_server.log 2>&1 &
    sleep 1
    sudo -n ip netns exec $NS ip -6 -o addr show mv0 scope link | awk '{print $4}' | cut -d/ -f1
    ;;
  down)
    P=$(sudo -n ip netns pids $NS 2>/dev/null | tr '\n' ' ')
    [ -n "$P" ] && sudo -n kill $P || true
    sleep 1
    sudo -n ip netns del $NS 2>/dev/null || true
    rm -rf /tmp/eg_dl
    ;;
  *) sed -n '2,11p' "$0"; exit 1 ;;
esac
