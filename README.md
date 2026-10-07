# to2610-router

A four-port 100 Mbps IPv4 router chip for the ECOS 2610 shuttle. It has no core: the hardware checks, routes and rewrites every packet, and an SPI host writes the port addresses and the route table.

![maturity](https://img.shields.io/badge/maturity-simulated-yellow) ![license](https://img.shields.io/badge/license-MIT%20OR%20Apache--2.0%20OR%20MulanPSL--2.0-blue)

Assembled by [`xirang`](https://github.com/Tape-Out/xirang) from [`erouter`](https://github.com/Tape-Out/erouter) (four ports, 16 routes, a 128-byte frame buffer per port), [`spis`](https://github.com/Tape-Out/spis) (SPI slave that masters the on-chip bus) and [`gpio`](https://github.com/Tape-Out/gpio) (six pins). There is no RTL of its own.

It pairs with [`to2610-switch`](https://github.com/Tape-Out/to2610-switch): one switch per subnet, one switch port wired to a router port. The payload bits of the RMII ports, the SPI management port and the GPIO pins are laid out the same way on both chips.

The board wiring (four RMII PHYs with 8P8C jacks, the SPI management header, MDIO over GPIO), the chip tests and the limits are in [`docs/流片说明.md`](docs/流片说明.md); the tape-out report is generated from that file.

## Management

The router is closed after reset, with empty tables. It does not send ARP requests, so the next hop of a route is a MAC address, and a directly attached host takes a /32 route of its own.

```console
$ python3 sw/router.py --ftdi ftdi://ftdi:232h/1 load sw/net.toml     # ports, routes, then open
$ python3 sw/router.py --spidev 0.0 routes                             # valid routes
$ python3 sw/router.py --spidev 0.0 route 4 0.0.0.0/0 3 02:00:00:00:00:13
$ python3 sw/router.py --spidev 0.0 counters                           # received, forwarded, dropped by reason
$ python3 sw/router.py --spidev 0.0 mdio 1 2                           # read register 2 of the PHY at address 1
```

`sw/spis.py` is the SPI protocol and `sw/router.py` the register map and the operations. The chip tests drive the chip through these same two files, and the joint simulation loads `sw/net.toml` itself.

Hosts behind the router set their gateway to the address of the port they are on, and their MTU to 114.

## Testing and tape-out

```console
$ ran test to2610-router                  # the chip tests, then two switches and the router pinging across subnets
$ ran asic to2610-router                  # to2610_router.v, ecc at 50 MHz, report.json
$ ran asic to2610-router --no-run         # only the Verilog file and ecc.toml
```

## License

任选其一：

- [MIT](LICENSE-MIT)
- [Apache 2.0](LICENSE-APACHE)
- [木兰宽松许可证 第2版](LICENSE-MULAN)

`SPDX-License-Identifier: MIT OR Apache-2.0 OR MulanPSL-2.0`

除非另行说明，你提交的贡献按上述三者同时授权，不附加其他条件。
