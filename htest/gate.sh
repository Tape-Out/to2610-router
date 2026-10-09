#!/usr/bin/env bash
# 门级冒烟：布线后的网表配 ICS55 标准单元的功能模型（`functional`，不带时序），在 iverilog 上跑整片测试里的几项。
# 网表与单元模型由组织的 asic.yml 给：GATE_NETLIST 是拼回了向量口的布线后网表，GATE_CELLS 是空格分隔的单元模型文件。
# 门级比 RTL 慢二十多倍，默认整片测试的十二项全跑；GATE_TESTS 换别的（逗号分隔）。
# 用法：gate.sh <输出目录>
set -euo pipefail
cd "$(dirname "$0")/.."
O=$(realpath -m "$1")
rm -rf "$O"
mkdir -p "$O"
A=${CHIP_ASIC:-$O/asic}
[ -s "$A/report.json" ] || $XIRANG asic to2610-router --no-run -o "$A"
export CHIP_REPORT=$A/report.json
make -s -C htest -f "$(cocotb-config --makefiles)/Makefile.sim" SIM=icarus TOPLEVEL_LANG=verilog \
  VERILOG_SOURCES="${GATE_NETLIST:?要布线后的网表} ${GATE_CELLS:?要单元模型}" COMPILE_ARGS="-Dfunctional" TOPLEVEL=to2610_router MODULE=test_chip \
  TESTCASE="${GATE_TESTS:-closed_until_configured,management,arp_gateway,ping_across,ping_pairs,route_table,drops,frame_sizes,back_to_back,switch_off,mdio,gpio}" SIM_BUILD="$O/sim" COCOTB_RESULTS_FILE="$O/results.xml" > "$O/sim.log" 2>&1 || true
tail -n 20 "$O/sim.log"
# cocotb 失败时 make 照样返回 0，判据是结果文件
[ -s "$O/results.xml" ] || { echo "没有 results.xml"; exit 1; }
if grep -q '<failure' "$O/results.xml"; then echo "门级有用例没过"; exit 1; fi
n=$(grep -c '<testcase' "$O/results.xml")
[ "$n" -gt 0 ] || { echo "一个用例也没跑"; exit 1; }
echo "门级 $n 项全过"
