"""两头都是宿主机的真 TCP/IP：联合仿真里 A0、B0 的位置各换成一块 TAP 网卡（htest/tcp.sh 建好，再挪进两个网络命名空间），
宿主机来的帧按 NET_GAP 拍放进去，tcp.sh 给 0，即按线速连发。这一半只搬帧、收尾时读路由器的计数，
判据（跨网段 ping、TCP 取文件的成败与用时、两边的重传）在 tcp.sh 那一头。
"""
import json
import os
import pathlib

import cocotb

import router as R
from test_net import clean, up
from test_with_kvc import host, tap

# A0、B0 的主机号，见 test_net.HOSTS
A0, B0 = 0, 2


@cocotb.test()
async def tcp(dut):
    d = pathlib.Path(os.environ["NET_BRIDGE"])
    b, phys, spi, hs = await up(dut, skip={A0, B0})
    fa, fb = tap(os.environ["NET_TAP_A"]), tap(os.environ["NET_TAP_B"])
    sa = cocotb.start_soon(host(b, phys[A0], fa, d / "stop"))
    sb = cocotb.start_soon(host(b, phys[B0], fb, d / "stop"))
    (d / "ready").write_text("1\n")
    a_in, a_out = await sa
    b_in, b_out = await sb
    os.close(fa)
    os.close(fb)
    n = await spi.do(R.counters())
    (d / "counters.json").write_text(json.dumps({"a_in": a_in, "a_out": a_out, "b_in": b_in, "b_out": b_out, **n}) + "\n")
    assert a_in > 0 and b_in > 0, (a_in, b_in)
    clean(phys, hs)
