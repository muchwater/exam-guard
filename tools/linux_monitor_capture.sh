#!/usr/bin/env bash
# Own-device monitor-mode capture on a Linux host whose Wi-Fi is otherwise unused (e.g. an idle lab machine).
#
#   sudo bash tools/linux_monitor_capture.sh <iface> <channel> <out.pcap> <stop_file> <max_s> <team_mac> [<team_mac> ...]
#
# - Refuses if <iface> holds an IPv4 address or the default route, so the host's own link is never taken down.
# - Keeps only frames whose RA or TA is a team MAC (kernel BPF `wlan addr1/addr2`, same rule as
#   capture/macfilter.py); frames of other devices never reach user space or disk.
# - Runs until <stop_file> exists (it can be created without root) or <max_s> seconds pass, then restores
#   managed mode and NetworkManager control, and hands the pcap to the invoking user.
# - Writes <out.pcap>.running (start epoch, this host's clock) while capturing and <out.pcap>.done at the end.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
[ $# -ge 6 ] || { sed -n '2,12p' "$0" >&2; exit 1; }
IFACE=$1; CH=$2; OUT=$3; STOP=$4; MAX=$5; shift 5
SNAPLEN=262144   # source: tcpdump(1) man page -- default snapshot length 262144 bytes (same as configs/default.yaml)

for m in "$@"; do
  [[ $m =~ ^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$ ]] || { echo "invalid MAC: $m" >&2; exit 1; }
done
FILTER=$(printf 'wlan addr1 %s or wlan addr2 %s or ' $(for m in "$@"; do echo "$m $m"; done) | sed 's/ or $//')

if ip -4 -o addr show dev "$IFACE" | grep -q inet; then echo "refusing: $IFACE has an IPv4 address" >&2; exit 1; fi
if ip route show default | grep -q " dev $IFACE "; then echo "refusing: $IFACE carries the default route" >&2; exit 1; fi

restore() {
  [ -n "${TCPDUMP_PID:-}" ] && kill "$TCPDUMP_PID" 2>/dev/null && wait "$TCPDUMP_PID" 2>/dev/null || true
  ip link set "$IFACE" down || true
  iw dev "$IFACE" set type managed || true
  ip link set "$IFACE" up || true
  command -v nmcli >/dev/null && nmcli device set "$IFACE" managed yes || true
  rm -f "$OUT.running"
  date +%s.%N > "$OUT.done"
  if [ -n "${SUDO_USER:-}" ]; then chown "$SUDO_USER" "$OUT" "$OUT.done" 2>/dev/null || true; fi
  iw dev "$IFACE" info | grep -E "type|channel" || true
}
trap restore EXIT

rm -f "$STOP" "$OUT.done"
command -v nmcli >/dev/null && nmcli device set "$IFACE" managed no || true
ip link set "$IFACE" down
iw dev "$IFACE" set type monitor
ip link set "$IFACE" up
iw dev "$IFACE" set channel "$CH" HT20
iw dev "$IFACE" info | grep -E "type|channel"

tcpdump -i "$IFACE" -y IEEE802_11_RADIO -U -s "$SNAPLEN" --time-stamp-precision=micro -w "$OUT" "$FILTER" &
TCPDUMP_PID=$!
date +%s.%N > "$OUT.running"
if [ -n "${SUDO_USER:-}" ]; then chown "$SUDO_USER" "$OUT.running" 2>/dev/null || true; fi
echo "capturing on $IFACE ch $CH -> $OUT (stop: touch $STOP, max ${MAX}s)"
for _ in $(seq "$MAX"); do
  [ -e "$STOP" ] && break
  kill -0 "$TCPDUMP_PID" 2>/dev/null || { echo "tcpdump exited" >&2; break; }
  sleep 1
done
echo "stopping"
