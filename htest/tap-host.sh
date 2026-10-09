#!/usr/bin/env bash
# 宿主机的一块 TAP 网卡顶替联合仿真里的 B0（10.0.1.10，MAC 02:00:00:00:00:12）：真的 TCP/IP，上面跑
# HTTP 服务（htest/www.py，FastAPI）与只答 lab.test 的 DNS（dnsmasq），两者都只听这块网卡的地址。
# 宿主机不替仿真里的网段转发出网，测试一个包也不出本机。过路由器的帧不能超过 128 字节，所以网卡的 MTU 是 114。
# 要 sudo：托管机免密；本机给 SUDO_ASKPASS 就走 sudo -A。用法：tap-host.sh up <网卡> <HTTP 根目录> | down <网卡>
set -euo pipefail
asroot() {
  if [ -n "${SUDO_ASKPASS:-}" ]; then sudo -A "$@"; else sudo "$@"; fi
}
H=$(cd "$(dirname "$0")" && pwd)
T=$2
case $1 in
up)
  asroot ip tuntap add dev "$T" mode tap user "${SUDO_USER:-$(id -un)}"
  # 别让宿主机往这根线上发 IPv6 的邻居发现与组播报告
  asroot sysctl -qw "net.ipv6.conf.$T.disable_ipv6=1"
  asroot ip link set "$T" address 02:00:00:00:00:12 mtu 114 up
  asroot ip addr add 10.0.1.10/24 dev "$T"
  asroot ip route add 10.0.0.0/24 via 10.0.1.1 dev "$T"
  asroot dnsmasq --conf-file=/dev/null --no-resolv --no-hosts --bind-interfaces --listen-address=10.0.1.10 \
    --address=/lab.test/10.0.1.10 --pid-file="/tmp/$T.dns"
  (WWW_ROOT=$3 exec python3 -m uvicorn www:app --app-dir "$H" --host 10.0.1.10 --port 8080) > "$3.http.log" 2>&1 &
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
  if [ -f "/tmp/$T.dns" ]; then
    asroot kill "$(cat "/tmp/$T.dns")" 2> /dev/null || true
    asroot rm -f "/tmp/$T.dns"
  fi
  asroot ip link del "$T" 2> /dev/null || true
  ;;
*)
  echo "用法：tap-host.sh up <网卡> <HTTP 根目录> | down <网卡>"
  exit 2
  ;;
esac
