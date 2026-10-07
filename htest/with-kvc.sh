#!/usr/bin/env bash
# 联合仿真接上 to2610-kvc 的那一半：两颗 to2610-switch 夹一颗 to2610-router，交换机 a 的 5 号口经 <桥目录> 接到
# kvc 的仿真（黑盒仓测试台的 +nicpipe）。这边先起，在 <桥目录> 里放下 ready；那边跑完放下 stop，这边收尾。
# 用法：with-kvc.sh <输出目录> <桥目录>。两颗已经出过 .v 的，把输出目录分别给 CHIP_ASIC（路由器）与 SWITCH_ASIC
set -euo pipefail
cd "$(dirname "$0")/.."
O=$(realpath -m "$1")
D=$(realpath -m "$2")
rm -rf "$O"
mkdir -p "$O" "$D"
R=${CHIP_ASIC:-$O/router}
W=${SWITCH_ASIC:-$O/switch}
[ -s "$R/report.json" ] || $XIRANG asic to2610-router --no-run -o "$R"
[ -s "$W/report.json" ] || $XIRANG asic to2610-switch --no-run -o "$W"
python3 htest/net.py "$W/report.json" "$R/report.json" > "$O/net.v"
export NET_SWITCH=$W/report.json NET_ROUTER=$R/report.json NET_BRIDGE=$D
export PYTHONPATH="$PWD/sw${PYTHONPATH:+:$PYTHONPATH}"
make -s -C htest -f "$(cocotb-config --makefiles)/Makefile.sim" SIM=icarus TOPLEVEL_LANG=verilog \
  VERILOG_SOURCES="$O/net.v $W/to2610_switch.v $R/to2610_router.v" TOPLEVEL=net MODULE=test_with_kvc \
  SIM_BUILD="$O/sim" COCOTB_RESULTS_FILE="$O/results.xml" > "$O/sim.log" 2>&1 || true
tail -n 20 "$O/sim.log"
[ -s "$O/results.xml" ] || { echo "没有 results.xml"; exit 1; }
if grep -q '<failure' "$O/results.xml"; then echo "联调没过"; exit 1; fi
echo "联调过了：$(cat "$D/counters.json")"
