# Hardware

The canonical physical target is a Raspberry Pi Zero 2 W wearing a
**PiSugar Whisplay HAT** (LCD, buttons, LED, **WM8960 audio**), and a
**PiSugar 3** battery HAT. The Whisplay HAT includes **dual MEMS
microphones and an onboard speaker** once the
[PiSugar/Whisplay](https://github.com/PiSugar/Whisplay) driver is
installed. USB or Bluetooth audio is an optional upgrade path.

This page documents the pin map, I2C addresses, and install automation.

## Whisplay HAT pin map

The Whisplay HAT exposes a 240×280 ST7789 LCD, a single WS2812 LED,
and four buttons (A/B/C/D). All constants live in
[`vibedump/integrations/whisplay.py`](../vibedump/integrations/whisplay.py)
and are the single source of truth for the rest of the app.

| Function | Bus / pin | Notes |
|----------|-----------|-------|
| LCD SPI bus | SPI0 CE0 (`/dev/spidev0.0`) | `WHISPLAY_SPI_BUS=0`, `WHISPLAY_SPI_DEV=0` |
| LCD SPI clock | 40 MHz | `WHISPLAY_SPI_HZ=40_000_000` |
| LCD resolution | 240 × 280 | `WHISPLAY_WIDTH=240`, `WHISPLAY_HEIGHT=280` |
| LCD D/C line | GPIO13 | data/command select (ST7789) |
| LCD RST line | GPIO7 | hardware reset |
| LCD Y offset | 20 px | Whisplay panel visible area offset |
| LED (WS2812) | 1 pixel, SMbus-controlled MCU | `WHISPLAY_LED_COUNT=1` |
| Button A | GPIO5 | active-low, on-HAT pull-up |
| Button B | GPIO6 | active-low, on-HAT pull-up |
| Button C | GPIO16 | active-low, on-HAT pull-up |
| Button D / primary push-to-talk | GPIO24 | active-low, on-HAT pull-up |

The Whisplay bridge polls all four buttons; consumers should treat
the result as "buttons held at the moment of the call".

## PiSugar 3 (I2C, address `0x57`)

The PiSugar 3 sits on the Pi's primary I2C bus. Constants live in
[`vibedump/integrations/pisugar.py`](../vibedump/integrations/pisugar.py).

| Setting | Value |
|---------|-------|
| I2C bus | `1` |
| I2C address | `0x57` |
| Battery percent register | `0x2A` |
| Voltage (mV) | `0x2B` / `0x2C` (16-bit big-endian) |
| Current (mA) | `0x33` / `0x34` (signed 16-bit) |
| Temperature | `0x04` (firmware-dependent) |
| Default poll interval | `30 s` |
| Low-battery threshold | `20 %` |
| Critical threshold | `10 %` |

If the kernel cannot see `/dev/i2c-1`, the real bridge raises
`PiSugarNotAvailable` and the app falls back to
`FakePiSugarBridge`.

## Audio (WM8960)

Install the official Whisplay driver before expecting audio devices:

```bash
git clone https://github.com/PiSugar/Whisplay.git --depth 1
cd Whisplay && sudo bash install_driver.sh && sudo reboot
```

| Component | Details |
|-----------|---------|
| Codec | WM8960 (I2C `0x1a`, I2S) |
| Capture | Dual MEMS mics — default input via `arecord` |
| Playback | Onboard speaker + 3.5 mm line-out |
| Driver repo | [PiSugar/Whisplay](https://github.com/PiSugar/Whisplay) |

Vibe-Dump uses `arecord` / `aplay` via
[`audio_capture.py`](../vibedump/integrations/audio_capture.py) and
[`audio_playback.py`](../vibedump/integrations/audio_playback.py).
Set optional ALSA devices in `.env`:

- `VIBEDUMP_ALSA_CAPTURE_DEVICE`
- `VIBEDUMP_ALSA_PLAYBACK_DEVICE`

WAVs are staged in `$VIBEDUMP_PTT_DIR` (default `/tmp`).

**Upgrade path:** USB or Bluetooth speaker/mic if you want louder TTS or
a different input — not required for the default Whisplay build.

## Screenshots

| Dumpi idle | Dumpi listening |
|---|---|
| ![Dumpi idle](screenshots/dumpi-idle.png) | ![Dumpi listening](screenshots/dumpi-listening.png) |

## Power budget

A 5 V / 2.5 A micro-USB supply is recommended. Worst-case draws
measured on a Pi Zero 2 W with the HATs stacked, Whisper `tiny`
running, and Wi-Fi active:

| Load | Typical draw |
|------|--------------|
| Pi Zero 2 W idle (no HAT) | ~0.15 A |
| Pi Zero 2 W + Whisplay (LCD + LED) | ~0.30 A |
| Pi Zero 2 W + Whisplay + PiSugar (charging) | ~0.80 A |
| Pi Zero 2 W + Whisplay + PiSugar + Whisper (CPU + I/O) | ~1.10 A |
| + Wi-Fi transmit bursts | peaks at ~1.30 A |

A 2.5 A supply gives ~80 % headroom for inrush. The PiSugar 3 acts
as a small UPS and will power the Pi through brown-outs of a few
seconds; do not rely on it for sustained unplugged runtime at peak
draw.

## What `install_whisplay_prereqs.sh` does

`scripts/install_whisplay_prereqs.sh` is idempotent and safe to
re-run on a fully configured system. It:

1. Installs `python3-spidev`, `python3-smbus`, `python3-rpi.gpio`,
   and `python3-pil` via apt.
2. Enables I2C and SPI in `raspi-config` (no-op if already enabled).
3. Adds the current user to the `spi` and `gpio` groups (log out
   and back in for the change to take effect).
4. Loads `spi-dev` and `i2c-dev` now and on boot via `/etc/modules`.
5. Installs `spidev`, `smbus2`, and `Pillow` via pip if the apt
   versions are not importable.
6. Probes `/dev/spidev0.0` to confirm the HAT is stacked; a missing
   node usually means a reboot is required.

Run it once with `sudo` and you are ready to plug in the HAT.
