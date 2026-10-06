#!/usr/bin/env bash
# 联合仿真：两颗 to2610-switch 夹一颗 to2610-router，三颗都是 ran asic 出的交付文件，cocotb 跑 test_net.py。
# 用法：net.sh <输出目录>。两颗已经出过 .v 的，把输出目录分别给 CHIP_ASIC（路由器）与 SWITCH_ASIC
set -euo pipefail
cd "$(dirname "$0")/.."
O=$(realpath -m "$1")
rm -rf "$O"
mkdir -p "$O"
R=${CHIP_ASIC:-$O/router}
W=${SWITCH_ASIC:-$O/switch}
[ -s "$R/report.json" ] || $XIRANG asic to2610-router --no-run -o "$R"
[ -s "$W/report.json" ] || $XIRANG asic to2610-switch --no-run -o "$W"
python3 htest/net.py "$W/report.json" "$R/report.json" > "$O/net.v"
export NET_SWITCH=$W/report.json NET_ROUTER=$R/report.json
make -s -C htest -f "$(cocotb-config --makefiles)/Makefile.sim" SIM=icarus TOPLEVEL_LANG=verilog \
  VERILOG_SOURCES="$O/net.v $W/to2610_switch.v $R/to2610_router.v" TOPLEVEL=net MODULE=test_net \
  SIM_BUILD="$O/sim" COCOTB_RESULTS_FILE="$O/results.xml" > "$O/sim.log" 2>&1 || true
tail -n 30 "$O/sim.log"
[ -s "$O/results.xml" ] || { echo "没有 results.xml"; exit 1; }
if grep -q '<failure' "$O/results.xml"; then echo "有用例没过"; exit 1; fi
n=$(grep -c '<testcase' "$O/results.xml")
[ "$n" -gt 0 ] || { echo "一个用例也没跑"; exit 1; }
echo "联合仿真 $n 个全过"
