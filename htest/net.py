"""出联合仿真的顶层 net.v：两颗 to2610-switch（a、b）夹一颗 to2610-router（r）。

每颗的三根向量照旧引到顶层（a_io_in 等），主机与管理口从那里接。芯片之间的 RMII 口对口直连：
一头的 txd、tx_en 接另一头的 rxd、crs_dv，rx_er 接 0。哪根线在 payload 的哪一位取自两份 report.json，
位表改了这里跟着变。板子上这一段是两颗 PHY 加一根网线，三颗同一个 50 MHz。

用法：net.py <to2610-switch 的 report.json> <to2610-router 的 report.json> > net.v
"""
import json
import pathlib
import sys

# (交换机, 它的口, 路由器的口)
LINKS = (("a", 7, 0), ("b", 7, 1))


def table(path: str) -> tuple[str, int, dict[str, dict[str, int]]]:
    rep = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    pin: dict[str, dict[str, int]] = {}
    for r in rep["pads"]["bits"]:
        for role in ("in", "out", "oe"):
            if r[role]:
                pin.setdefault(r[role], {})[role] = r["bit"]
    return rep["top"], rep["pads"]["width"], pin


def rmii(pin: dict, inst: str, port: int) -> tuple[list[int], list[int], int]:
    """一个口发出的三位、收进的三位（次序都是 d0、d1、有效）与 rx_er。"""
    tx = [pin[f"{inst}_pins_tx_{port}_{s}"]["out"] for s in ("txd[0]", "txd[1]", "tx_en[0]")]
    rx = [pin[f"{inst}_pins_rx_{port}_{s}"]["in"] for s in ("rxd[0]", "rxd[1]", "crs_dv[0]")]
    return tx, rx, pin[f"{inst}_pins_rx_{port}_rx_er[0]"]["in"]


def main() -> int:
    (stop, w, sw), (rtop, rw, rt) = table(sys.argv[1]), table(sys.argv[2])
    if w != rw:
        sys.exit(f"两颗的 payload 位数不同：{w} 与 {rw}")
    # 每颗的 io_in 里不取顶层的那几位，各取什么
    src: dict[str, dict[int, str]] = {c: {} for c in "arb"}
    for s, sp, rp in LINKS:
        stx, srx, ser = rmii(sw, "sw0", sp)
        rtx, rrx, rer = rmii(rt, "rt0", rp)
        for k in range(3):
            src[s][srx[k]] = f"r_io_out[{rtx[k]}]"
            src["r"][rrx[k]] = f"{s}_io_out[{stx[k]}]"
        src[s][ser] = src["r"][rer] = "1'b0"
    hi = w - 1
    out = ["// 由 htest/net.py 照两份 report.json 生成，不手改", "module net (",
           "  input  wire        clock,", "  input  wire        reset,"]
    for c in "arb":
        out += [f"  input  wire [{hi}:0] {c}_io_in,", f"  output wire [{hi}:0] {c}_io_out,",
                f"  output wire [{hi}:0] {c}_io_oe" + ("" if c == "b" else ",")]
    out += [");", f"  wire [{hi}:0] a_in, r_in, b_in;"]
    for c in "arb":
        out += [f"  assign {c}_in[{i}] = {src[c].get(i, f'{c}_io_in[{i}]')};" for i in range(w)]
    for c, top in (("a", stop), ("r", rtop), ("b", stop)):
        out.append(f"  {top} {c} (.clock(clock), .reset(reset), .io_in({c}_in), "
                   f".io_out({c}_io_out), .io_oe({c}_io_oe));")
    out.append("endmodule")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
