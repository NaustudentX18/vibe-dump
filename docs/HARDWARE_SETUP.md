# Hardware setup

Real Whisplay + PiSugar setup for Raspberry Pi Zero 2 W.

## 1. Stack the HATs

Pi Zero 2 W → PiSugar Whisplay HAT → PiSugar 3 battery HAT (see [HARDWARE.md](HARDWARE.md) for pin map).

## 2. Install drivers

### Whisplay (LCD, buttons, LED, **WM8960 audio**)

Official driver repository: **[PiSugar/Whisplay](https://github.com/PiSugar/Whisplay)**

```bash
git clone https://github.com/PiSugar/Whisplay.git --depth 1
cd Whisplay
sudo bash install_driver.sh
sudo reboot
```

Or use the Vibe-Dump helper (SPI/I2C + optional audio driver clone):

```bash
sudo ./scripts/install_whisplay_prereqs.sh
sudo reboot
```

Verify audio after reboot:

```bash
arecord -l    # expect wm8960 capture
aplay -l      # expect wm8960 playback
speaker-test -t wav -c 2 -l 1
```

### Vibe-Dump app

```bash
curl -fsSL https://raw.githubusercontent.com/NaustudentX18/vibe-dump/main/scripts/install.sh | bash
# or manual: pip install -e ".[web,all]" && ./scripts/run_dev.sh
```

### systemd (Pi production)

```bash
sudo ./scripts/install_systemd.sh install
sudo systemctl start vibedump vibedump-whisplay vibedump-pisugar vibedump-agent
```

## 3. Configure ALSA (if needed)

If multiple sound cards are present, set in `.env`:

```bash
VIBEDUMP_ALSA_CAPTURE_DEVICE=plughw:CARD=wm8960soundcard,DEV=0
VIBEDUMP_ALSA_PLAYBACK_DEVICE=plughw:CARD=wm8960soundcard,DEV=0
```

## 4. Verify

```bash
curl http://127.0.0.1:8080/api/health
curl http://127.0.0.1:8080/api/hardware/pisugar
./scripts/hardware_smoke.sh
```

Full pin map and power budget: [HARDWARE.md](HARDWARE.md).
