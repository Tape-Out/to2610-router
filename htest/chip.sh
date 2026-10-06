#!/usr/bin/env bash
# 整片测试：先 ran asic 出交付的那份 .v，再用 cocotb 在它上面跑 test_chip.py。
# 用法：chip.sh <输出目录>。息壤的调用方式由任务环境里的 $XIRANG 给，它带着这次的搜索路径。
# 已经跑过 ran asic 的，把它的输出目录给 CHIP_ASIC，就不再编一遍
set -euo pipefail
cd "$(dirname "$0")/.."
O=$(realpath -m "$1")
rm -rf "$O"
mkdir -p "$O"
A=${CHIP_ASIC:-$O/asic}
[ -s "$A/report.json" ] || $XIRANG asic to2610-router --no-run -o "$A"
export CHIP_REPORT=$A/report.json
make -s -C htest -f "$(cocotb-config --makefiles)/Makefile.sim" SIM=icarus TOPLEVEL_LANG=verilog \
  VERILOG_SOURCES="$A/to2610_router.v" TOPLEVEL=to2610_router MODULE=test_chip \
  SIM_BUILD="$O/sim" COCOTB_RESULTS_FILE="$O/results.xml" > "$O/sim.log" 2>&1 || true
tail -n 30 "$O/sim.log"
# cocotb 失败时 make 照样返回 0，判据是结果文件
[ -s "$O/results.xml" ] || { echo "没有 results.xml"; exit 1; }
if grep -q '<failure' "$O/results.xml"; then echo "有用例没过"; exit 1; fi
n=$(grep -c '<testcase' "$O/results.xml")
[ "$n" -gt 0 ] || { echo "一个用例也没跑"; exit 1; }
echo "整片测试 $n 个全过"
