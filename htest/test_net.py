"""联合仿真：两颗 to2610-switch 中间夹一颗 to2610-router，三颗都是交付的那份 .v。

    A0、A1 ── 交换机 a ──7 号口══0 号口── 路由器 ──1 号口══7 号口── 交换机 b ── B0、B1
      10.0.0.0/24，网关 10.0.0.1                         10.0.1.0/24，网关 10.0.1.1

交换机不碰管理口，复位后就在转发。路由器经它的 SPI 口写进 sw/net.toml：上板用的同一份工具、同一份配置。
"""
import os
import pathlib
import sys
import tomllib

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import Combine

import bench as B

SW = pathlib.Path(__file__).resolve().parent.parent / "sw"
sys.path.insert(0, str(SW))
import router as R  # noqa: E402

CFG = tomllib.loads((SW / "net.toml").read_text(encoding="utf-8"))
GW = (bytes([10, 0, 0, 1]), bytes([10, 0, 1, 1]))
# (哪台交换机, 口, 主机号, IP)；MAC 与 IP 就是 net.toml 里那四条主机路由
HOSTS = (("a", 0, 0, bytes([10, 0, 0, 10])), ("a", 3, 1, bytes([10, 0, 0, 11])),
         ("b", 1, 2, bytes([10, 0, 1, 10])), ("b", 6, 3, bytes([10, 0, 1, 11])))


async def up(dut, cfg=CFG):
    b = B.Bench(dut)
    chip = {"a": B.Chip(b, os.environ["NET_SWITCH"], "a_"), "r": B.Chip(b, os.environ["NET_ROUTER"], "r_"),
            "b": B.Chip(b, os.environ["NET_SWITCH"], "b_")}
    cocotb.start_soon(Clock(dut.clock, 1000 / chip["r"].mhz, units="ns").start())
    dut.reset.value = 1
    phys = [B.Phy(chip[s], "sw0", port) for s, port, _, _ in HOSTS]
    spi = B.Spi(chip["r"])
    cocotb.start_soon(b.run())
    await b.release()
    await b.cycles(8)
    hs = [B.Host(b, phy, B.mac_of(n), ip, gw=GW[s == "b"], n=n) for phy, (s, _, n, ip) in zip(phys, HOSTS)]
    cocotb.start_soon(B.serve(hs, b))
    if cfg:
        await spi.do(R.load(cfg, fresh=True))
    return b, phys, spi, hs


def clean(phys, hs):
    assert not any(p.bad_oe for p in phys), "RMII 的发送脚要一直驱动"
    assert not any(h.bad for h in hs), [h.bad for h in hs]


@cocotb.test()
async def across(dut):
    """两个网段的主机互 ping：A0 到 B0，再 B1 到 A1。每个方向过一次路由器，TTL 到达时是 63。"""
    b, phys, spi, hs = await up(dut)
    a0, a1, b0, b1 = hs
    assert await a0.ping(b0.ip, limit=40000)
    assert a0.ttl == [63] and b0.ttl == [63], (a0.ttl, b0.ttl)
    assert await b1.ping(a1.ip, limit=40000)
    n = await spi.do(R.counters())
    assert n["fwd"] == 4 and n["arp"] == 4, n
    clean(phys, hs)


@cocotb.test()
async def local_stays_local(dut):
    """同一网段的两台直接经交换机通，不过路由器：路由器只收到问别人的 ARP 广播，一个包也不转。"""
    b, phys, spi, hs = await up(dut)
    a0, a1, b0, b1 = hs
    assert await a0.ping(a1.ip)
    assert a0.ttl == [64] and a1.ttl == [64]
    n = await spi.do(R.counters())
    assert n["fwd"] == 0 and n["arp"] == 0 and n["l2"] >= 1, n
    clean(phys, hs)


@cocotb.test()
async def both_ways_at_once(dut):
    """两个方向同时 ping。"""
    b, phys, spi, hs = await up(dut)
    a0, a1, b0, b1 = hs
    got = [cocotb.start_soon(a0.ping(b1.ip, seq=5, limit=60000)),
           cocotb.start_soon(b0.ping(a1.ip, seq=6, limit=60000))]
    await Combine(*got)
    assert all(t.result() for t in got), [t.result() for t in got]
    clean(phys, hs)


@cocotb.test()
async def closed_router(dut):
    """路由器没配置：跨网段不通，网段内照通。"""
    b, phys, spi, hs = await up(dut, cfg=None)
    a0, a1, b0, b1 = hs
    assert not await a0.ping(b0.ip, limit=6000, tries=2)
    assert await b0.ping(b1.ip)
    clean(phys, hs)


@cocotb.test()
async def no_route(dut):
    """发往没有路由的网段：网关答了 ARP，包到路由器就丢并计数。"""
    b, phys, spi, hs = await up(dut)
    a0 = hs[0]
    assert not await a0.ping(bytes([10, 0, 2, 9]), limit=8000, tries=2)
    n = await spi.do(R.counters())
    assert n["route"] == 2 and n["fwd"] == 0 and n["arp"] == 1, n
    clean(phys, hs)
