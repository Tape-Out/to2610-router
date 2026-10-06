"""to2610-router 的整片测试，跑在交付的那份 .v 上，经五口顶层进出。

四台主机各接一个口，各占一个网段：p 号口是 10.0.p.0/24，网关 10.0.p.1 是路由器的那个口，主机是 10.0.p.10。
每个用例先复位。管理走 sw/ 里上板用的同一份代码。
"""
import os
import pathlib
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import Combine

import bench as B

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "sw"))
import router as R  # noqa: E402
import spis  # noqa: E402


def gw_mac(p: int) -> bytes:
    return bytes([0x02, 0, 0, 0, 0x01, p])


def gw_ip(p: int) -> bytes:
    return bytes([10, 0, p, 1])


def host_ip(p: int) -> bytes:
    return bytes([10, 0, p, 10])


def dotted(b: bytes) -> str:
    return ".".join(map(str, b))


CFG = {
    "port": [{"mac": gw_mac(p).hex(":"), "ip": dotted(gw_ip(p))} for p in range(R.PORTS)],
    "route": [{"to": dotted(host_ip(p)) + "/32", "port": p, "via": B.mac_of(p).hex(":")}
              for p in range(R.PORTS)],
}


def packet(p: int, dst: bytes, body: bytes = b"to2610-router" * 3, ttl: int = 64, ident: int = 7) -> bytes:
    """p 号主机发出的一个包。协议号取 17，主机收到了记下来但不应答。"""
    return B.ipv4(host_ip(p), dst, 17, body, ident=ident, ttl=ttl)


def frame(p: int, pkt: bytes) -> bytes:
    """p 号主机把包交给它的网关。"""
    return B.eth(gw_mac(p), B.mac_of(p), 0x0800, pkt)


def routed(q: int, pkt: bytes) -> bytes:
    """从 q 号口转出、交给 q 号主机的那一帧。包由调用方按 TTL 减一重算，不照搬路由器的增量更新。"""
    return B.eth(B.mac_of(q), gw_mac(q), 0x0800, pkt)


async def up(dut, cfg=CFG):
    b = B.Bench(dut)
    c = B.Chip(b, os.environ["CHIP_REPORT"])
    cocotb.start_soon(Clock(dut.clock, 1000 / c.mhz, units="ns").start())
    dut.reset.value = 1
    phys = [B.Phy(c, "rt0", p) for p in range(R.PORTS)]
    spi = B.Spi(c)
    mdio = B.MdioPhy(c)
    cocotb.start_soon(b.run())
    await b.release()
    await b.cycles(8)
    hs = [B.Host(b, phys[p], B.mac_of(p), host_ip(p), gw=gw_ip(p), n=p) for p in range(R.PORTS)]
    cocotb.start_soon(B.serve(hs, b))
    if cfg:
        await spi.do(R.load(cfg, fresh=True))
    return b, c, phys, spi, hs, mdio


def clean(phys, hs):
    assert not any(p.bad_oe for p in phys), "RMII 的发送脚要一直驱动"
    assert not any(h.bad for h in hs), [h.bad for h in hs]


async def quiet(b, phys, n=600):
    """等各口发完、路由器也转完：连着 n 拍哪个口上都没有帧。"""
    k = 0
    while k < n:
        await b.cycles(8)
        k = 0 if any(p.busy() or p.cur is not None for p in phys) else k + 8


@cocotb.test()
async def closed_until_configured(dut):
    """复位后是关着的：不答 ARP、不转发、不计数。配好打开就通。"""
    b, c, phys, spi, hs, _ = await up(dut, cfg=None)
    h = hs[0]
    phys[0].send(B.eth(B.BCAST, h.mac, 0x0806, B.arp(1, h.mac, h.ip, bytes(6), gw_ip(0))))
    phys[0].send(frame(0, packet(0, host_ip(1))))
    await quiet(b, phys)
    assert all(x.seen == [] for x in hs)
    assert set((await spi.do(R.counters())).values()) == {0}
    await spi.do(R.load(CFG, fresh=True))
    assert await hs[0].ping(host_ip(1))
    dut._log.info("关着时到过两帧，打开并 ping 通之后的计数：%s", await spi.do(R.counters()))
    clean(phys, hs)


@cocotb.test()
async def management(dut):
    b, c, phys, spi, hs, _ = await up(dut)
    assert await spi.do(spis.ident()) == b"SPIS"
    assert await spi.do(R.ports()) == [(R.mac(e["mac"]), R.ip(e["ip"])) for e in CFG["port"]]
    assert await spi.do(R.routes()) == [
        (p, int.from_bytes(host_ip(p), "big"), 32, p, int.from_bytes(B.mac_of(p), "big"))
        for p in range(R.PORTS)]
    assert await spi.do(spis.status()) == 0
    # 没有东西的地址回错，状态字 bit0 记下，读一次就清
    await spi.do(spis.rd1(0x2000_0000))
    assert await spi.do(spis.status()) == 1
    assert await spi.do(spis.status()) == 0
    # 整份重写：没写到的口与表项清掉
    await spi.do(R.load({"port": CFG["port"][:1], "route": CFG["route"][:1]}))
    assert await spi.do(R.ports()) == [(R.mac(CFG["port"][0]["mac"]), R.ip(CFG["port"][0]["ip"]))] + [(0, 0)] * 3
    assert [r[0] for r in await spi.do(R.routes())] == [0]


@cocotb.test()
async def arp_gateway(dut):
    """问本口地址的 ARP 就地应答，各字段照 RFC 826；问别的地址、问别的口的地址都不答。"""
    b, c, phys, spi, hs, _ = await up(dut)
    h = hs[2]

    def ask(ip):
        phys[2].send(B.eth(B.BCAST, h.mac, 0x0806, B.arp(1, h.mac, h.ip, bytes(6), ip)))

    ask(gw_ip(2))
    await quiet(b, phys)
    want = B.eth(h.mac, gw_mac(2), 0x0806, B.arp(2, gw_mac(2), gw_ip(2), h.mac, h.ip))
    assert h.seen == [want], h.seen
    assert all(x.seen == [] for x in hs if x is not h)
    ask(bytes([10, 0, 2, 77]))
    await quiet(b, phys, 100)
    ask(gw_ip(1))
    await quiet(b, phys)
    assert h.seen == [want]
    n = await spi.do(R.counters())
    assert (n["rx"], n["arp"], n["l2"], n["fwd"]) == (3, 1, 2, 0), n
    clean(phys, hs)


@cocotb.test()
async def ping_across(dut):
    """跨网段 ping：主机先问到网关的 MAC，包经路由器换 MAC、TTL 减一到对面，应答原路回来。"""
    b, c, phys, spi, hs, _ = await up(dut)
    assert await hs[0].ping(host_ip(1))
    assert hs[1].ttl == [63] and hs[0].ttl == [63], (hs[0].ttl, hs[1].ttl)
    n = await spi.do(R.counters())
    assert (n["rx"], n["fwd"], n["arp"]) == (4, 2, 2), n
    assert not any(n[k] for k in ("fcs", "l2", "hdr", "ttl", "route", "long", "busy")), n
    clean(phys, hs)


@cocotb.test()
async def ping_pairs(dut):
    """两对主机同时跨网段互 ping，再换一种配对。"""
    b, c, phys, spi, hs, _ = await up(dut)
    for pairs in (((0, 1), (2, 3)), ((1, 2), (3, 0))):
        got = [cocotb.start_soon(hs[a].ping(host_ip(z), seq=10 + a)) for a, z in pairs]
        await Combine(*got)
        assert all(t.result() for t in got), [t.result() for t in got]
    clean(phys, hs)


@cocotb.test()
async def route_table(dut):
    """没有路由就丢并计数；加了默认路由走它；更长的前缀压过它；撤掉又回到默认路由。表是开着时改的。"""
    b, c, phys, spi, hs, _ = await up(dut)
    far = bytes([10, 9, 9, 9])

    async def go(dst):
        for h in hs:
            h.seen.clear()
        phys[0].send(frame(0, packet(0, dst)))
        await quiet(b, phys)
        return [len(h.seen) for h in hs]

    assert await go(far) == [0, 0, 0, 0]
    assert (await spi.do(R.counters()))["route"] == 1
    await spi.do(R.route(4, "0.0.0.0/0", 3, B.mac_of(3).hex(":")))
    assert await go(far) == [0, 0, 0, 1]
    assert hs[3].seen == [routed(3, packet(0, far, ttl=63))]
    await spi.do(R.route(5, "10.9.0.0/16", 2, B.mac_of(2).hex(":")))
    assert await go(far) == [0, 0, 1, 0]
    assert hs[2].seen == [routed(2, packet(0, far, ttl=63))]
    assert await go(bytes([10, 8, 1, 1])) == [0, 0, 0, 1]
    assert await go(host_ip(1)) == [0, 1, 0, 0]
    await spi.do(R.drop(5))
    assert await go(far) == [0, 0, 0, 1]
    clean(phys, hs)


@cocotb.test()
async def drops(dut):
    """该丢的各丢各的、各记各的数，一帧也不转出去；之后照常转发。"""
    b, c, phys, spi, hs, _ = await up(dut)
    good = packet(0, host_ip(1))
    sick = bytearray(good)
    sick[10] ^= 0x01
    cases = (
        (frame(0, packet(0, host_ip(1), ttl=1)), True),              # ttl
        (frame(0, packet(0, host_ip(1), ttl=0)), True),              # ttl
        (frame(0, bytes(sick)), True),                               # hdr：头部校验和不对
        (B.eth(gw_mac(1), B.mac_of(0), 0x0800, good), True),         # l2：别的口的 MAC
        (B.eth(B.BCAST, B.mac_of(0), 0x0800, good), True),           # l2：广播的 IPv4
        (B.eth(gw_mac(0), B.mac_of(0), 0x86DD, good), True),         # l2：不是 IPv4
        (frame(0, good), False),                                     # fcs
        (frame(0, packet(0, bytes([192, 168, 1, 1]))), True),        # route
    )
    for f, ok in cases:
        phys[0].send(f, good=ok)
        await quiet(b, phys, 100)
    assert all(h.seen == [] for h in hs), [len(h.seen) for h in hs]
    n = await spi.do(R.counters())
    assert n == {"rx": 8, "fwd": 0, "fcs": 1, "l2": 3, "hdr": 1, "ttl": 2, "route": 1,
                 "long": 0, "busy": 0, "arp": 0}, n
    phys[0].send(frame(0, good))
    await quiet(b, phys)
    assert hs[1].seen == [routed(1, packet(0, host_ip(1), ttl=63))]
    clean(phys, hs)


@cocotb.test()
async def frame_sizes(dut):
    """缓冲 512 字节：正好 512 字节的帧转出去，逐字节是该有的样子；多一个字节整帧丢掉并计数。"""
    b, c, phys, spi, hs, _ = await up(dut)
    body = bytes(k & 0xFF for k in range(R.MTU - 14 - 20))
    f = frame(0, packet(0, host_ip(1), body))
    assert len(f) == R.MTU
    phys[0].send(f)
    await quiet(b, phys)
    assert hs[1].seen == [routed(1, packet(0, host_ip(1), body, ttl=63))]
    hs[1].seen.clear()
    phys[0].send(frame(0, packet(0, host_ip(1), body + b"!")))
    await quiet(b, phys)
    assert hs[1].seen == []
    n = await spi.do(R.counters())
    assert (n["rx"], n["fwd"], n["long"]) == (2, 1, 1), n
    clean(phys, hs)


@cocotb.test()
async def one_frame_per_port(dut):
    """每个入口只存一帧：前一帧还没发完时到的下一帧整帧丢掉并计数。

    两个入口同时发往同一个出口则各存各的，先后都送到。
    """
    b, c, phys, spi, hs, _ = await up(dut)

    def pkt(p, k, ttl=64):
        return packet(p, host_ip(1), bytes([k]) * 64, ttl=ttl, ident=k)

    phys[0].send(frame(0, pkt(0, 1)))
    phys[0].send(frame(0, pkt(0, 2)))
    await quiet(b, phys)
    assert hs[1].seen == [routed(1, pkt(0, 1, 63))]
    assert (await spi.do(R.counters()))["busy"] == 1
    hs[1].seen.clear()
    phys[0].send(frame(0, pkt(0, 3)))
    phys[2].send(frame(2, pkt(2, 4)))
    await quiet(b, phys)
    assert sorted(hs[1].seen) == sorted([routed(1, pkt(0, 3, 63)), routed(1, pkt(2, 4, 63))])
    n = await spi.do(R.counters())
    assert (n["rx"], n["fwd"], n["busy"]) == (4, 3, 1), n
    clean(phys, hs)


@cocotb.test()
async def switch_off(dut):
    """运行中关掉就不转发，再打开照常。"""
    b, c, phys, spi, hs, _ = await up(dut)
    assert await hs[0].ping(host_ip(1))
    await spi.do(R.enable(False))
    assert not await hs[0].ping(host_ip(1), seq=2, limit=4000)
    await spi.do(R.enable(True))
    assert await hs[0].ping(host_ip(1), seq=3)
    dut._log.info("关了又开之后的计数：%s", await spi.do(R.counters()))
    clean(phys, hs)


@cocotb.test()
async def mdio(dut):
    """经 GPIO 拨出 MDIO：读 PHY 的标识寄存器，写一个寄存器并在 PHY 那头核对。"""
    b, c, phys, spi, hs, phy = await up(dut, cfg=None)
    assert await spi.do(R.mdio_read(1, 2)) == 0x0007
    await spi.do(R.mdio_write(1, 4, 0x01E1))
    assert phy.regs[4] == 0x01E1


@cocotb.test()
async def gpio(dut):
    """余下四根 GPIO：出方向时驱到焊盘上，入方向时读得回外面给的电平。"""
    b, c, phys, spi, hs, _ = await up(dut, cfg=None)
    await spi.do(spis.wr(R.GPIO + R.DIR, 0b001100))
    await spi.do(spis.wr(R.GPIO + R.DOUT, 0b000100))
    await b.cycles(8)
    o, e = c.read()
    pin = lambda i, r: c.bit(f"gpio0_pins_gpio_{r}[{i}]", {"out": "out", "dir": "oe", "in": "in"}[r])  # noqa: E731
    assert (e >> pin(2, "dir")) & 1 and (e >> pin(3, "dir")) & 1
    assert not (e >> pin(4, "dir")) & 1
    assert (o >> pin(2, "out")) & 1 and not (o >> pin(3, "out")) & 1
    c.set(pin(4, "in"), 1)
    c.set(pin(5, "in"), 0)
    await b.cycles(8)
    assert (await spi.do(spis.rd1(R.GPIO + R.DIN)) >> 4) & 0b11 == 0b01
