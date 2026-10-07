#!/usr/bin/env bash
# 宿主机的一块 TAP 网卡顶替联合仿真里的 B0（10.0.1.10，MAC 02:00:00:00:00:12）：真的 TCP/IP，一个 HTTP 服务，
# 并替 10.0.0.0/24 做 NAT 出网。过路由器的帧不能超过 128 字节，所以网卡的 MTU 是 114，转发的 TCP 握手按它压 MSS。
# 要 sudo：托管机免密；本机给 SUDO_ASKPASS 就走 sudo -A。用法：tap-host.sh up <网卡> <HTTP 根目录> | down <网卡>
set -euo pipefail
asroot() {
  if [ -n "${SUDO_ASKPASS:-}" ]; then sudo -A "$@"; else sudo "$@"; fi
}
T=$2
nat=(-s 10.0.0.0/24 ! -o "$T" -j MASQUERADE)
mss=(-o "$T" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu)
case $1 in
up)
  asroot ip tuntap add dev "$T" mode tap user "${SUDO_USER:-$(id -un)}"
  # 别让宿主机往这根线上发 IPv6 的邻居发现与组播报告
  asroot sysctl -qw "net.ipv6.conf.$T.disable_ipv6=1"
  asroot ip link set "$T" address 02:00:00:00:00:12 mtu 114 up
  asroot ip addr add 10.0.1.10/24 dev "$T"
  asroot ip route add 10.0.0.0/24 via 10.0.1.1 dev "$T"
  asroot sysctl -qw net.ipv4.ip_forward=1
  asroot iptables -t nat -A POSTROUTING "${nat[@]}"
  # 托管机上 Docker 把 FORWARD 的默认策略设成了丢
  asroot iptables -I FORWARD -i "$T" -j ACCEPT
  asroot iptables -I FORWARD -o "$T" -j ACCEPT
  asroot iptables -t mangle -A FORWARD "${mss[@]}"
  (cd "$3" && exec python3 -m http.server 8080 --bind 10.0.1.10) > "$3.http.log" 2>&1 &
  echo $! > "/tmp/$T.http"
  for _ in $(seq 30); do
    ss -ltn | grep -q '10.0.1.10:8080 ' && exit 0
    python3 -c "import time; time.sleep(1)"
  done
  echo "HTTP 服务三十秒没起来"
  cat "$3.http.log"
  exit 1
  ;;
down)
  if [ -f "/tmp/$T.http" ]; then
    kill "$(cat "/tmp/$T.http")" 2> /dev/null || true
    rm -f "/tmp/$T.http"
  fi
  asroot iptables -t nat -D POSTROUTING "${nat[@]}" 2> /dev/null || true
  asroot iptables -D FORWARD -i "$T" -j ACCEPT 2> /dev/null || true
  asroot iptables -D FORWARD -o "$T" -j ACCEPT 2> /dev/null || true
  asroot iptables -t mangle -D FORWARD "${mss[@]}" 2> /dev/null || true
  asroot ip link del "$T" 2> /dev/null || true
  ;;
*)
  echo "用法：tap-host.sh up <网卡> <HTTP 根目录> | down <网卡>"
  exit 2
  ;;
esac
