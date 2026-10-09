#!/usr/bin/env bash
# 路由器在真流量下：两颗 to2610-switch 夹这一颗，A0、B0 换成宿主机上的两块 TAP 网卡，各放进一个网络命名空间，
# 两头是真的 TCP/IP。B0 上的 HTTP 服务是 htest/www.py（FastAPI），只听 TAP 的地址，一个包也不出本机。
# 宿主机来的帧按线速连发（NET_GAP 默认 0）。判：跨网段 ping 通、A0 经 HTTP 从 B0 取 64 KiB 核 md5、限时内取完；
# 记用时、两头的 TCP 重传与路由器的丢帧。TAP 先在这边建好，仿真打开之后再挪进命名空间：
# 打开的描述符跟着设备走。要 sudo（托管机免密；本机给 SUDO_ASKPASS 就走 sudo -A）。
# 用法：tcp.sh <输出目录>；两颗已经出过 .v 的，把输出目录分别给 CHIP_ASIC（路由器）与 SWITCH_ASIC
set -euo pipefail
cd "$(dirname "$0")/.."
O=$(realpath -m "$1")
rm -rf "$O"
mkdir -p "$O/www" "$O/bridge"
asroot() {
  if [ -n "${SUDO_ASKPASS:-}" ]; then sudo -A "$@"; else sudo "$@"; fi
}
in_a() { asroot ip netns exec xra "$@"; }
in_b() { asroot ip netns exec xrb "$@"; }
retrans() { "$@" cat /proc/net/snmp | awk '/^Tcp:/ { if (seen) { for (i = 1; i <= NF; i++) if (h[i] == "RetransSegs") print $i } else { split($0, h); seen = 1 } }'; }
down() {
  [ -f "$O/http.pid" ] && asroot kill "$(cat "$O/http.pid")" 2> /dev/null || true
  touch "$O/bridge/stop"
  asroot ip netns del xra 2> /dev/null || true
  asroot ip netns del xrb 2> /dev/null || true
  asroot ip link del xta 2> /dev/null || true
  asroot ip link del xtb 2> /dev/null || true
}
down
rm -f "$O/bridge/stop"
trap down EXIT

R=${CHIP_ASIC:-$O/router}
W=${SWITCH_ASIC:-$O/switch}
[ -s "$R/report.json" ] || $XIRANG asic to2610-router --no-run -o "$R"
[ -s "$W/report.json" ] || $XIRANG asic to2610-switch --no-run -o "$W"
python3 htest/net.py "$W/report.json" "$R/report.json" > "$O/net.v"
python3 -c "import random, sys; open(sys.argv[1], 'wb').write(random.Random(2613).randbytes(65536))" "$O/www/blob"

for t in xta xtb; do asroot ip tuntap add dev "$t" mode tap user "$(id -un)"; done
export NET_SWITCH=$W/report.json NET_ROUTER=$R/report.json NET_BRIDGE=$O/bridge NET_TAP_A=xta NET_TAP_B=xtb
export NET_GAP=${NET_GAP:-0}
export PYTHONPATH="$PWD/sw${PYTHONPATH:+:$PYTHONPATH}"
make -s -C htest -f "$(cocotb-config --makefiles)/Makefile.sim" SIM=icarus TOPLEVEL_LANG=verilog \
  VERILOG_SOURCES="$O/net.v $W/to2610_switch.v $R/to2610_router.v" TOPLEVEL=net MODULE=test_tcp \
  SIM_BUILD="$O/sim" COCOTB_RESULTS_FILE="$O/results.xml" > "$O/sim.log" 2>&1 &
sim=$!
n=0
until [ -f "$O/bridge/ready" ] || ! kill -0 $sim 2> /dev/null || [ $n -ge 360 ]; do
  python3 -c "import time; time.sleep(5)"
  n=$((n + 1))
done
[ -f "$O/bridge/ready" ] || { tail -n 20 "$O/sim.log"; echo "联合仿真没起来"; exit 1; }

# 地址、MAC 与 net.toml 里 A0、B0 的那两条主机路由一致；MTU 114：过路由器的帧不超过 128 字节
side() {  # <命名空间> <网卡> <MAC> <地址/前缀> <网关>
  asroot ip netns add "$1"
  asroot ip link set "$2" netns "$1"
  asroot ip netns exec "$1" ip link set lo up
  asroot ip netns exec "$1" sysctl -qw "net.ipv6.conf.$2.disable_ipv6=1"
  asroot ip netns exec "$1" ip link set "$2" address "$3" mtu 114 up
  asroot ip netns exec "$1" ip addr add "$4" dev "$2"
  asroot ip netns exec "$1" ip route add default via "$5"
}
side xra xta 02:00:00:00:00:10 10.0.0.10/24 10.0.0.1
side xrb xtb 02:00:00:00:00:12 10.0.1.10/24 10.0.1.1
# 命名空间里以 root 跑，解释器给全路径：装了 fastapi 的是当前这个 python，不是系统的
in_b env WWW_ROOT="$O/www" "$(command -v python3)" -m uvicorn www:app --app-dir "$PWD/htest" --host 10.0.1.10 --port 8080 \n  > "$O/http.log" 2>&1 &
echo $! > "$O/http.pid"
for _ in $(seq 60); do
  in_b ss -ltn | grep -q '10.0.1.10:8080 ' && break
  python3 -c "import time; time.sleep(1)"
done

bad=0
ra0=$(retrans in_a)
rb0=$(retrans in_b)
in_a ping -c 3 -W 120 10.0.1.10 > "$O/ping.log" 2>&1 || bad=1
grep -q " 3 received" "$O/ping.log" || { echo "跨网段 ping 不通：$(tail -n 2 "$O/ping.log" | tr '\n' ' ')"; bad=1; }
t0=$SECONDS
in_a curl -s -o "$O/got" --max-time "${NET_LIMIT:-3600}" http://10.0.1.10:8080/blob || true
secs=$((SECONDS - t0))
want=$(md5sum < "$O/www/blob" | cut -c1-32)
got=$(md5sum < "$O/got" 2> /dev/null | cut -c1-32 || true)
[ "$got" = "$want" ] || { echo "64 KiB 没取全：$(stat -c %s "$O/got" 2> /dev/null || echo 0) 字节"; bad=1; }
ra=$(($(retrans in_a) - ra0))
rb=$(($(retrans in_b) - rb0))

touch "$O/bridge/stop"
wait $sim || true
[ -s "$O/bridge/counters.json" ] || { tail -n 20 "$O/sim.log"; echo "联合仿真没收尾"; exit 1; }
grep -q '<failure' "$O/results.xml" && { tail -n 20 "$O/sim.log"; bad=1; }
python3 - "$O/bridge/counters.json" "$secs" "$ra" "$rb" "$NET_GAP" > "$O/summary.txt" <<'EOF'
import json, sys
n = json.load(open(sys.argv[1]))
print(f"NET_GAP={sys.argv[5]}：取 64 KiB 用 {sys.argv[2]} 秒；重传 A0 {sys.argv[3]} 段、B0 {sys.argv[4]} 段；"
      f"路由器收 {n['rx']}、转 {n['fwd']}、因口上的缓冲都压着包丢 {n['busy']}；"
      f"进 A0 {n['a_out']} 帧、进 B0 {n['b_out']} 帧")
EOF
cat "$O/summary.txt"
[ $bad = 0 ] || { echo "真流量下没过"; exit 1; }
echo "真流量下过了"
