# Hamlib-based CAT for Home Assistant

Shows your ham radio's **frequency**, **mode** and **band** as Home Assistant sensors. It reads them over a **Home Assistant Connect AUX-2** RS-232 port (an ESPHome serial proxy), a USB CAT cable, a `socket://` serial server, or a Hamlib `rigctld`.  This project borrows heavily from hamlib.

| Protocol option | Radios |
|---|---|
| Kenwood / Elecraft / Flex | TS-480, TS-590, TS-890, TS-990, TS-2000, K3, K4, KX2, KX3, FlexRadio CAT |
| Yaesu CAT | FT-991/A, FT-891, FT-710, FTDX10, FTDX101, FT-450 |
| Yaesu legacy binary | FT-817, FT-818, FT-857, FT-897 (8N2) |
| Icom CI-V | IC-7300, IC-705, IC-7610, IC-9700, IC-7100, IC-905… (set the CI-V address) |
| rigctld | Anything Hamlib supports |

## Requirements
- Home Assistant 2026.9 or later (needed for the AUX-2 and the serial-port picker).
- An AUX-2 that has been added through the ESPHome integration.

## Install
1. Copy `custom_components/HAm_cat` into `/config/custom_components/`, or add this repo to HACS as a custom repository.
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Hamlib CAT**.
4. Choose **Serial port**, then pick the AUX-2 port from the **Serial proxies** list. Select your radio's protocol and baud rate.

You get three sensors: `sensor.<name>_frequency` (MHz, with `frequency_hz` as an attribute), `sensor.<name>_mode`, and `sensor.<name>_band` (`20m`, `2m`, … or `GEN` outside the ham bands). You can change the poll interval (default 1 s) under **Configure**.

## Wiring (read before plugging in)
- Each AUX-2 RS-232 port is a **3.5 mm jack carrying TX, RX and ground only**, at ±5 V. Never plug it into an audio jack.
- **Radios with a DB-9 RS-232 port** (TS-590, TS-2000, K3, FTDX10/101, FT-991 RS-232 port): use a 3.5 mm-to-DB-9 cable. Swap TX and RX if you get no reply. Also turn off RTS/CTS handshaking in the radio's menu, because the jack has no flow-control lines.
- **Icom CI-V jacks** and **FT-817/857/897 ACC/CAT ports** are TTL level, not RS-232. They need a level converter (CT-17-style for Icom, a CAT RS-232 interface for Yaesu) between the radio and the AUX-2.
- Radios whose CAT port is USB only (for example IC-7300 or FT-991A over USB) won't work on the AUX-2's RS-232 jack. Use the radio's rear CI-V/RS-232 port, or run `rigctld` on a computer connected to the radio's USB port.
- Make the radio's CAT baud rate match the baud rate you select. For Icom, also turn **CI-V USB Echo Back** off and set **CI-V Transceive** as you like (both on and off are handled).

## Using it
```yaml
automation:
  - alias: "Switch to the 40m antenna"
    triggers:
      - trigger: state
        entity_id: sensor.ham_radio_band
        to: "40m"
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.antenna_relay_40m
```

## Development
`rig.py` has no Home Assistant imports. Run its tests, which use simulated Kenwood, Yaesu, Icom and rigctld radios, with `pytest tests`.
