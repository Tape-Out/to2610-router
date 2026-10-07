"""联合仿真接上 to2610-kvc：拓扑与 test_net.py 相同，交换机 a 的 5 号口再经一个「桥」接到另一个仿真里的 W5500。

    kvc（W5500）╌桥╌ 5 号口 ─ 交换机 a ─ A0、A1 ─ 7 号口══路由器══交换机 b ─ B0、B1

另一个仿真（kvc 的 Linux，黑盒仓测试台的 +nicpipe）把网卡发出的帧追加进 <目录>/tx，这边从 a 的 5 号口送进交换机；
交换机从 5 号口送出的帧追加进 <目录>/rx。两边各跑各的，只按帧对齐。那边在 <目录> 里放下 stop，这边就收尾。
地址照 future-work 的 04 L4：kvc 是 10.0.0.20，MAC 02:00:00:00:00:20，路由器上为它加一条主机路由。

给了 NET_TAP 时跑 with_host：B0 的位置换成宿主机的一块 TAP 网卡（htest/tap-host.sh 配好的），宿主机有真的 TCP/IP，
并替 10.0.0.0/24 做 NAT 出网；路由器再加一条指向它的默认路由。
"""
import fcntl
import json
import os
import pathlib
import struct

import cocotb

import bench as B
import router as R
from test_net import CFG, clean, up

KVC = {"to": "10.0.0.20/32", "port": 0, "via": "02:00:00:00:00:20"}
TAP = os.environ.get("NET_TAP", "")
# 宿主机顶替的是 B0：主机号 2，地址与 MAC 就是 net.toml 里那条 10.0.1.10 的主机路由
HOST = 2
DEFAULT = {"to": "0.0.0.0/0", "port": 1, "via": "02:00:00:00:00:12"}


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


def tap(name: str) -> int:
    fd = os.open("/dev/net/tun", os.O_RDWR | os.O_NONBLOCK)
    # TUNSETIFF；IFF_TAP | IFF_NO_PI：收发的就是不带 FCS 的以太网帧
    fcntl.ioctl(fd, 0x400454CA, struct.pack("16sH", name.encode(), 0x0002 | 0x1000))
    return fd


async def host(b: B.Bench, phy: B.Phy, fd: int, stop: pathlib.Path, gap: int = 2500):
    """TAP 与交换机 b 的那个口之间搬帧。宿主机来的帧排队、每 gap 拍放进去一帧：路由器每口只有一帧的缓冲，
    外网服务器一口气发来的十段原样灌进去，后几段都被丢掉；真网上丢了几毫秒就补回来，仿真里一帧要将近一秒，
    补一次就是几十秒的退避。2500 拍够一帧过两台交换机与路由器。"""
    up_, down, idle = 0, 0, gap
    queue: list[bytes] = []
    while not stop.exists():
        await b.cycles(64)
        idle += 64
        while True:
            try:
                queue.append(os.read(fd, 2048))
            except BlockingIOError:
                break
        if queue and idle >= gap:
            fr = queue.pop(0)
            phy.send(fr + bytes(max(0, 60 - len(fr))))
            up_ += 1
            idle = 0
        for fr, ok in phy.take():
            if ok:
                os.write(fd, fr)
                down += 1
    return up_, down


@cocotb.test(skip=bool(TAP))
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


@cocotb.test(skip=not TAP)
async def with_host(dut):
    """kvc 经交换机 a、路由器、交换机 b 到宿主机：ping、TCP 取文件、经 NAT 查 DNS 与取外网的页面，判据在 kvc 那一半。
    这一半只管帧有没有丢、坏：两个方向都有帧过、路由器没有因 FCS 或报文头丢过包。"""
    d = pathlib.Path(os.environ["NET_BRIDGE"])
    cfg = dict(CFG, route=CFG["route"] + [KVC, DEFAULT])
    b, phys, spi, hs = await up(dut, cfg, skip={HOST})
    fd = tap(TAP)
    port = B.Phy(phys[0].c, "sw0", 5)
    side = cocotb.start_soon(host(b, phys[HOST], fd, d / "stop"))
    (d / "ready").write_text("1\n")
    sent, back = await bridge(b, port, d)
    to_host, from_host = await side
    os.close(fd)
    n = await spi.do(R.counters())
    (d / "counters.json").write_text(json.dumps({"to_switch": sent, "to_kvc": back, "from_host": to_host,
                                                 "to_host": from_host, **n}) + "\n")
    assert to_host > 0 and from_host > 0, (to_host, from_host)
    assert n["fwd"] >= 2 * 10 and n["fcs"] == 0 and n["hdr"] == 0, n
    clean(phys + [port], hs)
