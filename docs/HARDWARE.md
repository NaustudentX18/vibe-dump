# Hardware

The canonical physical target is a Raspberry Pi Zero 2 W wearing a
Waveshare Whisplay HAT, a PiSugar 3 battery HAT, a USB microphone, and
a USB or Bluetooth speaker. This page documents the pin map, the I2C
addresses, and the assembly steps the install script automates.

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
| LCD IRQ line | GPIO25 | falling-edge interrupt from the ST7789 |
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

## Microphone

Any ALSA-visible input works. The recommended choices are:

- **USB microphone** — plug-and-play, no `alsactl` config required.
  Listed as `hw:0,0` once enumerated.
- **3.5 mm headset / lav mic + USB audio dongle** — the Pi Zero 2 W
  has no onboard audio jack, so a $3 USB sound card is the cheapest
  path. May need a one-time `alsamixer` F-volume bump on Pi OS
  Bookworm.

The audio-capture bridge uses `arecord` from `alsa-utils` and writes
WAVs into `$VIBEDUMP_PTT_DIR` (default `/tmp`).

## Speaker

Two options:

- **USB or Bluetooth speaker** — the Pi Zero 2 W has no onboard audio
  jack, so a small USB speaker (or any A2DP Bluetooth speaker paired
  via `bluetoothctl`) is the path. `aplay`-compatible, no extra
  wiring. Quality is fine for the listener's short follow-up prompts.
- **Whisplay built-in piezo path** — the HAT does not include a
  speaker, but the LCD's backlight driver exposes a PWM output some
  builders repurpose. Treat as optional and project-specific.

## Assembly photos

| | |
|---|---|
| ![HAT stacked on Pi](docs/screenshots/assembly-1.jpg) | ![PiSugar 3 attached](docs/screenshots/assembly-2.jpg) |
| ![USB mic and speaker](docs/screenshots/assembly-3.jpg) | ![Power and final build](docs/screenshots/assembly-4.jpg) |

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
