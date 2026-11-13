"""Protocol tests against simulated radios (no Home Assistant needed)."""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys

import pytest

# Load rig.py directly so the HA-dependent package __init__ isn't imported.
_path = pathlib.Path(__file__).parents[1] / "custom_components" / "HAm_cat" / "rig.py"
_spec = importlib.util.spec_from_file_location("rig", _path)
rig = importlib.util.module_from_spec(_spec)
sys.modules["rig"] = rig
_spec.loader.exec_module(rig)


class FakeRadio(rig.Transport):
    """A transport whose 'radio' answers each write via a responder function."""

    def __init__(self, responder):
        super().__init__()
        self.responder = responder
        self.opens = 0
        self.written: list[bytes] = []

    async def _connect(self):
        self.opens += 1
        reader = asyncio.StreamReader()
        radio = self

        class W:
            def write(self, data):
                radio.written.append(data)
                reply = radio.responder(data)
                if reply:
                    reader.feed_data(reply)

            async def drain(self):
                pass

            def close(self):
                pass

            async def wait_closed(self):
                pass

        return reader, W()


def run(coro):
    return asyncio.run(coro)


# --- Kenwood / Elecraft --------------------------------------------------


def kenwood(freq=14_074_000, mode="2", noise=b""):
    def respond(cmd: bytes):
        if cmd == b"FA;":
            return noise + f"FA{freq:011d};".encode()
        if cmd == b"MD;":
            return f"MD{mode};".encode()
        return b"?;"
    return respond


def test_kenwood_freq_and_mode():
    client = rig.RigClient(FakeRadio(kenwood()), rig.KenwoodDriver())
    state = run(client.async_read_state())
    assert state.frequency_hz == 14_074_000
    assert state.mode == "USB"
    assert state.band == "20m"


def test_kenwood_skips_auto_info_frames():
    noise = b"IF00007074000     +00000000002000000;"
    client = rig.RigClient(FakeRadio(kenwood(7_074_000, "1", noise)), rig.KenwoodDriver())
    state = run(client.async_read_state())
    assert state.frequency_hz == 7_074_000
    assert state.mode == "LSB"
    assert state.band == "40m"


def test_kenwood_rejected_command():
    client = rig.RigClient(FakeRadio(lambda c: b"?;"), rig.KenwoodDriver())
    with pytest.raises(rig.RigError):
        run(client.async_read_state())


# --- Yaesu newcat --------------------------------------------------------


def test_yaesu_newcat():
    def respond(cmd):
        return {b"FA;": b"FA050313000;", b"MD0;": b"MD0C;"}.get(cmd, b"?;")
    client = rig.RigClient(FakeRadio(respond), rig.YaesuNewcatDriver())
    state = run(client.async_read_state())
    assert state.frequency_hz == 50_313_000
    assert state.mode == "PKTUSB"
    assert state.band == "6m"


def test_mode_failure_still_reports_frequency():
    def respond(cmd):
        return b"FA014074000;" if cmd == b"FA;" else b"?;"
    state = run(rig.RigClient(FakeRadio(respond), rig.YaesuNewcatDriver()).async_read_state())
    assert state.frequency_hz == 14_074_000
    assert state.mode is None


# --- Yaesu legacy (FT-817) -----------------------------------------------


def test_yaesu_legacy_ft817():
    # 145.500.00 MHz in 10 Hz units -> 14550000 -> BCD 14 55 00 00, mode FM (0x08)
    def respond(cmd):
        assert cmd == b"\x00\x00\x00\x00\x03"
        return bytes([0x14, 0x55, 0x00, 0x00, 0x08])
    state = run(rig.RigClient(FakeRadio(respond), rig.YaesuLegacyDriver()).async_read_state())
    assert state.frequency_hz == 145_500_000
    assert state.mode == "FM"
    assert state.band == "2m"


# --- Icom CI-V -----------------------------------------------------------


def icom(addr=0x94, freq_bcd=bytes([0x00, 0x40, 0x07, 0x14, 0x00]), mode=0x01, echo=True, transceive=b""):
    def respond(cmd: bytes):
        out = cmd if echo else b""  # single-wire CI-V bus echoes our frame
        out += transceive
        if cmd == rig.civ_frame(addr, 0x03):
            out += b"\xfe\xfe\xe0" + bytes([addr, 0x03]) + freq_bcd + b"\xfd"
        elif cmd == rig.civ_frame(addr, 0x04):
            out += b"\xfe\xfe\xe0" + bytes([addr, 0x04, mode, 0x01]) + b"\xfd"
        return out
    return respond


def test_icom_freq_mode_with_echo():
    state = run(rig.RigClient(FakeRadio(icom()), rig.IcomCivDriver(0x94)).async_read_state())
    assert state.frequency_hz == 14_074_000
    assert state.mode == "USB"


def test_icom_ignores_transceive_broadcasts():
    broadcast = b"\xfe\xfe\x00\x94\x00\x00\x00\x07\x07\x00\xfd"  # someone else tuning
    state = run(
        rig.RigClient(FakeRadio(icom(transceive=broadcast)), rig.IcomCivDriver(0x94)).async_read_state()
    )
    assert state.frequency_hz == 14_074_000


def test_icom_6_byte_frequency_ic905():
    # 10.368100000 GHz, LSB-first BCD: 00 00 10 68 03 01
    freq = bytes([0x00, 0x00, 0x10, 0x68, 0x03, 0x01])
    state = run(rig.RigClient(FakeRadio(icom(0xAC, freq)), rig.IcomCivDriver(0xAC)).async_read_state())
    assert state.frequency_hz == 10_368_100_000


def test_icom_wrong_address_times_out():
    client = rig.RigClient(FakeRadio(icom(addr=0x94)), rig.IcomCivDriver(0xA4))
    rig.READ_TIMEOUT = 0.05
    try:
        with pytest.raises(rig.RigError):
            run(client.async_read_state())
    finally:
        rig.READ_TIMEOUT = 1.0


# --- rigctld --------------------------------------------------------------


def test_rigctld():
    def respond(cmd):
        return {b"f\n": b"7074000\n", b"m\n": b"PKTUSB\n3000\n"}[cmd]
    state = run(rig.RigClient(FakeRadio(respond), rig.RigctldDriver()).async_read_state())
    assert (state.frequency_hz, state.mode, state.passband_hz) == (7_074_000, "PKTUSB", 3000)


def test_rigctld_error():
    client = rig.RigClient(FakeRadio(lambda c: b"RPRT -11\n"), rig.RigctldDriver())
    with pytest.raises(rig.RigError, match="RPRT -11"):
        run(client.async_read_state())


# --- Reconnect ------------------------------------------------------------


def test_reconnects_after_error():
    calls = {"n": 0}

    def flaky(cmd):
        calls["n"] += 1
        if calls["n"] == 1:
            return b""  # radio silent -> timeout
        return kenwood()(cmd)

    radio = FakeRadio(flaky)
    client = rig.RigClient(radio, rig.KenwoodDriver())
    rig.READ_TIMEOUT = 0.05

    async def go():
        with pytest.raises(rig.RigError):
            await client.async_read_state()
        assert not radio.is_open
        return await client.async_read_state()

    try:
        state = run(go())
    finally:
        rig.READ_TIMEOUT = 1.0
    assert state.frequency_hz == 14_074_000
    assert radio.opens == 2


def test_bands():
    assert rig.band_for_frequency(3_573_000) == "80m"
    assert rig.band_for_frequency(10_136_000) == "30m"
    assert rig.band_for_frequency(446_000_000) == "70cm"
    assert rig.band_for_frequency(9_600_000) == "GEN"
    assert rig.band_for_frequency(None) is None
