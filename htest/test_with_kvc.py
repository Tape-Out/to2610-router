"""联合仿真接上 to2610-kvc：拓扑与 test_net.py 相同，交换机 a 的 5 号口再经一个「桥」接到另一个仿真里的 W5500。

    kvc（W5500）╌桥╌ 5 号口 ─ 交换机 a ─ A0、A1 ─ 7 号口══路由器══交换机 b ─ B0、B1

另一个仿真（kvc 的 Linux，黑盒仓测试台的 +nicpipe）把网卡发出的帧追加进 <目录>/tx，这边从 a 的 5 号口送进交换机；
交换机从 5 号口送出的帧追加进 <目录>/rx。两边各跑各的，只按帧对齐。那边在 <目录> 里放下 stop，这边就收尾。
地址照 future-work 的 04 L4：kvc 是 10.0.0.20，MAC 02:00:00:00:00:20，路由器上为它加一条主机路由。
"""
import json
import os
import pathlib

import cocotb

import bench as B
import router as R
from test_net import CFG, clean, up

KVC = {"to": "10.0.0.20/32", "port": 0, "via": "02:00:00:00:00:20"}


async def bridge(b: B.Bench, phy: B.Phy, d: pathlib.Path):
    tx, rx, stop = d / "tx", d / "rx", d / "stop"
    off, sent, back = 0, 0, 0
    while not stop.exists():
        await b.cycles(64)
        if tx.exists():
            data = tx.read_bytes()
            while off + 2 <= len(data):
                n = int.from_bytes(data[off:off + 2], "big")
                if off + 2 + n > len(data):
                    break
                frame = data[off + 2:off + 2 + n]
                # W5500 上线前补齐到 60 字节，这里替它补
                phy.send(frame + bytes(max(0, 60 - len(frame))))
                off += 2 + n
                sent += 1
        got = [f for f, ok in phy.take() if ok]
        if got:
            with rx.open("ab") as f:
                for fr in got:
                    f.write(len(fr).to_bytes(2, "big") + fr)
            back += len(got)
    return sent, back


@cocotb.test()
async def with_kvc(dut):
    d = pathlib.Path(os.environ["NET_BRIDGE"])
    cfg = dict(CFG, route=CFG["route"] + [KVC])
    b, phys, spi, hs = await up(dut, cfg)
    port = B.Phy(phys[0].c, "sw0", 5)
    (d / "ready").write_text("1\n")
    sent, back = await bridge(b, port, d)
    n = await spi.do(R.counters())
    (d / "counters.json").write_text(json.dumps({"to_switch": sent, "to_kvc": back, **n}) + "\n")
    # 跨网段的三个 ping 来回各过一次路由器
    assert n["fwd"] >= 6, n
    clean(phys + [port], hs)
