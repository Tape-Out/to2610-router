"""to2610-router 的管理：口地址、路由表、计数、经 GPIO 走 MDIO。

复位后路由器是关着的：各口的 MAC 与 IP、路由表都是空的，要主机写进去再开。路由表的下一跳是
MAC 地址，路由器自己不发 ARP，所以直连的主机也要各写一条 /32。地址照 `erouter` 与 `gpio` 的
regmap.yaml 与本仓 ip.yaml 的 addr。

    python3 sw/router.py --ftdi ftdi://ftdi:232h/1 load sw/net.toml
    python3 sw/router.py --spidev 0.0 routes
"""
import argparse
import ipaddress
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import spis  # noqa: E402

RT, GPIO = 0x1000_0000, 0x1000_1000
PORTS, ROUTES, MTU = 4, 16, 512     # 照本仓 ip.yaml 的 ports、routes、mtu

CTRL = 0x000
MACHI, IPADDR = 0x010, 0x080        # 每口：MAC 的高 16 位、低 32 位两个字；IP 一个字
ROUTE = 0x100                       # 每条四个字：前缀、{有效 15, 出口 11:8, 前缀长 5:0}、下一跳 MAC 高 16 位、低 32 位
COUNT = 0x400
COUNTERS = ("rx", "fwd", "fcs", "l2", "hdr", "ttl", "route", "long", "busy", "arp")
DOUT, DIN, DIR = 0x00, 0x04, 0x08
MDC, MDIO = 0, 1                    # GPIO 的第 0、1 位


def mac(s) -> int:
    """先上线的字节在最高位。"""
    if isinstance(s, int):
        return s
    b = bytes.fromhex(s.replace(":", "").replace("-", ""))
    if len(b) != 6:
        raise ValueError(f"MAC 要 6 个字节：{s}")
    return int.from_bytes(b, "big")


def ip(s) -> int:
    return int(ipaddress.IPv4Address(s))


def net(s) -> tuple[int, int]:
    n = ipaddress.IPv4Network(s, strict=False)
    return int(n.network_address), n.prefixlen


def mac_str(m: int) -> str:
    return m.to_bytes(6, "big").hex(":")


def _halves(m: int) -> tuple[int, int]:
    return m >> 32, m & 0xFFFF_FFFF


def _entry(to, out: int, via) -> tuple[int, int, int, int]:
    prefix, n = net(to)
    if not 0 <= out < PORTS:
        raise ValueError(f"出口 {out} 不在 0 至 {PORTS - 1}")
    return prefix, 1 << 15 | out << 8 | n, *_halves(mac(via))


def enable(on: bool = True):
    yield from spis.wr(RT + CTRL, int(on))


def port(p: int, mac_, ip_):
    """口的 MAC 与 IP。IP 写 0.0.0.0 则本口不答 ARP。"""
    yield from spis.wr(RT + MACHI + 8 * p, *_halves(mac(mac_)))
    yield from spis.wr(RT + IPADDR + 4 * p, ip(ip_))


def route(slot: int, to, out: int, via):
    """路由器开着时改一条：先撤有效位，改完再置上，查表看不到改了一半的表项。"""
    prefix, cfg, hi, lo = _entry(to, out, via)
    a = RT + ROUTE + 16 * slot
    yield from spis.wr(a + 4, 0)
    yield from spis.wr(a, prefix)
    yield from spis.wr(a + 8, hi, lo)
    yield from spis.wr(a + 4, cfg)


def drop(slot: int):
    yield from spis.wr(RT + ROUTE + 16 * slot + 4, 0)


def load(cfg: dict, fresh: bool = False):
    """整份配置一次写进去：先关，写各口、写路由表，再开。关着的时候没有包在查表，整条可以连着写。

    `cfg` 是 net.toml 的样子：`port` 一列（`mac`、`ip`，可带 `n`，不带就按次序），`route` 一列
    （`to`、`port`、`via`）。没写到的口与表项清零；`fresh` 说明芯片刚复位、表本来就是空的，不必清。
    """
    ports = [(0, 0)] * PORTS
    for i, e in enumerate(cfg.get("port", [])):
        ports[e.get("n", i)] = (mac(e["mac"]), ip(e["ip"]))
    routes = [_entry(e["to"], e["port"], e["via"]) for e in cfg.get("route", [])]
    if len(routes) > ROUTES:
        raise ValueError(f"路由表只有 {ROUTES} 条，给了 {len(routes)} 条")
    if not fresh:
        routes += [(0, 0, 0, 0)] * (ROUTES - len(routes))
    yield from enable(False)
    yield from spis.wr(RT + MACHI, *(w for m, _ in ports for w in _halves(m)))
    yield from spis.wr(RT + IPADDR, *(a for _, a in ports))
    if routes:
        yield from spis.wr(RT + ROUTE, *(w for r in routes for w in r))
    yield from enable(True)


def ports():
    """[(MAC, IP)]，按口号。"""
    w = yield from spis.rd(RT + MACHI, 2 * PORTS)
    a = yield from spis.rd(RT + IPADDR, PORTS)
    return [((w[2 * p] & 0xFFFF) << 32 | w[2 * p + 1], a[p]) for p in range(PORTS)]


def routes():
    """有效的表项：[(槽, 前缀, 前缀长, 出口, 下一跳 MAC)]。整张表一次突发读完。"""
    w = yield from spis.rd(RT + ROUTE, 4 * ROUTES)
    return [(i, w[4 * i], w[4 * i + 1] & 0x3F, (w[4 * i + 1] >> 8) & 0xF,
             (w[4 * i + 2] & 0xFFFF) << 32 | w[4 * i + 3])
            for i in range(ROUTES) if (w[4 * i + 1] >> 15) & 1]


def counters():
    """收到的帧、转发的包、各种原因丢掉的、答过的 ARP，名字照 COUNTERS。"""
    w = yield from spis.rd(RT + COUNT, len(COUNTERS))
    return dict(zip(COUNTERS, w))


def _bits(bits):
    for b in bits:
        yield from spis.wr(GPIO + DOUT, b << MDIO)
        yield from spis.wr(GPIO + DOUT, (b << MDIO) | (1 << MDC))
    yield from spis.wr(GPIO + DOUT, 0)


def _tick():
    yield from spis.wr(GPIO + DOUT, 1 << MDC)
    yield from spis.wr(GPIO + DOUT, 0)


def _head(op: int, phy: int, reg: int) -> list[int]:
    # 802.3 第 22 条：32 个 1 的前导、ST=01、OP、PHYAD、REGAD
    return ([1] * 32 + [0, 1] + [op >> 1, op & 1]
            + [(phy >> i) & 1 for i in range(4, -1, -1)]
            + [(reg >> i) & 1 for i in range(4, -1, -1)])


def mdio_read(phy: int, reg: int):
    """PHY 在 MDC 上升沿之后换位，所以每个上升沿之后读一次。"""
    yield from spis.wr(GPIO + DIR, (1 << MDC) | (1 << MDIO))
    yield from _bits(_head(0b10, phy, reg))
    yield from spis.wr(GPIO + DIR, 1 << MDC)
    yield from _tick()
    v = 0
    for _ in range(16):
        yield from _tick()
        d = yield from spis.rd1(GPIO + DIN)
        v = (v << 1) | ((d >> MDIO) & 1)
    yield from _tick()
    return v


def mdio_write(phy: int, reg: int, val: int):
    yield from spis.wr(GPIO + DIR, (1 << MDC) | (1 << MDIO))
    yield from _bits(_head(0b01, phy, reg) + [1, 0] + [(val >> i) & 1 for i in range(15, -1, -1)])
    yield from spis.wr(GPIO + DIR, 1 << MDC)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="to2610-router 管理")
    link = ap.add_mutually_exclusive_group(required=True)
    link.add_argument("--spidev", help="总线.片选，如 0.0")
    link.add_argument("--ftdi", help="pyftdi 的地址，如 ftdi://ftdi:232h/1")
    ap.add_argument("--hz", type=int, default=2_000_000, help="SCK，不超过芯片主频的八分之一")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ld = sub.add_parser("load", help="整份配置写进去并打开，格式见 sw/net.toml")
    ld.add_argument("file")
    sub.add_parser("ident")
    sub.add_parser("on")
    sub.add_parser("off")
    p = sub.add_parser("port", help="如 port 0 02:00:00:00:01:00 10.0.0.1")
    p.add_argument("n", type=int)
    p.add_argument("mac")
    p.add_argument("ip")
    r = sub.add_parser("route", help="如 route 0 10.0.1.12/32 1 02:00:00:00:00:1c")
    r.add_argument("slot", type=int)
    r.add_argument("to")
    r.add_argument("out", type=int)
    r.add_argument("via")
    d = sub.add_parser("drop")
    d.add_argument("slot", type=int)
    sub.add_parser("ports")
    sub.add_parser("routes")
    sub.add_parser("counters")
    m = sub.add_parser("mdio")
    m.add_argument("phy", type=int)
    m.add_argument("reg", type=int)
    m.add_argument("val", nargs="?", type=lambda x: int(x, 0))
    a = ap.parse_args(argv)
    if a.spidev:
        bus, _, dev = a.spidev.partition(".")
        x = spis.spidev(int(bus), int(dev or 0), a.hz)
    else:
        x = spis.ftdi(a.ftdi, a.hz)
    run = lambda op: spis.run(op, x)  # noqa: E731
    if a.cmd == "load":
        import tomllib
        run(load(tomllib.loads(pathlib.Path(a.file).read_text(encoding="utf-8"))))
    elif a.cmd == "ident":
        print(run(spis.ident()).decode(errors="replace"))
    elif a.cmd in ("on", "off"):
        run(enable(a.cmd == "on"))
    elif a.cmd == "port":
        run(port(a.n, a.mac, a.ip))
    elif a.cmd == "route":
        run(route(a.slot, a.to, a.out, a.via))
    elif a.cmd == "drop":
        run(drop(a.slot))
    elif a.cmd == "ports":
        for n, (m_, a_) in enumerate(run(ports())):
            print(f"{n}  {mac_str(m_)}  {ipaddress.IPv4Address(a_)}")
    elif a.cmd == "routes":
        for slot, prefix, n, out, nh in run(routes()):
            print(f"{slot:2}  {ipaddress.IPv4Address(prefix)}/{n:<2}  口 {out}  {mac_str(nh)}")
    elif a.cmd == "counters":
        for k, v in run(counters()).items():
            print(f"{k:6} {v:10}")
    elif a.cmd == "mdio":
        if a.val is None:
            print(f"0x{run(mdio_read(a.phy, a.reg)):04x}")
        else:
            run(mdio_write(a.phy, a.reg, a.val))
    return 0


if __name__ == "__main__":
    sys.exit(main())
