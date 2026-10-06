"""spis 的主机端：经 SPI 读写芯片的片上总线。

协议照 `Tape-Out/spis` 的 README：模式 0，高位先出，字按大端上线。

每个操作都是生成器：交出一次传输要发的字节，收回同样长的应答。怎么发由驱动定——板上是
spidev 或 USB 转 SPI（`run`），仿真里是测试台（它 await 时钟）。两边跑的是同一份代码，
仿真测过的就是上板用的。
"""


def wr(addr: int, *words: int):
    yield bytes([0x02]) + addr.to_bytes(4, "big") + b"".join(
        (w & 0xFFFFFFFF).to_bytes(4, "big") for w in words)


def rd(addr: int, n: int = 1):
    """一次最多连读 256 个字；只读这么多，不会多碰下一个寄存器。"""
    out = []
    while n > 0:
        k = min(n, 256)
        if k == 1:
            rx = yield bytes([0x03]) + addr.to_bytes(4, "big") + bytes(5)
            body = rx[6:]
        else:
            rx = yield bytes([0x0B]) + addr.to_bytes(4, "big") + bytes([k - 1]) + bytes(1 + 4 * k)
            body = rx[7:]
        out += [int.from_bytes(body[4 * i:4 * i + 4], "big") for i in range(k)]
        addr += 4 * k
        n -= k
    return out


def rd1(addr: int):
    return (yield from rd(addr))[0]


def status():
    """bit0：总线回过错；bit1：主机快过总线。读完清零。"""
    return (yield bytes([0x05, 0]))[1]


def ident():
    return (yield bytes([0x9F, 0, 0, 0, 0]))[1:5]


def run(op, xfer):
    """同步驱动：`xfer(tx) -> rx` 拉低 cs_n、全双工收发、拉高 cs_n。"""
    try:
        tx = next(op)
        while True:
            tx = op.send(xfer(tx))
    except StopIteration as done:
        return done.value


def spidev(bus: int = 0, dev: int = 0, hz: int = 2_000_000):
    """树莓派一类的 Linux 板子。SCK 不能超过芯片主频的八分之一。"""
    import spidev as sd
    s = sd.SpiDev()
    s.open(bus, dev)
    s.mode = 0
    s.max_speed_hz = hz
    return lambda tx: bytes(s.xfer2(list(tx)))


def ftdi(url: str = "ftdi://ftdi:232h/1", hz: int = 2_000_000):
    """FT232H 一类的 USB 转 SPI。"""
    from pyftdi.spi import SpiController
    c = SpiController()
    c.configure(url)
    port = c.get_port(cs=0, freq=hz, mode=0)
    return lambda tx: bytes(port.exchange(tx, duplex=True))
