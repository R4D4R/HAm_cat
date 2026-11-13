"""Rig control protocols (Hamlib-compatible) with no Home Assistant imports.

Hamlib's C library opens serial devices itself, so it cannot open the
``esphome-hass://`` serial-proxy ports that a Home Assistant Connect AUX-2
exposes. This module re-implements the read side of Hamlib's most common
backends in Python on top of ``serialx`` (which *can* open those ports), and
also talks to a real Hamlib ``rigctld`` over TCP for every other radio.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class RigState:
    """What we read from the radio on each poll."""

    frequency_hz: int | None = None
    mode: str | None = None
    passband_hz: int | None = None

    @property
    def band(self) -> str | None:
        return band_for_frequency(self.frequency_hz)


class RigError(Exception):
    """Communication with the radio failed."""


# Amateur bands (ITU Region 2 / US edges). Used for the "band" sensor, which is
# handy for automations such as antenna or amplifier band switching.
BANDS: tuple[tuple[str, int, int], ...] = (
    ("2200m", 135_700, 137_800),
    ("630m", 472_000, 479_000),
    ("160m", 1_800_000, 2_000_000),
    ("80m", 3_500_000, 4_000_000),
    ("60m", 5_330_000, 5_410_000),
    ("40m", 7_000_000, 7_300_000),
    ("30m", 10_100_000, 10_150_000),
    ("20m", 14_000_000, 14_350_000),
    ("17m", 18_068_000, 18_168_000),
    ("15m", 21_000_000, 21_450_000),
    ("12m", 24_890_000, 24_990_000),
    ("10m", 28_000_000, 29_700_000),
    ("6m", 50_000_000, 54_000_000),
    ("2m", 144_000_000, 148_000_000),
    ("1.25m", 222_000_000, 225_000_000),
    ("70cm", 420_000_000, 450_000_000),
    ("33cm", 902_000_000, 928_000_000),
    ("23cm", 1_240_000_000, 1_300_000_000),
)
BAND_GENERAL = "GEN"


def band_for_frequency(freq_hz: int | None) -> str | None:
    if freq_hz is None:
        return None
    for name, low, high in BANDS:
        if low <= freq_hz <= high:
            return name
    return BAND_GENERAL


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------


class Transport:
    """Byte stream to the radio (serial port, serial proxy or TCP)."""

    def __init__(self) -> None:
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    @property
    def is_open(self) -> bool:
        return self._writer is not None

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        raise NotImplementedError

    async def open(self) -> None:
        if self._writer is None:
            self._reader, self._writer = await self._connect()

    async def close(self) -> None:
        writer, self._reader, self._writer = self._writer, None, None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001 - closing a dead link must not raise
                pass

    def discard_input(self) -> None:
        """Drop anything already buffered (stale replies, CI-V transceive)."""
        reader = self._reader
        if reader is not None:
            buf = getattr(reader, "_buffer", None)
            if isinstance(buf, bytearray):
                buf.clear()

    async def write(self, data: bytes) -> None:
        if self._writer is None:
            raise RigError("Not connected")
        self._writer.write(data)
        await self._writer.drain()

    async def read_until(self, sep: bytes, timeout: float) -> bytes:
        if self._reader is None:
            raise RigError("Not connected")
        try:
            async with asyncio.timeout(timeout):
                return await self._reader.readuntil(sep)
        except TimeoutError as err:
            raise RigError("Timed out waiting for the radio") from err
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as err:
            raise RigError(f"Bad reply from the radio: {err}") from err

    async def read_exactly(self, n: int, timeout: float) -> bytes:
        if self._reader is None:
            raise RigError("Not connected")
        try:
            async with asyncio.timeout(timeout):
                return await self._reader.readexactly(n)
        except TimeoutError as err:
            raise RigError("Timed out waiting for the radio") from err
        except asyncio.IncompleteReadError as err:
            raise RigError("Connection closed by the radio") from err


class SerialTransport(Transport):
    """Serial port via serialx: /dev/tty*, socket://, or an AUX-2 esphome-hass:// URL."""

    def __init__(self, url: str, baudrate: int, stopbits: int = 1) -> None:
        super().__init__()
        self.url = url
        self.baudrate = baudrate
        self.stopbits = stopbits

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        import serialx  # imported lazily so the protocol code is testable without it

        kwargs: dict[str, Any] = {"url": self.url, "baudrate": self.baudrate}
        if self.stopbits == 2:
            kwargs["stopbits"] = serialx.StopBits.TWO
        try:
            return await serialx.open_serial_connection(**kwargs)
        except Exception as err:  # serialx raises OSError/SerialException subclasses
            raise RigError(f"Could not open {self.url}: {err}") from err


class TcpTransport(Transport):
    """Plain TCP, used for Hamlib rigctld."""

    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        self.host = host
        self.port = port

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            async with asyncio.timeout(5):
                return await asyncio.open_connection(self.host, self.port)
        except (OSError, TimeoutError) as err:
            raise RigError(f"Could not connect to rigctld at {self.host}:{self.port}: {err}") from err


# ---------------------------------------------------------------------------
# Protocol drivers (named after the Hamlib backends they mirror)
# ---------------------------------------------------------------------------

READ_TIMEOUT = 1.0


class Driver:
    """Reads state from a radio over a transport."""

    async def read_state(self, transport: Transport) -> RigState:
        raise NotImplementedError


# --- Kenwood / Elecraft / Yaesu "newcat" ASCII CAT -------------------------

KENWOOD_MODES = {
    "1": "LSB", "2": "USB", "3": "CW", "4": "FM", "5": "AM",
    "6": "RTTY", "7": "CWR", "9": "RTTYR",
}

# Yaesu newcat (FT-991, FTDX10/101, FT-710, FT-891, FT-450...): MD0<x>;
YAESU_NEWCAT_MODES = {
    "1": "LSB", "2": "USB", "3": "CW", "4": "FM", "5": "AM",
    "6": "RTTY", "7": "CWR", "8": "PKTLSB", "9": "RTTYR",
    "A": "PKTFM", "B": "FMN", "C": "PKTUSB", "D": "AMN",
    "E": "PSK", "F": "PKTFMN",
}


class KenwoodDriver(Driver):
    """Kenwood TS-480/590/890/990/2000, Elecraft K2/K3/K4/KX2/KX3, FlexRadio CAT."""

    modes = KENWOOD_MODES

    async def _query(self, transport: Transport, cmd: str) -> str:
        await transport.write(f"{cmd};".encode())
        # Skip unrelated frames (e.g. auto-information "AI" broadcasts) and echoes.
        for _ in range(8):
            raw = await transport.read_until(b";", READ_TIMEOUT)
            frame = raw.decode("ascii", errors="ignore").strip().rstrip(";")
            frame = frame.lstrip("\x00\r\n ")
            if frame == "?":
                raise RigError(f"Radio rejected command {cmd}")
            if frame.startswith(cmd) and len(frame) > len(cmd):
                return frame[len(cmd):]
        raise RigError(f"No reply to {cmd}")

    def _parse_mode(self, payload: str) -> str | None:
        return self.modes.get(payload[-1:], f"UNKNOWN({payload})") if payload else None

    async def read_state(self, transport: Transport) -> RigState:
        transport.discard_input()
        freq_payload = await self._query(transport, "FA")
        if not freq_payload.isdigit():
            raise RigError(f"Unexpected frequency reply: FA{freq_payload}")
        state = RigState(frequency_hz=int(freq_payload))
        try:
            state.mode = self._parse_mode(await self._query(transport, "MD"))
        except RigError:
            state.mode = None  # frequency is the important part
        return state


class YaesuNewcatDriver(KenwoodDriver):
    """Yaesu "newcat" radios (FT-991/A, FT-891, FT-710, FTDX10, FTDX101, FT-450...)."""

    modes = YAESU_NEWCAT_MODES

    async def read_state(self, transport: Transport) -> RigState:
        transport.discard_input()
        freq_payload = await self._query(transport, "FA")
        if not freq_payload.isdigit():
            raise RigError(f"Unexpected frequency reply: FA{freq_payload}")
        state = RigState(frequency_hz=int(freq_payload))
        try:
            # Request "MD0;" -> reply "MD02;" (main receiver, mode 2 = USB)
            payload = await self._query(transport, "MD0")
            state.mode = self.modes.get(payload[-1:], f"UNKNOWN({payload})")
        except RigError:
            state.mode = None
        return state


# --- Legacy Yaesu 5-byte binary CAT (FT-817/818/857/897) -------------------

YAESU_LEGACY_MODES = {
    0x00: "LSB", 0x01: "USB", 0x02: "CW", 0x03: "CWR", 0x04: "AM",
    0x06: "WFM", 0x08: "FM", 0x0A: "PKTUSB", 0x0C: "PKTFM",
    0x82: "CW", 0x83: "CWR", 0x8A: "PKTUSB",
}


def decode_bcd_be(data: bytes) -> int:
    """Packed BCD, most significant byte first."""
    value = 0
    for byte in data:
        hi, lo = byte >> 4, byte & 0x0F
        if hi > 9 or lo > 9:
            raise RigError(f"Invalid BCD byte 0x{byte:02X}")
        value = value * 100 + hi * 10 + lo
    return value


class YaesuLegacyDriver(Driver):
    """FT-817/818/857/897 'read frequency and mode' (opcode 0x03)."""

    async def read_state(self, transport: Transport) -> RigState:
        transport.discard_input()
        await transport.write(b"\x00\x00\x00\x00\x03")
        reply = await transport.read_exactly(5, READ_TIMEOUT)
        freq = decode_bcd_be(reply[:4]) * 10  # radio reports in 10 Hz units
        return RigState(frequency_hz=freq, mode=YAESU_LEGACY_MODES.get(reply[4], f"UNKNOWN(0x{reply[4]:02X})"))


# --- Icom CI-V --------------------------------------------------------------

CIV_PREAMBLE = b"\xfe\xfe"
CIV_EOM = 0xFD
CIV_CONTROLLER = 0xE0
CIV_NG = 0xFA

ICOM_MODES = {
    0x00: "LSB", 0x01: "USB", 0x02: "AM", 0x03: "CW", 0x04: "RTTY",
    0x05: "FM", 0x06: "WFM", 0x07: "CWR", 0x08: "RTTYR", 0x17: "DV",
}


def decode_bcd_le(data: bytes) -> int:
    """Icom frequency: packed BCD, least significant byte first."""
    return decode_bcd_be(bytes(reversed(data)))


def civ_frame(to_addr: int, cmd: int, data: bytes = b"") -> bytes:
    return CIV_PREAMBLE + bytes([to_addr, CIV_CONTROLLER, cmd]) + data + bytes([CIV_EOM])


class IcomCivDriver(Driver):
    """Icom CI-V radios (IC-7300, IC-705, IC-7610, IC-9700, IC-7100, ...)."""

    def __init__(self, address: int) -> None:
        self.address = address

    async def _query(self, transport: Transport, cmd: int) -> bytes:
        await transport.write(civ_frame(self.address, cmd))
        # CI-V is a shared bus: we see our own echo, and with "CI-V Transceive"
        # on, unsolicited broadcasts too. Keep reading until our reply arrives.
        for _ in range(10):
            raw = await transport.read_until(bytes([CIV_EOM]), READ_TIMEOUT)
            start = raw.rfind(CIV_PREAMBLE)
            if start < 0:
                continue
            body = raw[start + 2 : -1]  # to, from, cmd, data...
            if len(body) < 3:
                continue
            to_addr, from_addr, reply_cmd = body[0], body[1], body[2]
            if from_addr != self.address or to_addr != CIV_CONTROLLER:
                continue  # our echo, or a transceive broadcast to 0x00
            if reply_cmd == CIV_NG:
                raise RigError(f"Radio rejected CI-V command 0x{cmd:02X}")
            if reply_cmd == cmd:
                return body[3:]
        raise RigError(f"No CI-V reply to 0x{cmd:02X} (check the CI-V address)")

    async def read_state(self, transport: Transport) -> RigState:
        transport.discard_input()
        freq_data = await self._query(transport, 0x03)
        if len(freq_data) < 4:
            raise RigError("Short CI-V frequency reply")
        state = RigState(frequency_hz=decode_bcd_le(freq_data))
        try:
            mode_data = await self._query(transport, 0x04)
            if mode_data:
                state.mode = ICOM_MODES.get(mode_data[0], f"UNKNOWN(0x{mode_data[0]:02X})")
        except RigError:
            state.mode = None
        return state


# --- Hamlib rigctld (TCP) ---------------------------------------------------


class RigctldDriver(Driver):
    """A real Hamlib rigctld: supports every radio Hamlib does."""

    async def _line(self, transport: Transport) -> str:
        raw = await transport.read_until(b"\n", 3.0)
        line = raw.decode(errors="ignore").strip()
        if line.startswith("RPRT"):
            raise RigError(f"rigctld error: {line}")
        return line

    async def read_state(self, transport: Transport) -> RigState:
        transport.discard_input()
        await transport.write(b"f\n")
        line = await self._line(transport)
        try:
            state = RigState(frequency_hz=int(float(line)))
        except ValueError as err:
            raise RigError(f"Unexpected rigctld frequency reply: {line!r}") from err
        try:
            await transport.write(b"m\n")
            state.mode = await self._line(transport) or None
            passband = await self._line(transport)
            state.passband_hz = int(passband) if passband.lstrip("-").isdigit() else None
        except RigError:
            state.mode = None
        return state


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class RigClient:
    """Keeps one connection open, serialises polls, reconnects after errors."""

    def __init__(self, transport: Transport, driver: Driver) -> None:
        self.transport = transport
        self.driver = driver
        self._lock = asyncio.Lock()

    async def async_read_state(self) -> RigState:
        async with self._lock:
            try:
                await self.transport.open()
                return await self.driver.read_state(self.transport)
            except RigError:
                await self.transport.close()
                raise
            except (OSError, ConnectionError) as err:
                await self.transport.close()
                raise RigError(str(err)) from err

    async def async_close(self) -> None:
        async with self._lock:
            await self.transport.close()
